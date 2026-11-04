import sqlite3


class Rows(list):
    pass


class Meta:
    def __init__(self, last_row_id):
        self.last_row_id = last_row_id


class Result:
    def __init__(self, rows, last_row_id):
        self.results = Rows(rows)
        self.meta = Meta(last_row_id)


class Statement:
    def __init__(self, conn, sql):
        self.conn = conn
        self.sql = sql
        self.args = ()

    def bind(self, *args):
        for value in args:
            if value is not None and not isinstance(value, (str, int, float)):
                raise TypeError("D1 cannot bind " + repr(value))
        self.args = args
        return self

    def run_now(self):
        cursor = self.conn.execute(self.sql, self.args)
        rows = [dict(row) for row in cursor.fetchall()]
        return Result(rows, cursor.lastrowid)

    async def all(self):
        return self.run_now()

    async def run(self):
        return self.run_now()

    async def first(self):
        rows = self.run_now().results
        return rows[0] if rows else None


class Binding:
    def __init__(self, schema_path):
        self.conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        with open(schema_path, "r", encoding="utf-8") as handle:
            self.conn.executescript(handle.read())

    def prepare(self, sql):
        return Statement(self.conn, sql)

    async def batch(self, statements):
        self.conn.execute("BEGIN")
        try:
            results = [s.run_now() for s in statements]
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")
        return results
