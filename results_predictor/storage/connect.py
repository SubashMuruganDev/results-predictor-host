"""Stateless remote ledger connections; local SQLite is for development only."""
import os
import re
import sqlite3
from contextlib import contextmanager


def hosted():
    return os.environ.get('RESULTS_PREDICTOR_HOSTED', '').lower() in ('1', 'true', 'yes') or 'RENDER' in os.environ


class Row(tuple):
    def __new__(cls, cursor, values):
        row = super().__new__(cls, values)
        row.columns = [column[0] for column in cursor.description]
        return row

    def keys(self):
        return self.columns

    def __getitem__(self, key):
        return super().__getitem__(self.columns.index(key) if isinstance(key, str) else key)


class Guard:
    """Reject ledger rewrites in Python as well as with database triggers."""
    def __init__(self, raw, integrity_error):
        self.raw = raw
        self.IntegrityError = integrity_error

    def check(self, sql):
        clean = re.sub(r'/\*.*?\*/|--[^\n]*', ' ', sql, flags=re.S)
        clean = re.sub(r'["`\[\]]', '', clean)
        ledger = r'(?:\w+\.)?(?:predictions_log|actuals|scores)\b'
        if re.search(r'\b(?:UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM|REPLACE\s+INTO|INSERT\s+OR\s+REPLACE\s+INTO)\s+' + ledger, clean, re.I):
            raise self.IntegrityError('append-only table: UPDATE/DELETE/replacement forbidden')

    def execute(self, sql, parameters=()):
        self.check(sql)
        return self.raw.execute(sql, parameters)

    def executemany(self, sql, parameters):
        self.check(sql)
        return self.raw.executemany(sql, parameters)

    def executescript(self, sql):
        self.check(sql)
        return self.raw.executescript(sql)

    def cursor(self):
        return Guard(self.raw.cursor(), self.IntegrityError)

    def __iter__(self):
        return iter(self.raw)

    def __getattr__(self, name):
        return getattr(self.raw, name)


@contextmanager
def transaction(conn):
    try:
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def connect(path):
    url, token = os.environ.get('TURSO_DATABASE_URL'), os.environ.get('TURSO_AUTH_TOKEN')
    if hosted() and not (url and token):
        raise RuntimeError('Hosted storage requires TURSO_DATABASE_URL and TURSO_AUTH_TOKEN; local SQLite fallback is disabled')
    if url and token:
        import turso_serverless
        raw = turso_serverless.connect(url, auth_token=token)
        error = turso_serverless.IntegrityError
        storage = 'turso'
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(path)
        error = sqlite3.IntegrityError
        storage = 'local'
    raw.row_factory = Row
    return Guard(raw, error), storage, error
