"""A fake asyncpg connection for the Postgres store: records SQL, answers the store's queries.

Used by the unit tests that fake the store below the SQL layer (no database).
"""

import contextlib


class FakeRecord(dict):
    """asyncpg.Record stand-in: mapping access by column name."""


class FakeConn:
    def __init__(self, records=(), *, lock_ok=True, reject_ids=(), scan_run_id=41):
        self.records = [FakeRecord(record) for record in records]
        self.lock_ok = lock_ok
        self.reject_ids = set(reject_ids)
        self.scan_run_id = scan_run_id
        self.calls = []
        self.closed = False
        self.transactions = 0
        self.updates = []  # (sql, args) of every UPDATE of a post table

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        if "current_user" in sql:
            return "riviu_scanner"
        if "pg_try_advisory_lock" in sql:
            return self.lock_ok
        if "pg_advisory_unlock" in sql:
            return True
        if "count(*)" in sql:
            return 0
        if sql.startswith("UPDATE public.scan_run"):
            return args[0]
        if sql.startswith("INSERT INTO public.scan_run"):
            return self.scan_run_id
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        if sql.startswith("UPDATE public."):
            self.updates.append((sql, args))
            return [FakeRecord(id=row_id) for row_id in args[0] if row_id not in self.reject_ids]
        if "AS deleted" in sql:
            return [FakeRecord(id=row_id, link_air="https://edited.example", deleted=False, khoa_chi_so=False) for row_id in args[0]]
        if "channel_override" in sql:
            return []
        return list(self.records)

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "OK"

    @contextlib.asynccontextmanager
    async def _transaction(self):
        self.transactions += 1
        yield

    def transaction(self, **_kwargs):
        return self._transaction()

    def is_closed(self):
        return self.closed

    async def close(self):
        self.closed = True


class Events:
    def __init__(self):
        self.logs = []
        self.statuses = []

    async def broadcast_log(self, message, **_kwargs):
        self.logs.append(message)

    async def broadcast_status(self, status):
        self.statuses.append(status)

    async def broadcast_data(self, _row):
        pass

    async def broadcast_duplicates(self, _data):
        pass


class NoPlaywright:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return False
