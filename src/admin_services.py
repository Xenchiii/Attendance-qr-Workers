import secrets
from collections import Counter
from datetime import datetime, timedelta, timezone

import config
import dbx as db
import security
import services

USER_SORTS = {"name": "u.last_name", "id": "u.employee_no", "email": "u.email", "role": "u.role", "status": "u.status", "joined": "u.created_at"}
ROOM_STATUSES = ("AVAILABLE", "MAINTENANCE", "INACTIVE")
TEMP_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
USER_PER_PAGE = 10
FULL_NAME = "(u.first_name || ' ' || u.last_name)"


def initials(first, last):
    return ((first or "?")[:1] + (last or "")[:1]).upper()


def decorate_user(row):
    row["initials"] = initials(row["first_name"], row["last_name"])
    row["full_name"] = row["first_name"] + " " + row["last_name"]
    return row


async def users_page(f, page, per=USER_PER_PAGE):
    where = ["u.role <> 'admin'"]
    args = []
    if f.get("role") in ("teacher", "staff"):
        where.append("u.role = ?")
        args.append(f["role"])
    if f.get("status") in ("ACTIVE", "INACTIVE", "SUSPENDED"):
        where.append("u.status = ?")
        args.append(f["status"])
    if f.get("q"):
        like = "%" + f["q"] + "%"
        where.append("(" + FULL_NAME + " LIKE ? OR u.email LIKE ? OR u.employee_no LIKE ?)")
        args.extend([like, like, like])
    clause = " AND ".join(where)
    total = (await db.one("SELECT COUNT(*) AS n FROM users u WHERE " + clause, *args))["n"]
    pager = services.paginate(total, page, per)
    column = USER_SORTS.get(f.get("sort"), "u.status")
    direction = "DESC" if f.get("dir") == "desc" else "ASC"
    rows = await db.query(
        "SELECT u.id, u.role, u.employee_no, u.first_name, u.last_name, u.email, u.status, u.created_at, u.last_login, "
        "((SELECT COUNT(*) FROM attendance a WHERE a.teacher_id = u.id OR a.scanned_by = u.id) "
        "+ (SELECT COUNT(*) FROM qr_sessions q WHERE q.teacher_id = u.id)) AS refs "
        "FROM users u WHERE " + clause + " ORDER BY " + column + " " + direction + ", u.last_name, u.id LIMIT ? OFFSET ?",
        *args, per, pager["offset"],
    )
    for row in rows:
        decorate_user(row)
        row["last_login_text"] = services.local_time(row["last_login"]) if row["last_login"] else "Never"
    return rows, pager


async def pending_users():
    rows = await db.query(
        "SELECT id, role, employee_no, first_name, last_name, email, status FROM users WHERE status = 'INACTIVE' AND role <> 'admin' ORDER BY id"
    )
    return [decorate_user(row) for row in rows]


async def pending_count():
    return (await db.one("SELECT COUNT(*) AS n FROM users WHERE status = 'INACTIVE' AND role <> 'admin'"))["n"]


async def create_user(role, first, last, number, email, password):
    if role not in ("teacher", "staff"):
        return False, "Choose teacher or staff"
    return await services.register(role, first, last, number, email, password, active=True)


async def target(user_id):
    return await db.one("SELECT id, role, first_name, last_name FROM users WHERE id = ? AND role <> 'admin'", user_id)


async def reset_password(user_id):
    user = await target(user_id)
    if user is None:
        return False, "Account not found"
    temp = "".join(secrets.choice(TEMP_ALPHABET) for _ in range(10))
    await db.batch([
        ("UPDATE users SET password_hash = ? WHERE id = ?", (await security.hash_password(temp), user_id)),
        ("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,)),
    ])
    return True, "New password for " + user["first_name"] + " " + user["last_name"] + ": " + temp


async def delete_user(user_id):
    user = await target(user_id)
    if user is None:
        return False, "Account not found"
    row = await db.one(
        "SELECT ((SELECT COUNT(*) FROM attendance WHERE teacher_id = ? OR scanned_by = ?) "
        "+ (SELECT COUNT(*) FROM qr_sessions WHERE teacher_id = ?)) AS n",
        user_id, user_id, user_id,
    )
    if row["n"]:
        return False, "That account has attendance history, so it cannot be deleted. Suspend it instead."
    await db.batch([
        ("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,)),
        ("DELETE FROM users WHERE id = ?", (user_id,)),
    ])
    return True, "Account deleted"


async def rooms_overview():
    rooms = await db.query(
        "SELECT r.id, r.code, r.name, r.building, r.floor, r.capacity, r.status, "
        "((SELECT COUNT(*) FROM attendance a WHERE a.room_id = r.id) + (SELECT COUNT(*) FROM qr_sessions q WHERE q.room_id = r.id)) AS refs, "
        "(SELECT COUNT(*) FROM attendance a WHERE a.room_id = r.id) AS uses "
        "FROM rooms r ORDER BY r.building, r.name"
    )
    busy = await db.query(
        "SELECT a.room_id, " + services.NAME + " AS teacher_name FROM attendance a JOIN users t ON t.id = a.teacher_id WHERE a.status = 'ACTIVE'"
    )
    active = {row["room_id"]: row["teacher_name"] for row in busy}
    for room in rooms:
        room["occupant"] = active.get(room["id"])
    return rooms


async def update_room(room_id, name, building, floor, capacity, status):
    room = await db.one("SELECT id, status FROM rooms WHERE id = ?", room_id)
    if room is None:
        return False, "Room not found"
    name, building = name.strip(), building.strip()
    if not name or not building:
        return False, "Name and building are required"
    try:
        size = max(0, int(capacity or 0))
    except ValueError:
        size = 0
    new_status = room["status"]
    if room["status"] != "OCCUPIED":
        if status not in ROOM_STATUSES:
            return False, "Invalid room status"
        new_status = status
    await db.execute(
        "UPDATE rooms SET name = ?, building = ?, floor = ?, capacity = ?, status = ? WHERE id = ?",
        name, building, floor.strip(), size, new_status, room_id,
    )
    return True, "Room updated"


async def delete_room(room_id):
    room = await db.one(
        "SELECT status, ((SELECT COUNT(*) FROM attendance WHERE room_id = ?) + (SELECT COUNT(*) FROM qr_sessions WHERE room_id = ?)) AS refs "
        "FROM rooms WHERE id = ?",
        room_id, room_id, room_id,
    )
    if room is None:
        return False, "Room not found"
    if room["status"] == "OCCUPIED" or room["refs"]:
        return False, "That room has attendance history, so it cannot be deleted. Set it to inactive instead."
    await db.execute("DELETE FROM rooms WHERE id = ?", room_id)
    return True, "Room deleted"


async def close_attendance(attendance_id):
    row = await db.one("SELECT id, room_id, time_in FROM attendance WHERE id = ? AND status = 'ACTIVE'", attendance_id)
    if row is None:
        return False, "That record is not active"
    minutes = max(1, round((datetime.now(timezone.utc) - services.parse_utc(row["time_in"])).total_seconds() / 60))
    await db.batch([
        ("UPDATE attendance SET time_out = ?, duration_minutes = ?, status = 'INCOMPLETE' WHERE id = ?", (db.now(), minutes, row["id"])),
        ("UPDATE rooms SET status = 'AVAILABLE' WHERE id = ?", (row["room_id"],)),
    ])
    return True, "Record closed and the room is free again"


async def update_profile(user_id, first, last):
    first, last = first.strip(), last.strip()
    if not first or not last:
        return False, "First and last name are required"
    await db.execute("UPDATE users SET first_name = ?, last_name = ? WHERE id = ?", first, last, user_id)
    return True, "Profile updated"


async def change_password(user_id, keep_token, current, new, confirm):
    row = await db.one("SELECT password_hash FROM users WHERE id = ?", user_id)
    if row is None or not await security.verify_password(current, row["password_hash"]):
        return False, "Your current password is not correct"
    if len(new) < 8:
        return False, "The new password must be at least 8 characters"
    if new != confirm:
        return False, "The new passwords do not match"
    if new == current:
        return False, "Choose a password different from the current one"
    await db.batch([
        ("UPDATE users SET password_hash = ? WHERE id = ?", (await security.hash_password(new), user_id)),
        ("DELETE FROM auth_sessions WHERE user_id = ? AND token_hash <> ?", (user_id, security.sha256(keep_token or ""))),
    ])
    return True, "Password changed. Other devices were signed out."


def local_date(value):
    return services.parse_utc(value).astimezone(config.TZ).date()


async def overview():
    today = datetime.now(config.TZ).date()
    days = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    low = services.day_range(days[0].isoformat())[0]
    week_rows = await db.query("SELECT time_in FROM attendance WHERE time_in >= ?", low)
    counts = Counter(local_date(row["time_in"]) for row in week_rows)
    peak = max(counts.values(), default=0)
    chart = []
    for day in days:
        n = counts.get(day, 0)
        chart.append({
            "label": day.strftime("%a"), "date": day.strftime("%b %d"), "n": n, "today": day == today,
            "h": 3 if n == 0 else max(8, round(n / peak * 100)),
        })
    month_low = services.day_range((today - timedelta(days=29)).isoformat())[0]
    rooms = await db.query(
        "SELECT r.name, COUNT(*) AS n, COALESCE(SUM(a.duration_minutes), 0) AS minutes FROM attendance a "
        "JOIN rooms r ON r.id = a.room_id WHERE a.time_in >= ? GROUP BY r.id, r.name ORDER BY n DESC, r.name LIMIT 5",
        month_low,
    )
    top = max((row["n"] for row in rooms), default=1)
    for row in rooms:
        row["w"] = max(6, round(row["n"] / top * 100))
        row["time"] = services.fmt_minutes(row["minutes"])
    admin = {"role": "admin", "id": 0}
    return {
        "stats": await services.stats(),
        "chart": chart,
        "week_total": sum(item["n"] for item in chart),
        "rooms": rooms,
        "pending": (await pending_users())[:5],
        "active": await services.attendance_rows(admin, {"status": "ACTIVE"}, 8),
        "recent": await services.attendance_rows(admin, {}, 6),
    }
