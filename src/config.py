from datetime import timedelta, timezone

TZ_HOURS = 8.0
TZ = timezone(timedelta(hours=TZ_HOURS))
DEMO_LOGIN = False


def tz_label():
    total = int(TZ_HOURS * 60)
    sign = "+" if total >= 0 else "-"
    return "UTC%s%02d:%02d" % (sign, abs(total) // 60, abs(total) % 60)


def read(env, name, default=None):
    try:
        value = getattr(env, name)
    except Exception:
        return default
    return default if value is None else str(value)


def configure(env):
    global TZ_HOURS, TZ, DEMO_LOGIN
    try:
        TZ_HOURS = float(read(env, "TZ_OFFSET_HOURS", "8"))
    except ValueError:
        TZ_HOURS = 8.0
    TZ = timezone(timedelta(hours=TZ_HOURS))
    DEMO_LOGIN = read(env, "DEMO_LOGIN", "false").lower() == "true"
