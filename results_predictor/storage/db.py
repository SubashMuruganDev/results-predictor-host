"""SQLite persistence; prediction payloads and their frozen thresholds never mutate."""
import csv
import uuid
import hashlib
import json
import os
import sqlite3
import pandas as pd
from pathlib import Path
from ..fundamentals.normalize import now
from .connect import connect, hosted, transaction

STATES={'PREDICTED','AWAITING_FILING','FILED','PARSED','RECONCILED','SCORED','NO_FILING','PARSE_FAILED','BASIS_MISMATCH'}
ALLOWED={
 'PREDICTED':{'AWAITING_FILING'}, 'AWAITING_FILING':{'FILED','NO_FILING'},
 'NO_FILING':{'AWAITING_FILING'}, 'FILED':{'PARSED','PARSE_FAILED'},
 'PARSE_FAILED':{'AWAITING_FILING'}, 'PARSED':{'RECONCILED','BASIS_MISMATCH','PARSE_FAILED'},
 'BASIS_MISMATCH':{'AWAITING_FILING'}, 'RECONCILED':{'SCORED'}, 'SCORED':set()}

def dumps(value):
    # pandas converts NaN to JSON null, including nested dicts.
    import pandas as pd
    return pd.Series([value]).to_json(orient='values', date_format='iso')[1:-1]

class Database:
    def __init__(self, path=None):
        self.path=Path(path or os.environ.get('RESULTS_PREDICTOR_DB','data/results_predictor.sqlite3'))
        self.hosted=hosted()
        self.conn,self.storage,self.IntegrityError=connect(self.path)
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, kind TEXT, effective_date TEXT, fetched_at TEXT, source TEXT, sha256 TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS calendar(symbol TEXT PRIMARY KEY, payload TEXT);
        CREATE TABLE IF NOT EXISTS predictions(id INTEGER PRIMARY KEY, created_at TEXT, state TEXT, payload TEXT, outcome TEXT);
        CREATE TABLE IF NOT EXISTS transitions(id INTEGER PRIMARY KEY, prediction_id INTEGER, state TEXT, at TEXT, detail TEXT);
        ''')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS predictions_log(
          prediction_id INTEGER PRIMARY KEY REFERENCES predictions(id), run_id TEXT NOT NULL,
          created_at TEXT NOT NULL, symbol TEXT, result_date TEXT, metric TEXT,
          v2_call TEXT, v2_lean TEXT, v2_confidence TEXT, old_E1 REAL, old_call TEXT,
          options_context TEXT, thresholds TEXT, data_timestamps TEXT, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS actuals(id INTEGER PRIMARY KEY, prediction_id INTEGER UNIQUE REFERENCES predictions_log(prediction_id), recorded_at TEXT, actual REAL, reaction_pct REAL, filing TEXT);
        CREATE TABLE IF NOT EXISTS scores(id INTEGER PRIMARY KEY, prediction_id INTEGER REFERENCES predictions_log(prediction_id), actual_id INTEGER REFERENCES actuals(id), model TEXT, metric TEXT, period TEXT, call TEXT, actual_label TEXT, hit INTEGER, UNIQUE(prediction_id,model));
        """)
        self.conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS immutable_predictions_log_replace
        BEFORE INSERT ON predictions_log
        WHEN EXISTS(SELECT 1 FROM predictions_log WHERE prediction_id=NEW.prediction_id)
        BEGIN SELECT RAISE(ABORT, 'append-only table: replacement forbidden'); END;
        """)
        for table in ('predictions_log','actuals','scores'):
            for action in ('UPDATE','DELETE'):
                self.conn.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'append-only table'); END")
        self.conn.commit()
        # Preserve pre-existing forecasts in the new audit trail without re-running models.
        for row in self.conn.execute('SELECT id,created_at,payload FROM predictions WHERE id NOT IN (SELECT prediction_id FROM predictions_log)').fetchall():
            payload=json.loads(row['payload'])
            payload.setdefault('created_at',row['created_at'])
            self.log_prediction(row['id'],payload)

    def close(self):
        self.conn.close()

    def snapshot(self, kind, payload, source, effective_date=None):
        raw=dumps(payload)
        self.conn.execute('INSERT INTO snapshots(kind,effective_date,fetched_at,source,sha256,payload) VALUES(?,?,?,?,?,?)',
                          (kind,effective_date or now()[:10],now(),source,hashlib.sha256(raw.encode()).hexdigest(),raw))
        self.conn.commit()

    def latest_snapshot(self, kind, source=None):
        query='SELECT * FROM snapshots WHERE kind=?'
        args=[kind]
        if source:
            query+=' AND source=?'
            args.append(source)
        row=self.conn.execute(query+' ORDER BY id DESC LIMIT 1',args).fetchone()
        return dict(row, payload=json.loads(row['payload'])) if row else None

    def metadata(self, key, value=None):
        if value is not None:
            self.conn.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',(key,value))
            self.conn.commit()
        row=self.conn.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
        return row[0] if row else None

    def calendar_event(self, symbol):
        row=self.conn.execute('SELECT payload FROM calendar WHERE symbol=?',(symbol,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_event(self, event):
        from dataclasses import asdict
        self.conn.execute('INSERT OR REPLACE INTO calendar VALUES(?,?)',(event.symbol,dumps(asdict(event))))
        self.conn.commit()

    def save_prediction(self, payload):
        payload=dict(payload)
        payload.setdefault('run_id',str(uuid.uuid4()))
        at=pd.Timestamp(payload.get('created_at') or now())
        if at.tzinfo is None: at=at.tz_localize('Asia/Kolkata')
        payload['created_at']=at.tz_convert('Asia/Kolkata').isoformat()
        cur=self.conn.execute('INSERT INTO predictions(created_at,state,payload) VALUES(?,?,?)',(payload['created_at'],'PREDICTED',dumps(payload)))
        ident=cur.lastrowid
        self.conn.execute('INSERT INTO transitions(prediction_id,state,at) VALUES(?,?,?)',(ident,'PREDICTED',now()))
        self.conn.commit()
        self.log_prediction(ident,payload)
        return ident

    def get_prediction(self, ident):
        row=self.conn.execute('SELECT * FROM predictions WHERE id=?',(ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        return dict(row,payload=json.loads(row['payload']),outcome=json.loads(row['outcome']) if row['outcome'] else None)

    def pending(self):
        return [self.get_prediction(r[0]) for r in self.conn.execute("SELECT id FROM predictions WHERE state != 'SCORED'")]

    def transition(self, ident, state, outcome=None, detail=''):
        current=self.get_prediction(ident)['state']
        if state not in ALLOWED[current]:
            raise ValueError(f'invalid transition {current} -> {state}')
        with transaction(self.conn):
            self.conn.execute('UPDATE predictions SET state=?, outcome=COALESCE(?,outcome) WHERE id=?',(state,dumps(outcome) if outcome is not None else None,ident))
            self.conn.execute('INSERT INTO transitions(prediction_id,state,at,detail) VALUES(?,?,?,?)',(ident,state,now(),detail))

    def transitions(self, ident):
        return [r[0] for r in self.conn.execute('SELECT state FROM transitions WHERE prediction_id=? ORDER BY id',(ident,))]

    def counts(self):
        counts={state:0 for state in sorted(STATES)}
        counts.update({r[0]:r[1] for r in self.conn.execute('SELECT state,COUNT(*) FROM predictions GROUP BY state')})
        return counts

    def log_prediction(self, ident, payload):
        payload=dict(payload)
        payload.setdefault('run_id',str(uuid.uuid4()))
        at=pd.Timestamp(payload.get('created_at') or now())
        if at.tzinfo is None: at=at.tz_localize('Asia/Kolkata')
        payload['created_at']=at.tz_convert('Asia/Kolkata').isoformat()
        payload.setdefault('old_call',None)
        values=(ident,payload['run_id'],payload['created_at'],payload.get('symbol'),payload.get('result_date'),payload.get('metric'),payload.get('v2_call'),payload.get('v2_lean'),payload.get('v2_confidence'),payload.get('E1'),payload.get('old_call'),dumps(payload.get('options_context')),dumps({k:v for k,v in payload.items() if 'threshold' in k}),dumps(payload.get('data_timestamps',{})),dumps(payload))
        with transaction(self.conn):
            self.conn.execute('INSERT INTO predictions_log VALUES('+','.join('?' for _ in values)+')',values)
        if self.hosted: return
        path=Path(os.environ.get('RESULTS_PREDICTOR_REPORTS','reports'))/'predictions_log.csv'
        path.parent.mkdir(parents=True,exist_ok=True)
        # Cross-process lock prevents interleaved CSV rows/header writes on Linux hosting.
        import fcntl
        with path.open('a+',newline='') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX)
            stream.seek(0,2)
            writer=csv.writer(stream)
            if stream.tell()==0: writer.writerow(['database','prediction_id','run_id','created_at','symbol','result_date','metric','v2_call','v2_lean','v2_confidence','old_E1','old_call','options_context','thresholds','data_timestamps','payload'])
            writer.writerow([str(self.path.resolve()),*values])

    def record_actual(self, ident, actual, label):
        p=self.get_prediction(ident)['payload']
        with transaction(self.conn):
            cur=self.conn.execute('INSERT OR IGNORE INTO actuals(prediction_id,recorded_at,actual,reaction_pct,filing) VALUES(?,?,?,?,?)',(ident,now(),actual['actual'],actual.get('reaction_pct'),dumps(actual)))
            if not cur.rowcount: return
            for model,call in [('v2',p.get('v2_call')),('old',p.get('old_call'))]:
                hit=None if call not in ('BEAT','MISS') or label=='PENDING' else int(call==label)
                self.conn.execute('INSERT INTO scores(prediction_id,actual_id,model,metric,period,call,actual_label,hit) VALUES(?,?,?,?,?,?,?,?)',(ident,cur.lastrowid,model,p.get('metric'),p.get('result_date','')[:7],call,label,hit))

    def scoreboard(self):
        return [dict(r) for r in self.conn.execute("SELECT period,metric,model,COUNT(*) AS observations,COUNT(hit) AS called,SUM(hit) AS hits,AVG(hit) AS hit_rate FROM scores GROUP BY period,metric,model ORDER BY period,metric,model")]

    def report(self):
        rows=[]
        for r in self.conn.execute('SELECT l.prediction_id,l.payload,p.state,a.actual,a.reaction_pct FROM predictions_log l JOIN predictions p ON p.id=l.prediction_id LEFT JOIN actuals a ON a.prediction_id=l.prediction_id ORDER BY l.prediction_id DESC'):
            rows.append(dict(json.loads(r['payload']),prediction_id=r['prediction_id'],state=r['state'],actual=r['actual'],reaction_pct=r['reaction_pct']))
        return dict(rows=rows,scoreboard=self.scoreboard())

    BACKUP_TABLES=('predictions','transitions','predictions_log','actuals','scores','snapshots','calendar','metadata')

    @staticmethod
    def _backup_hash(tables):
        return hashlib.sha256(json.dumps(tables,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

    def export_backup(self):
        tables={table:[dict(row) for row in self.conn.execute(f'SELECT * FROM {table} ORDER BY 1')] for table in self.BACKUP_TABLES}
        return dict(version=1,exported_at=now(),tables=tables,sha256=self._backup_hash(tables))

    def import_backup(self, payload, *, replace=False):
        if replace: raise ValueError('append-only backup import does not allow replace')
        tables=payload.get('tables',{})
        if payload.get('sha256')!=self._backup_hash(tables):
            raise ValueError('backup hash mismatch')
        if payload.get('version')!=1 or not isinstance(tables,dict) or set(tables)-set(self.BACKUP_TABLES):
            raise ValueError('unsupported backup format')
        existing={r[0] for r in self.conn.execute('SELECT prediction_id FROM predictions_log')}
        imported=0
        with transaction(self.conn):
            for table in self.BACKUP_TABLES:
                allowed={r[1] for r in self.conn.execute(f'PRAGMA table_info({table})')}
                for row in tables.get(table,[]):
                    if not isinstance(row,dict) or not row or set(row)-allowed:
                        raise ValueError('invalid backup columns')
                    ident=row.get('id') if table=='predictions' else row.get('prediction_id')
                    if ident in existing: continue
                    keys=list(row)
                    # Plain ledger INSERT preserves duplicate rejection; auxiliary tables may already exist.
                    key='prediction_id' if table=='predictions_log' else ('key' if table=='metadata' else 'symbol' if table=='calendar' else 'id')
                    if self.conn.execute(f'SELECT 1 FROM {table} WHERE {key}=?',(row.get(key),)).fetchone(): continue
                    self.conn.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",[row[k] for k in keys])
                    imported+=1
        return dict(ok=True,imported=imported)
