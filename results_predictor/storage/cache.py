"""Persistent JSON cache, isolated by source/mode. Parsed filings never expire."""
import os
import sqlite3
import time
from pathlib import Path
from .db import dumps
import json

TTLS={'universe':86400,'calendar':21600,'fundamentals':86400,'options':900,'filing':None}

class Cache:
    def __init__(self,path=None):
        self.path=Path(path or os.environ.get('RESULTS_PREDICTOR_CACHE','cache/providers.sqlite3'))
        self.path.parent.mkdir(parents=True,exist_ok=True)

    def load(self,key,kind,fetch,refresh=False):
        with sqlite3.connect(self.path) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, at REAL, payload TEXT)')
            row=conn.execute('SELECT at,payload FROM cache WHERE key=?',(key,)).fetchone()
            if row and not refresh and (TTLS[kind] is None or time.time()-row[0]<TTLS[kind]):
                return json.loads(row[1])
            payload=fetch()
            conn.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)',(key,time.time(),dumps(payload)))
            return payload
