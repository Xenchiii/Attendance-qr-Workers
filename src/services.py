import re
from datetime import datetime, timedelta, timezone

import segno

import config
import dbx as db
import security
from accounts import ACCOUNTS

EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
QR_LIFETIME_MINUTES = 5
SESSION_HOURS = 8
NAME = "(t.first_name || ' ' || t.last_name)"
_ready = False


def parse_utc(value):
    return datetime.strptime(db.iso(value)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def local_time(value):
    if not value:
        return "-"
    return parse_utc(value).astimezone(config.TZ).strftime("%b %d, %Y %I:%M %p")


def today_local():
    return datetime.now(config.TZ).strftime("%Y-%m-%d")


def day_range(date_text):
    try:
        start = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=config.TZ)
    except ValueError:
        return None
    end = start + timedelta(days=1)
    to_utc = lambda value: value.astimezone(timezone.utc).strftime(db.STAMP)
    return to_utc(start), to_utc(end)


async def ensure_ready():
    global _ready
    if _ready:
        return
    row = await db.one("SELECT COUNT(*) AS n FROM users")
    if row["n"] == 0:
        statements = []
        for account in ACCOUNTS:
            statements.append((
                "INSERT INTO users (role, employee_no, first_name, last_name, email, password_hash, status) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE')",
                (account["role"], account["employee_no"], account["first_name"], account["last_name"], account["email"],
                 await security.hash_password(account["password"])),
            ))
        await db.batch(statements)
    _ready = True


def reset_ready():
    global _ready
    _ready = False


async def register(role, first, last, number, email, password, active=False):
    first, last, number = first.strip(), last.strip(), number.strip()
    email = email.strip().lower()
    if role not in ("teacher", "staff") or not first or not last or not number:
        return False, "Complete all fields"
    if not EMAIL.match(email):
        return False, "Enter a valid email address"
    if len(password) < 8:
        return False, "Password must be at least 8 characters"
    status = "ACTIVE" if active or role == "teacher" else "INACTIVE"
    try:
        await db.execute(
            "INSERT INTO users (role, employee_no, first_name, last_name, email, password_hash, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            role, number, first, last, email, await security.hash_password(password), status,
        )
    except db.IntegrityError:
        return False, "That email or ID number is already registered"
    if active:
        return True, "Account created for " + first + " " + last
    if status == "ACTIVE":
        return True, "Registration successful. You can now log in."
    return True, "Registration successful. An administrator must approve your account before you can log in."


async def login(role, email, password):
    user = await db.one("SELECT * FROM users WHERE email = ? AND role = ?", email.strip().lower(), role)
    if user is None or not await security.verify_password(password, user["password_hash"]):
        return None, "Incorrect email, password or role"
    if user["status"] == "INACTIVE":
        return None, "Your account is waiting for administrator approval"
    if user["status"] != "ACTIVE":
        return None, "Your account is suspended. Contact the administrator"
    token = security.new_token()
    expires = db.stamp(datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS))
    await db.batch([
        ("INSERT INTO auth_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)", (security.sha256(token), user["id"], expires)),
        ("UPDATE users SET last_login = ? WHERE id = ?", (db.now(), user["id"])),
    ])
    return token, None


async def logout(token):
    if security.valid_sid(token):
        await db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", security.sha256(token))


async def user_for_token(token):
    if not security.valid_sid(token):
        return None
    return await db.one(
        "SELECT u.id, u.role, u.employee_no, u.first_name, u.last_name, u.email FROM auth_sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ? AND u.status = 'ACTIVE'",
        security.sha256(token), db.now(),
    )


async def rooms():
    return await db.query("SELECT id, code, name, building, floor, capacity, status FROM rooms ORDER BY building, name")


async def available_rooms():
    return [r for r in await rooms() if r["status"] == "AVAILABLE"]


async def add_room(code, name, building, floor, capacity):
    code, name, building = code.strip(), name.strip(), building.strip()
    if not code or not name or not building:
        return False, "Code, name and building are required"
    try:
        size = max(0, int(capacity or 0))
    except ValueError:
        size = 0
    try:
        await db.execute(
            "INSERT INTO rooms (code, name, building, floor, capacity) VALUES (?, ?, ?, ?, ?)",
            code, name, building, floor.strip(), size,
        )
    except db.IntegrityError:
        return False, "That room code already exists"
    return True, "Room added"


async def set_status(user_id, status):
    if status not in ("ACTIVE", "INACTIVE", "SUSPENDED"):
        return False, "Invalid status"
    statements = [("UPDATE users SET status = ? WHERE id = ? AND role <> 'admin'", (status, user_id))]
    if status != "ACTIVE":
        statements.append((
            "DELETE FROM auth_sessions WHERE user_id IN (SELECT id FROM users WHERE id = ? AND role <> 'admin')", (user_id,),
        ))
    await db.batch(statements)
    return True, "Account updated"


async def stats():
    bounds = day_range(today_local())
    return await db.one(
        "SELECT (SELECT COUNT(*) FROM users WHERE role = 'teacher' AND status = 'ACTIVE') AS teachers, "
        "(SELECT COUNT(*) FROM users WHERE role = 'staff' AND status = 'ACTIVE') AS staff, "
        "(SELECT COUNT(*) FROM users WHERE status = 'INACTIVE') AS pending, "
        "(SELECT COUNT(*) FROM attendance WHERE status = 'ACTIVE') AS in_use, "
        "(SELECT COUNT(*) FROM attendance WHERE time_in >= ? AND time_in < ?) AS today",
        bounds[0], bounds[1],
    )


async def generate_qr(teacher_id, room_id):
    room = await db.one("SELECT id FROM rooms WHERE id = ? AND status = 'AVAILABLE'", room_id)
    if room is None:
        return None, "That room is not available"
    if await db.one("SELECT id FROM attendance WHERE teacher_id = ? AND status = 'ACTIVE'", teacher_id):
        return None, "Time out of your current class before generating a new code"
    token = security.new_token()
    created = datetime.now(timezone.utc)
    expires = created + timedelta(minutes=QR_LIFETIME_MINUTES)
    await db.batch([
        ("UPDATE qr_sessions SET status = 'CANCELLED' WHERE teacher_id = ? AND status = 'ACTIVE'", (teacher_id,)),
        (
            "INSERT INTO qr_sessions (teacher_id, room_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (teacher_id, room["id"], security.sha256(token), db.stamp(created), db.stamp(expires)),
        ),
    ])
    return {"token": token, "expires_at": expires.timestamp(), "room_id": room["id"]}, None


def qr_image(text):
    return segno.make(text, error="m", micro=False).svg_data_uri(scale=8, border=2)


SORTS = {"time": "a.time_in", "teacher": "teacher_name", "room": "r.name", "duration": "a.duration_minutes", "status": "a.status"}
STATUSES = ("ACTIVE", "COMPLETED", "INCOMPLETE", "CANCELLED")
PER_PAGE = 12
BASE = "FROM attendance a JOIN users t ON t.id = a.teacher_id JOIN rooms r ON r.id = a.room_id"


def fmt_minutes(minutes):
    if minutes is None:
        return "-"
    hours, rest = divmod(int(minutes), 60)
    return "%dh %02dm" % (hours, rest) if hours else "%d min" % rest


def paginate(total, page, per):
    pages = max(1, -(-total // per))
    page = min(max(1, page), pages)
    window = []
    for number in range(1, pages + 1):
        if number in (1, pages) or abs(number - page) <= 1:
            window.append(number)
        elif window and window[-1] is not None:
            window.append(None)
    return {
        "page": page, "pages": pages, "per": per, "total": total, "offset": (page - 1) * per, "window": window,
        "first": 0 if total == 0 else (page - 1) * per + 1, "last": min(total, page * per),
        "prev": page - 1 if page > 1 else None, "next": page + 1 if page < pages else None,
    }


def range_bounds(start, end):
    low = day_range(start)[0] if start and day_range(start) else None
    high = day_range(end)[1] if end and day_range(end) else None
    return low, high


def decorate(row):
    row["time_in_text"] = local_time(row["time_in"])
    row["time_out_text"] = local_time(row["time_out"])
    row["duration_text"] = fmt_minutes(row["duration_minutes"])
    if row["status"] == "ACTIVE":
        elapsed = (datetime.now(timezone.utc) - parse_utc(row["time_in"])).total_seconds() / 60
        row["elapsed_minutes"] = max(0, int(elapsed))
        row["elapsed_text"] = fmt_minutes(row["elapsed_minutes"])
    return row


def attendance_where(user, f):
    where = ["1 = 1"]
    args = []
    if user["role"] == "teacher":
        where.append("a.teacher_id = ?")
        args.append(user["id"])
    if f.get("status") in STATUSES:
        where.append("a.status = ?")
        args.append(f["status"])
    if str(f.get("room", "")).isdigit():
        where.append("a.room_id = ?")
        args.append(int(f["room"]))
    low, high = range_bounds(f.get("start"), f.get("end"))
    if low:
        where.append("a.time_in >= ?")
        args.append(low)
    if high:
        where.append("a.time_in < ?")
        args.append(high)
    if f.get("q"):
        where.append("(" + NAME + " LIKE ? OR r.name LIKE ?)")
        args.extend(["%" + f["q"] + "%", "%" + f["q"] + "%"])
    return " AND ".join(where), args


async def attendance_count(user, f):
    where, args = attendance_where(user, f)
    return (await db.one("SELECT COUNT(*) AS n " + BASE + " WHERE " + where, *args))["n"]


async def attendance_rows(user, f=None, limit=500, offset=0):
    f = f or {}
    where, args = attendance_where(user, f)
    column = SORTS.get(f.get("sort"), "a.time_in")
    direction = "ASC" if f.get("dir") == "asc" else "DESC"
    sql = (
        "SELECT a.id, a.time_in, a.time_out, a.duration_minutes, a.status, a.teacher_id, a.room_id, "
        + NAME + " AS teacher_name, r.name AS room_name " + BASE + " WHERE " + where
        + " ORDER BY " + column + " " + direction + ", a.id DESC LIMIT ? OFFSET ?"
    )
    return [decorate(row) for row in await db.query(sql, *args, limit, offset)]


async def attendance_page(user, f, page, per=PER_PAGE):
    pager = paginate(await attendance_count(user, f), page, per)
    return await attendance_rows(user, f, per, pager["offset"]), pager


async def teacher_summary(teacher_id):
    now_local = datetime.now(config.TZ)
    monday = (now_local - timedelta(days=now_local.weekday())).strftime("%Y-%m-%d")
    low = day_range(monday)[0]
    week = await db.one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(duration_minutes), 0) AS minutes FROM attendance WHERE teacher_id = ? AND time_in >= ?",
        teacher_id, low,
    )
    total = await db.one(
        "SELECT COUNT(*) AS n, COALESCE(AVG(duration_minutes), 0) AS avg FROM attendance WHERE teacher_id = ? AND status = 'COMPLETED'",
        teacher_id,
    )
    every = await db.one("SELECT COUNT(*) AS n FROM attendance WHERE teacher_id = ?", teacher_id)
    return {
        "week_classes": week["n"], "week_time": fmt_minutes(week["minutes"]),
        "all_classes": every["n"],
        "average": fmt_minutes(round(total["avg"])) if total["n"] else "-",
    }


async def cancel_qr(teacher_id):
    row = await db.one("SELECT COUNT(*) AS n FROM qr_sessions WHERE teacher_id = ? AND status = 'ACTIVE'", teacher_id)
    await db.execute("UPDATE qr_sessions SET status = 'CANCELLED' WHERE teacher_id = ? AND status = 'ACTIVE'", teacher_id)
    return (True, "QR code cancelled") if row["n"] else (False, "You have no active QR code")


async def scan(token, staff_id):
    token = (token or "").strip()
    if not token:
        return False, "No QR code data received"
    session = await db.one(
        "SELECT id, teacher_id, room_id, status, expires_at FROM qr_sessions WHERE token_hash = ?", security.sha256(token),
    )
    if session is None:
        return False, "This QR code is not recognised"
    if session["status"] != "ACTIVE":
        return False, "This QR code was already used or cancelled"
    if db.stamp(parse_utc(session["expires_at"])) < db.now():
        await db.execute("UPDATE qr_sessions SET status = 'EXPIRED' WHERE id = ?", session["id"])
        return False, "This QR code has expired. Ask the teacher to generate a new one"
    if await db.one("SELECT id FROM attendance WHERE teacher_id = ? AND status = 'ACTIVE'", session["teacher_id"]):
        return False, "This teacher already has an active attendance record"
    room = await db.one("SELECT name, status FROM rooms WHERE id = ?", session["room_id"])
    if room["status"] != "AVAILABLE":
        return False, room["name"] + " is not available"
    moment = db.now()
    try:
        await db.batch([
            (
                "INSERT INTO attendance (teacher_id, room_id, qr_session_id, scanned_by, time_in) VALUES (?, ?, ?, ?, ?)",
                (session["teacher_id"], session["room_id"], session["id"], staff_id, moment),
            ),
            ("UPDATE qr_sessions SET status = 'USED', used_at = ? WHERE id = ?", (moment, session["id"])),
            ("UPDATE rooms SET status = 'OCCUPIED' WHERE id = ?", (session["room_id"],)),
        ])
    except db.IntegrityError:
        return False, "This teacher already has an active attendance record"
    teacher = await db.one("SELECT first_name, last_name FROM users WHERE id = ?", session["teacher_id"])
    return True, "Time In recorded for " + teacher["first_name"] + " " + teacher["last_name"] + " in " + room["name"]


async def time_out(teacher_id):
    open_row = await db.one("SELECT id, room_id, time_in FROM attendance WHERE teacher_id = ? AND status = 'ACTIVE'", teacher_id)
    if open_row is None:
        return False, "You have no active attendance record"
    minutes = max(1, round((datetime.now(timezone.utc) - parse_utc(open_row["time_in"])).total_seconds() / 60))
    await db.batch([
        ("UPDATE attendance SET time_out = ?, duration_minutes = ?, status = 'COMPLETED' WHERE id = ?", (db.now(), minutes, open_row["id"])),
        ("UPDATE rooms SET status = 'AVAILABLE' WHERE id = ?", (open_row["room_id"],)),
    ])
    return True, "Time Out recorded"
