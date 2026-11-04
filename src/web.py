import base64
import csv
import io
import json
import re
import time
import traceback
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import jinja2

import admin_services
import config
import dbx as db
import services
from accounts import ACCOUNTS
from templates_bundle import TEMPLATES

COOKIE_NAME = "sid"
SESSION_SECONDS = services.SESSION_HOURS * 3600
HOMES = {"teacher": "/teacher", "staff": "/staff", "admin": "/admin"}
THEMES = ("system", "light", "dark")
NAV = {
    "admin": [
        ("Overview", "admin", ("admin",)),
        ("Users", "admin_users", ("admin_users",)),
        ("Rooms", "admin_rooms", ("admin_rooms",)),
        ("Reports", "admin_reports", ("admin_reports",)),
        ("Profile", "profile", ("profile",)),
    ],
    "staff": [
        ("Scan", "staff", ("staff",)),
        ("History", "history", ("history",)),
        ("Profile", "profile", ("profile",)),
    ],
    "teacher": [
        ("Dashboard", "teacher", ("teacher",)),
        ("My attendance", "history", ("history",)),
        ("Profile", "profile", ("profile",)),
    ],
}

PATHS = {}
ROUTES = []


class Response:
    def __init__(self, body="", status=200, headers=None, cookies=None):
        self.body = body
        self.status = status
        self.headers = headers or []
        self.cookies = cookies or []


class Context:
    def __init__(self, method, url, cookie_header, body):
        parts = urlsplit(url)
        self.method = method
        self.path = parts.path or "/"
        self.query = parts.query
        self.secure = parts.scheme == "https"
        self.args = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        self.form = {k: v[0] for k, v in parse_qs(body or "", keep_blank_values=True).items()}
        self.cookies = parse_cookies(cookie_header)
        self.user = None
        self.theme = "system"
        self.endpoint = None
        self.flashes = []
        self.new_flashes = []
        self.out = []

    def flash(self, message, category):
        self.new_flashes.append([category, message])

    def notify(self, result):
        ok, message = result
        self.flash(message, "ok" if ok else "bad")
        return ok

    def set_cookie(self, name, value, max_age, http_only=True, samesite="Strict"):
        parts = [name + "=" + value, "Path=/", "Max-Age=%d" % max_age, "SameSite=" + samesite]
        if http_only:
            parts.append("HttpOnly")
        if self.secure:
            parts.append("Secure")
        self.out.append("; ".join(parts))

    def drop_cookie(self, name):
        self.set_cookie(name, "", 0)

    def full_path(self):
        return self.path + "?" + self.query


def parse_cookies(header):
    jar = {}
    for piece in (header or "").split(";"):
        if "=" in piece:
            key, value = piece.split("=", 1)
            jar[key.strip()] = value.strip()
    return jar


def pack_flash(items):
    return base64.urlsafe_b64encode(json.dumps(items).encode("utf-8")).decode("ascii").rstrip("=")


def unpack_flash(text):
    try:
        data = json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode("utf-8"))
        return [(str(c), str(m)) for c, m in data][:5]
    except Exception:
        return []


def route(method, pattern, name, roles=None):
    PATHS[name] = pattern
    regex = re.compile("^" + re.sub(r"\{(\w+)\}", lambda m: "(?P<%s>%s)" % (m.group(1), r"\d+" if m.group(1).endswith("_id") else r"[a-z]+"), pattern) + "$")

    def decorator(view):
        ROUTES.append((method, regex, name, roles, view))
        return view
    return decorator


def url_for(name, **values):
    if name == "static":
        return "/static/" + values["filename"]
    path = PATHS[name]
    rest = {}
    for key, value in values.items():
        token = "{" + key + "}"
        if token in path:
            path = path.replace(token, str(value))
        elif value is not None and value != "":
            rest[key] = value
    return path + ("?" + urlencode(rest) if rest else "")


def safe_next(target, fallback):
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return fallback


def redirect(ctx, to):
    if ctx.new_flashes:
        ctx.set_cookie("flash", pack_flash(ctx.new_flashes), 60, samesite="Lax")
    return Response("", 303, [("Location", to)], ctx.out)


def back(ctx, default):
    return redirect(ctx, safe_next(ctx.form.get("next"), url_for(default)))


def valid_date(text):
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return text
    except ValueError:
        return ""


def to_int(text, default=1):
    try:
        return max(1, int(text))
    except (TypeError, ValueError):
        return default


environment = jinja2.Environment(loader=jinja2.DictLoader(TEMPLATES), autoescape=True)
environment.filters["pretty"] = lambda value: str(value).lower()
environment.globals["url_for"] = url_for


async def render(ctx, template, status=200, **values):
    user = ctx.user
    page = {
        "g": SimpleNamespace(user=user, theme=ctx.theme),
        "theme": ctx.theme,
        "today_text": datetime.now(config.TZ).strftime("%a, %b %d"),
        "tz_name": config.tz_label(),
        "request": SimpleNamespace(
            args=ctx.args, endpoint=ctx.endpoint, path=ctx.path, full_path=ctx.full_path(),
        ),
        "get_flashed_messages": lambda with_categories=False: ctx.flashes,
    }

    def here(**changes):
        args = dict(ctx.args)
        for key, value in changes.items():
            if value in (None, ""):
                args.pop(key, None)
            else:
                args[key] = value
        return ctx.path + ("?" + urlencode(args) if args else "")

    page["here"] = here
    if user:
        page["nav"] = [
            {"label": label, "endpoint": endpoint, "active": ctx.endpoint in group}
            for label, endpoint, group in NAV[user["role"]]
        ]
        page["initials"] = admin_services.initials(user["first_name"], user["last_name"])
        page["pending_count"] = await admin_services.pending_count() if user["role"] == "admin" else 0
    page.update(values)
    body = environment.get_template(template).render(**page)
    if "flash" in ctx.cookies:
        ctx.drop_cookie("flash")
    return Response(body, status, [("Content-Type", "text/html; charset=utf-8")], ctx.out)


def filters(ctx):
    args = ctx.args
    sort = args.get("sort", "time")
    return {
        "q": args.get("q", "").strip()[:80],
        "status": args.get("status", "") if args.get("status") in services.STATUSES else "",
        "start": valid_date(args.get("start", "")),
        "end": valid_date(args.get("end", "")),
        "room": args.get("room", "") if args.get("room", "").isdigit() else "",
        "sort": sort if sort in services.SORTS else "time",
        "dir": "asc" if args.get("dir") == "asc" else "desc",
    }


def presets(f):
    today = datetime.now(config.TZ).date()
    items = [
        ("Today", today, today),
        ("Last 7 days", today - timedelta(days=6), today),
        ("Last 30 days", today - timedelta(days=29), today),
    ]
    return [
        {"label": label, "start": a.isoformat(), "end": b.isoformat(), "on": f["start"] == a.isoformat() and f["end"] == b.isoformat()}
        for label, a, b in items
    ]


def login_cookie(ctx, token):
    ctx.set_cookie(COOKIE_NAME, token, SESSION_SECONDS)


@route("POST", "/theme", "theme")
async def theme(ctx):
    mode = ctx.form.get("mode", "system")
    if mode == "cycle":
        mode = THEMES[(THEMES.index(ctx.theme) + 1) % len(THEMES)]
    if mode not in THEMES:
        mode = "system"
    ctx.set_cookie("theme", mode, 31536000, http_only=False, samesite="Lax")
    return redirect(ctx, safe_next(ctx.form.get("next"), "/"))


@route("GET", "/", "index")
async def index(ctx):
    if ctx.user:
        return redirect(ctx, HOMES[ctx.user["role"]])
    mode = "signup" if ctx.args.get("mode") == "signup" else "login"
    return await render(ctx, "login.html", mode=mode, demo=ACCOUNTS if config.DEMO_LOGIN else [])


@route("POST", "/login", "login")
async def login(ctx):
    role = ctx.form.get("role", "")
    token, error = await services.login(role, ctx.form.get("email", ""), ctx.form.get("password", ""))
    if error:
        ctx.flash(error, "bad")
        return redirect(ctx, "/")
    login_cookie(ctx, token)
    return redirect(ctx, HOMES[role])


@route("POST", "/demo-login/{role}", "demo_login")
async def demo_login(ctx, role):
    account = next((a for a in ACCOUNTS if a["role"] == role), None)
    if not config.DEMO_LOGIN or account is None:
        return redirect(ctx, "/")
    token, error = await services.login(role, account["email"], account["password"])
    if error:
        ctx.flash(error, "bad")
        return redirect(ctx, "/")
    login_cookie(ctx, token)
    return redirect(ctx, HOMES[role])


@route("POST", "/register", "register")
async def register(ctx):
    form = ctx.form
    ok, message = await services.register(
        form.get("role", ""), form.get("first_name", ""), form.get("last_name", ""),
        form.get("employee_no", ""), form.get("email", ""), form.get("password", ""),
    )
    ctx.flash(message, "ok" if ok else "bad")
    return redirect(ctx, "/?mode=" + ("login" if ok else "signup"))


@route("POST", "/logout", "logout")
async def logout(ctx):
    await services.logout(ctx.cookies.get(COOKIE_NAME))
    ctx.drop_cookie(COOKIE_NAME)
    ctx.drop_cookie("qr")
    return redirect(ctx, "/")


@route("GET", "/profile", "profile", ())
async def profile(ctx):
    row = await db.one("SELECT employee_no, created_at, last_login FROM users WHERE id = ?", ctx.user["id"])
    return await render(
        ctx, "profile.html",
        joined=services.local_time(row["created_at"]),
        last_login=services.local_time(row["last_login"]) if row["last_login"] else "This session",
        employee_no=row["employee_no"],
    )


@route("POST", "/profile", "profile_update", ())
async def profile_update(ctx):
    ctx.notify(await admin_services.update_profile(ctx.user["id"], ctx.form.get("first_name", ""), ctx.form.get("last_name", "")))
    return redirect(ctx, "/profile")


@route("POST", "/profile/password", "profile_password", ())
async def profile_password(ctx):
    form = ctx.form
    ctx.notify(await admin_services.change_password(
        ctx.user["id"], ctx.cookies.get(COOKIE_NAME), form.get("current", ""), form.get("new", ""), form.get("confirm", ""),
    ))
    return redirect(ctx, "/profile")


def pending_qr(ctx):
    raw = ctx.cookies.get("qr", "")
    token, _, expires = raw.partition(".")
    try:
        left = float(expires) - time.time()
    except ValueError:
        return None
    return {"token": token, "seconds": int(left)} if left > 0 and re.fullmatch(r"[a-f0-9]+", token) else None


@route("GET", "/teacher", "teacher", ("teacher",))
async def teacher(ctx):
    user = ctx.user
    current = await db.one(
        "SELECT a.id, a.time_in, a.room_id, r.name AS room_name FROM attendance a JOIN rooms r ON r.id = a.room_id "
        "WHERE a.teacher_id = ? AND a.status = 'ACTIVE'",
        user["id"],
    )
    if current:
        current = services.decorate({**current, "time_out": None, "duration_minutes": None, "status": "ACTIVE"})
    qr = None
    pending = pending_qr(ctx)
    if pending and not current:
        qr = {"image": services.qr_image(pending["token"]), "token": pending["token"], "seconds": pending["seconds"]}
    elif "qr" in ctx.cookies:
        ctx.drop_cookie("qr")
    return await render(
        ctx, "teacher.html",
        rooms=await services.available_rooms(),
        recent=await services.attendance_rows(user, {}, 5),
        summary=await services.teacher_summary(user["id"]),
        current=current,
        qr=qr,
    )


@route("POST", "/teacher/qr", "teacher_qr", ("teacher",))
async def teacher_qr(ctx):
    data, error = await services.generate_qr(ctx.user["id"], ctx.form.get("room_id", ""))
    if error:
        ctx.flash(error, "bad")
    else:
        ctx.set_cookie("qr", data["token"] + "." + str(int(data["expires_at"])), services.QR_LIFETIME_MINUTES * 60)
    return redirect(ctx, "/teacher")


@route("POST", "/teacher/qr/cancel", "teacher_qr_cancel", ("teacher",))
async def teacher_qr_cancel(ctx):
    ctx.drop_cookie("qr")
    ctx.notify(await services.cancel_qr(ctx.user["id"]))
    return redirect(ctx, "/teacher")


@route("POST", "/teacher/timeout", "teacher_timeout", ("teacher",))
async def teacher_timeout(ctx):
    ctx.notify(await services.time_out(ctx.user["id"]))
    return back(ctx, "teacher")


@route("GET", "/history", "history", ("teacher", "staff"))
async def history(ctx):
    f = filters(ctx)
    rows, pager = await services.attendance_page(ctx.user, f, to_int(ctx.args.get("page")))
    return await render(
        ctx, "history.html", title="My attendance" if ctx.user["role"] == "teacher" else "Attendance history",
        rows=rows, pager=pager, f=f, presets=presets(f), rooms=await services.rooms(),
        show_teacher=ctx.user["role"] != "teacher", can_close=False,
    )


@route("GET", "/staff", "staff", ("staff",))
async def staff(ctx):
    today = services.today_local()
    window = {"start": today, "end": today}
    return await render(
        ctx, "staff.html",
        in_use=await services.attendance_rows(ctx.user, {"status": "ACTIVE"}, 50),
        recent=await services.attendance_rows(ctx.user, window, 8),
        today_count=await services.attendance_count(ctx.user, window),
    )


@route("POST", "/staff/scan", "staff_scan", ("staff",))
async def staff_scan(ctx):
    ctx.notify(await services.scan(ctx.form.get("token", ""), ctx.user["id"]))
    return redirect(ctx, "/staff")


@route("GET", "/admin", "admin", ("admin",))
async def admin(ctx):
    return await render(ctx, "admin.html", **await admin_services.overview())


@route("GET", "/admin/users", "admin_users", ("admin",))
async def admin_users(ctx):
    args = ctx.args
    f = {
        "q": args.get("q", "").strip()[:80],
        "role": args.get("role", "") if args.get("role") in ("teacher", "staff") else "",
        "status": args.get("status", "") if args.get("status") in ("ACTIVE", "INACTIVE", "SUSPENDED") else "",
        "sort": args.get("sort", "status") if args.get("sort") in admin_services.USER_SORTS else "status",
        "dir": "desc" if args.get("dir") == "desc" else "asc",
    }
    rows, pager = await admin_services.users_page(f, to_int(args.get("page")))
    return await render(ctx, "admin_users.html", users=rows, pager=pager, f=f)


@route("POST", "/admin/users", "admin_user_create", ("admin",))
async def admin_user_create(ctx):
    form = ctx.form
    ctx.notify(await admin_services.create_user(
        form.get("role", ""), form.get("first_name", ""), form.get("last_name", ""),
        form.get("employee_no", ""), form.get("email", ""), form.get("password", ""),
    ))
    return back(ctx, "admin_users")


@route("POST", "/admin/users/{user_id}/status", "admin_status", ("admin",))
async def admin_status(ctx, user_id):
    ctx.notify(await services.set_status(int(user_id), ctx.form.get("status", "")))
    return back(ctx, "admin_users")


@route("POST", "/admin/users/{user_id}/reset", "admin_reset", ("admin",))
async def admin_reset(ctx, user_id):
    ok, message = await admin_services.reset_password(int(user_id))
    ctx.flash(message, "sticky" if ok else "bad")
    return back(ctx, "admin_users")


@route("POST", "/admin/users/{user_id}/delete", "admin_delete", ("admin",))
async def admin_delete(ctx, user_id):
    ctx.notify(await admin_services.delete_user(int(user_id)))
    return back(ctx, "admin_users")


@route("GET", "/admin/rooms", "admin_rooms", ("admin",))
async def admin_rooms(ctx):
    return await render(ctx, "admin_rooms.html", rooms=await admin_services.rooms_overview())


@route("POST", "/admin/rooms", "admin_room_add", ("admin",))
async def admin_room_add(ctx):
    form = ctx.form
    ctx.notify(await services.add_room(
        form.get("code", ""), form.get("name", ""), form.get("building", ""), form.get("floor", ""), form.get("capacity", ""),
    ))
    return redirect(ctx, "/admin/rooms")


@route("POST", "/admin/rooms/{room_id}", "admin_room_update", ("admin",))
async def admin_room_update(ctx, room_id):
    form = ctx.form
    ctx.notify(await admin_services.update_room(
        int(room_id), form.get("name", ""), form.get("building", ""), form.get("floor", ""), form.get("capacity", ""), form.get("status", ""),
    ))
    return redirect(ctx, "/admin/rooms")


@route("POST", "/admin/rooms/{room_id}/delete", "admin_room_delete", ("admin",))
async def admin_room_delete(ctx, room_id):
    ctx.notify(await admin_services.delete_room(int(room_id)))
    return redirect(ctx, "/admin/rooms")


@route("GET", "/admin/reports", "admin_reports", ("admin",))
async def admin_reports(ctx):
    f = filters(ctx)
    rows, pager = await services.attendance_page(ctx.user, f, to_int(ctx.args.get("page")))
    return await render(
        ctx, "history.html", title="Attendance reports", rows=rows, pager=pager, f=f, presets=presets(f),
        rooms=await services.rooms(), show_teacher=True, can_close=True,
    )


@route("POST", "/admin/attendance/{attendance_id}/close", "admin_close", ("admin",))
async def admin_close(ctx, attendance_id):
    ctx.notify(await admin_services.close_attendance(int(attendance_id)))
    return back(ctx, "admin_reports")


@route("GET", "/report.csv", "report", ())
async def report(ctx):
    rows = await services.attendance_rows(ctx.user, filters(ctx), 5000)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Teacher", "Room", "Time in", "Time out", "Duration (min)", "Status"])
    for row in rows:
        writer.writerow([
            row["teacher_name"], row["room_name"], row["time_in_text"], row["time_out_text"],
            row["duration_minutes"] if row["duration_minutes"] is not None else "", row["status"],
        ])
    return Response(
        buffer.getvalue(), 200,
        [("Content-Type", "text/csv; charset=utf-8"), ("Content-Disposition", "attachment; filename=attendance-report.csv")],
        ctx.out,
    )


async def error_page(ctx, code, title, text):
    ctx.endpoint = "error"
    return await render(ctx, "error.html", status=code, code=code, title=title, text=text)


async def handle(method, url, cookie_header, body, env=None):
    ctx = Context(method, url, cookie_header, body)
    try:
        if env is not None:
            config.configure(env)
            db.use(env.DB)
        await services.ensure_ready()
        theme_cookie = ctx.cookies.get("theme", "system")
        ctx.theme = theme_cookie if theme_cookie in THEMES else "system"
        ctx.user = await services.user_for_token(ctx.cookies.get(COOKIE_NAME))
        if "flash" in ctx.cookies:
            ctx.flashes = unpack_flash(ctx.cookies["flash"])
        for verb, regex, name, roles, view in ROUTES:
            found = regex.match(ctx.path)
            if verb != method or not found:
                continue
            ctx.endpoint = name
            if roles is not None:
                if ctx.user is None:
                    return redirect(ctx, "/")
                if roles and ctx.user["role"] not in roles:
                    ctx.flash("You do not have access to that page", "bad")
                    return redirect(ctx, HOMES[ctx.user["role"]])
            return await view(ctx, **found.groupdict())
        return await error_page(ctx, 404, "Page not found", "That page does not exist or was moved.")
    except Exception as error:
        print("".join(traceback.format_exception(error)))
        text = "The server hit a problem. Please try again."
        if "no such table" in str(error).lower():
            text = "The database tables are missing. Run the schema.sql step from the setup guide."
        ctx.user = None
        try:
            return await error_page(ctx, 500, "Something went wrong", text)
        except Exception:
            return Response("Something went wrong", 500, [("Content-Type", "text/plain; charset=utf-8")])
