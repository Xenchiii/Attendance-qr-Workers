import json
from datetime import datetime, timezone

try:
    from js import JSON, null
    from pyodide.ffi import to_js
    ON_WORKER = True
except ImportError:
    JSON = null = to_js = None
    ON_WORKER = False

STAMP = "%Y-%m-%d %H:%M:%S"
_binding = None


class IntegrityError(Exception):
    pass


def use(binding):
    global _binding
    _binding = binding


def now():
    return datetime.now(timezone.utc).strftime(STAMP)


def stamp(value):
    return value.strftime(STAMP)


def iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    return str(value).replace(" ", "T")[:19] + "Z"


def _arg(value):
    return null if value is None and ON_WORKER else value


def _plain(value):
    if value is None:
        return None
    if ON_WORKER:
        text = JSON.stringify(value)
        return None if text is None else json.loads(text)
    return value


def _fail(error):
    if "constraint" in str(error).lower():
        raise IntegrityError(str(error)) from error
    raise error


def _statement(sql, args):
    return _binding.prepare(sql).bind(*[_arg(a) for a in args])


async def query(sql, *args):
    try:
        result = await _statement(sql, args).all()
    except IntegrityError:
        raise
    except Exception as error:
        _fail(error)
    return [dict(row) for row in (_plain(result.results) or [])]


async def one(sql, *args):
    rows = await query(sql, *args)
    return rows[0] if rows else None


async def execute(sql, *args):
    try:
        result = await _statement(sql, args).run()
    except Exception as error:
        _fail(error)
    return result.meta.last_row_id


async def batch(statements):
    prepared = [_statement(sql, args) for sql, args in statements]
    try:
        await _binding.batch(to_js(prepared) if ON_WORKER else prepared)
    except Exception as error:
        _fail(error)
