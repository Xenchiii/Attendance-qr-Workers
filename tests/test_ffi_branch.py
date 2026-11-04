import importlib.util
import json
import os
import sys
import types

import pytest

from d1shim import Binding

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
SCHEMA = os.path.join(os.path.dirname(SRC), "schema.sql")
NULL = object()


class StrictStatement:
    def __init__(self, inner):
        self.inner = inner

    def bind(self, *args):
        for value in args:
            if value is None:
                raise TypeError("D1_TYPE_ERROR: Type 'undefined' not supported")
        self.inner.bind(*[None if v is NULL else v for v in args])
        return self

    async def all(self):
        result = self.inner.run_now()
        result.results = types.SimpleNamespace(rows=list(result.results))
        return result

    async def run(self):
        return self.inner.run_now()


class StrictBinding:
    def __init__(self):
        self.inner = Binding(SCHEMA)

    def prepare(self, sql):
        return StrictStatement(self.inner.prepare(sql))

    async def batch(self, statements):
        return await self.inner.batch([s.inner for s in statements])


@pytest.fixture
def worker_dbx(monkeypatch):
    js = types.ModuleType("js")
    js.null = NULL
    js.JSON = types.SimpleNamespace(stringify=lambda v: json.dumps(v.rows if hasattr(v, "rows") else v))
    ffi = types.ModuleType("pyodide.ffi")
    ffi.to_js = lambda value, **kw: value
    pyodide = types.ModuleType("pyodide")
    monkeypatch.setitem(sys.modules, "js", js)
    monkeypatch.setitem(sys.modules, "pyodide", pyodide)
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi)
    spec = importlib.util.spec_from_file_location("dbx_worker", os.path.join(SRC, "dbx.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.use(StrictBinding())
    return module


async def test_none_becomes_null_and_rows_are_plain(worker_dbx):
    assert worker_dbx.ON_WORKER
    new_id = await worker_dbx.execute(
        "INSERT INTO users (role, employee_no, first_name, last_name, email, password_hash, last_login) VALUES (?, ?, ?, ?, ?, ?, ?)",
        "staff", "Z-1", "Zed", "Zee", "z@x.ph", "h", None,
    )
    assert new_id > 0
    row = await worker_dbx.one("SELECT first_name, last_login FROM users WHERE id = ?", new_id)
    assert row == {"first_name": "Zed", "last_login": None}
    await worker_dbx.batch([("UPDATE users SET first_name = ? WHERE id = ?", ("Zoe", new_id))])
    assert (await worker_dbx.one("SELECT first_name FROM users"))["first_name"] == "Zoe"


async def test_constraint_errors_become_integrity_errors(worker_dbx):
    sql = "INSERT INTO users (role, employee_no, first_name, last_name, email, password_hash) VALUES ('staff', 'Q', 'a', 'b', 'q@x', 'h')"
    await worker_dbx.execute(sql)
    with pytest.raises(worker_dbx.IntegrityError):
        await worker_dbx.execute(sql)
