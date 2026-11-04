import re
import time

import pytest

import dbx
import security
import services


ADMIN = ("admin", "admin124@attendance.ph", "Admin@12345")
TEACHER = ("teacher", "teacher@attendance.ph", "Teacher@12345")
STAFF = ("staff", "staff@attendance.ph", "Staff@12345")



@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_login_page_and_seeded_accounts(client):
    page = await client.get("/")
    assert page.status == 200 and "QR Attendance" in page.body
    for creds in (ADMIN, TEACHER, STAFF):
        c = type(client)()
        result = await c.login(*creds)
        assert result.headers[0][1] == "/" + creds[0]


async def test_bad_password_shows_flash(client):
    result = await client.post("/login", role="admin", email=ADMIN[1], password="wrong")
    assert result.status == 303
    page = await client.get("/")
    assert "Incorrect email, password or role" in page.body
    again = await client.get("/")
    assert "Incorrect email" not in again.body


async def test_hash_compatible_with_python_and_java_format(client):
    stored = await security.hash_password("Secret123")
    assert re.fullmatch(r"[0-9a-f]{32}:[0-9a-f]{64}", stored)
    assert await security.verify_password("Secret123", stored)
    assert not await security.verify_password("secret123", stored)


async def test_signup_rules(client):
    await client.post("/register", role="teacher", first_name="Ana", last_name="Lee", employee_no="T-9", email="ana@x.ph", password="longenough")
    ok = await client.login("teacher", "ana@x.ph", "longenough")
    assert ok.headers[0][1] == "/teacher"
    await client.post("/register", role="staff", first_name="Bo", last_name="Cruz", employee_no="S-9", email="bo@x.ph", password="longenough")
    c = type(client)()
    await c.post("/login", role="staff", email="bo@x.ph", password="longenough")
    assert "waiting for administrator approval" in (await c.get("/")).body
    visitor = type(client)()
    await visitor.post("/register", role="staff", first_name="B", last_name="C", employee_no="S-9", email="b2@x.ph", password="longenough")
    assert "already registered" in (await visitor.get("/?mode=signup")).body


async def test_full_attendance_flow(client):
    teacher = type(client)()
    staff = type(client)()
    await teacher.login(*TEACHER)
    await staff.login(*STAFF)
    room = (await dbx.one("SELECT id FROM rooms WHERE code = 'RM-101'"))["id"]
    await teacher.post("/teacher/qr", room_id=str(room))
    page = await teacher.get("/teacher")
    assert "data:image/svg+xml" in page.body
    token = re.search(r'class="code">([a-f0-9]+)<', page.body).group(1)
    await staff.post("/staff/scan", token=token)
    assert "Time In recorded for Jerald Resurreccion in Room 101" in (await staff.get("/staff")).body
    await staff.post("/staff/scan", token=token)
    assert "already used" in (await staff.get("/staff")).body
    assert "Currently in class" in (await teacher.get("/teacher")).body
    await teacher.post("/teacher/timeout")
    after = await teacher.get("/teacher")
    assert "Time Out recorded" in after.body and "Currently in class" not in after.body
    row = await dbx.one("SELECT status FROM attendance")
    assert row["status"] == "COMPLETED"
    assert (await dbx.one("SELECT status FROM rooms WHERE id = ?", room))["status"] == "AVAILABLE"


async def test_expired_code_and_cancel(client):
    teacher = type(client)()
    staff = type(client)()
    await teacher.login(*TEACHER)
    await staff.login(*STAFF)
    room = (await dbx.one("SELECT id FROM rooms LIMIT 1"))["id"]
    await teacher.post("/teacher/qr", room_id=str(room))
    token = re.search(r'class="code">([a-f0-9]+)<', (await teacher.get("/teacher")).body).group(1)
    await dbx.execute("UPDATE qr_sessions SET expires_at = '2000-01-01 00:00:00'")
    await staff.post("/staff/scan", token=token)
    assert "expired" in (await staff.get("/staff")).body
    await teacher.post("/teacher/qr", room_id=str(room))
    await teacher.post("/teacher/qr/cancel")
    assert "QR code cancelled" in (await teacher.get("/teacher")).body


async def test_roles_are_enforced(client):
    teacher = type(client)()
    await teacher.login(*TEACHER)
    denied = await teacher.get("/admin")
    assert denied.status == 303 and denied.headers[0][1] == "/teacher"
    anon = await type(client)().get("/admin")
    assert anon.status == 303 and anon.headers[0][1] == "/"
    assert (await type(client)().get("/nope")).status == 404


async def test_admin_pages_render(client):
    admin = type(client)()
    await admin.login(*ADMIN)
    for path in ("/admin", "/admin/users", "/admin/rooms", "/admin/reports", "/profile", "/admin/users?q=juan&role=teacher&sort=name&dir=desc"):
        page = await admin.get(path)
        assert page.status == 200, path
    assert "Jerald" in (await admin.get("/admin/users")).body


async def test_admin_manages_users_and_rooms(client):
    admin = type(client)()
    await admin.login(*ADMIN)
    await admin.post("/admin/users", role="staff", first_name="New", last_name="Hire", employee_no="N-1", email="new@x.ph", password="longenough")
    user = await dbx.one("SELECT id, status FROM users WHERE email = 'new@x.ph'")
    assert user["status"] == "ACTIVE"
    await admin.post("/admin/users/%d/status" % user["id"], status="SUSPENDED")
    assert (await dbx.one("SELECT status FROM users WHERE id = ?", user["id"]))["status"] == "SUSPENDED"
    await admin.post("/admin/users/%d/reset" % user["id"])
    page = await admin.get("/admin/users")
    assert "New password for New Hire" in page.body and "<code>" in page.body
    await admin.post("/admin/users/%d/delete" % user["id"])
    assert await dbx.one("SELECT id FROM users WHERE email = 'new@x.ph'") is None
    await admin.post("/admin/rooms", code="X-1", name="Extra", building="Annex", floor="G", capacity="10")
    room = await dbx.one("SELECT id FROM rooms WHERE code = 'X-1'")
    await admin.post("/admin/rooms/%d" % room["id"], name="Extra 2", building="Annex", floor="G", capacity="12", status="MAINTENANCE")
    assert (await dbx.one("SELECT status, name FROM rooms WHERE id = ?", room["id"]))["status"] == "MAINTENANCE"
    await admin.post("/admin/rooms/%d/delete" % room["id"])
    assert await dbx.one("SELECT id FROM rooms WHERE code = 'X-1'") is None


async def test_profile_and_password_change(client):
    teacher = type(client)()
    await teacher.login(*TEACHER)
    await teacher.post("/profile", first_name="Jeralda", last_name="Cruz")
    assert "Jeralda" in (await teacher.get("/teacher")).body
    await teacher.post("/profile/password", current="Teacher@12345", new="BrandNew123", confirm="BrandNew123")
    fresh = type(client)()
    await fresh.login("teacher", "teacher@attendance.ph", "BrandNew123")
    other = type(client)()
    await other.post("/login", role="teacher", email="teacher@attendance.ph", password="Teacher@12345")
    assert "Incorrect email" in (await other.get("/")).body


async def test_history_filters_csv_and_theme(client):
    admin = type(client)()
    await admin.login(*ADMIN)
    teacher_id = (await dbx.one("SELECT id FROM users WHERE role = 'teacher'"))["id"]
    room_id = (await dbx.one("SELECT id FROM rooms LIMIT 1"))["id"]
    for i in range(15):
        await dbx.execute(
            "INSERT INTO attendance (teacher_id, room_id, time_in, time_out, duration_minutes, status) VALUES (?, ?, ?, ?, 50, 'COMPLETED')",
            teacher_id, room_id, "2026-10-%02d 01:00:00" % (i + 1), "2026-10-%02d 01:50:00" % (i + 1),
        )
    page = await admin.get("/admin/reports?sort=duration&dir=asc&page=2")
    assert page.status == 200
    csv_page = await admin.get("/report.csv?status=COMPLETED")
    assert csv_page.body.count("\n") == 16 and "text/csv" in csv_page.headers[0][1]
    await admin.post("/theme", mode="dark", next="/admin")
    assert 'data-theme="dark"' in (await admin.get("/admin")).body
    inject = await admin.get("/admin/reports?sort=time;DROP TABLE users")
    assert inject.status == 200 and await dbx.one("SELECT COUNT(*) AS n FROM users")


async def test_one_active_class_rule_is_atomic(client):
    await client.get("/")
    teacher_id = (await dbx.one("SELECT id FROM users WHERE role = 'teacher'"))["id"]
    room = (await dbx.one("SELECT id FROM rooms LIMIT 1"))["id"]
    await dbx.execute("INSERT INTO attendance (teacher_id, room_id, time_in) VALUES (?, ?, ?)", teacher_id, room, dbx.now())
    with pytest.raises(dbx.IntegrityError):
        await dbx.batch([
            ("UPDATE rooms SET status = 'OCCUPIED' WHERE id = ?", (room,)),
            ("INSERT INTO attendance (teacher_id, room_id, time_in) VALUES (?, ?, ?)", (teacher_id, room, dbx.now())),
        ])
    assert (await dbx.one("SELECT status FROM rooms WHERE id = ?", room))["status"] == "AVAILABLE"


async def test_logout_and_security_headers_on_cookie(client):
    teacher = type(client)()
    result = await teacher.login(*TEACHER)
    cookie = [c for c in result.cookies if c.startswith("sid=")][0]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Secure" in cookie
    await teacher.post("/logout")
    assert (await teacher.get("/teacher")).status == 303


async def test_demo_login_route_matches_words(client):
    import config
    config.DEMO_LOGIN = True
    try:
        c = type(client)()
        result = await c.post("/demo-login/teacher")
        assert result.status == 303 and result.headers[0][1] == "/teacher"
    finally:
        config.DEMO_LOGIN = False
