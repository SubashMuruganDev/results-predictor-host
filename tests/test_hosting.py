import socket
_connect=socket.socket.connect
_create_connection=socket.create_connection
import copy
import pytest
from results_predictor.storage.db import Database


def test_hosted_requires_remote(tmp_path, monkeypatch):
    monkeypatch.setenv('RESULTS_PREDICTOR_HOSTED','yes')
    monkeypatch.delenv('TURSO_DATABASE_URL',raising=False)
    monkeypatch.delenv('TURSO_AUTH_TOKEN',raising=False)
    with pytest.raises(RuntimeError,match='TURSO_DATABASE_URL'):
        Database(tmp_path/'db')
    assert not (tmp_path/'db').exists()


def test_backup_roundtrip(tmp_path):
    source=Database(tmp_path/'source')
    ident=source.save_prediction(dict(symbol='X',metric='sales',result_date='2020-01-01',v2_call='BEAT'))
    source.record_actual(ident,dict(actual=110),'BEAT')
    backup=source.export_backup()
    target=Database(tmp_path/'target')
    target.import_backup(backup)
    target.import_backup(backup)
    assert target.report()==source.report()
    assert target.export_backup()['tables']==backup['tables']
    tampered=copy.deepcopy(backup)
    tampered['tables']['predictions_log'][0]['symbol']='BAD'
    with pytest.raises(ValueError,match='hash'):
        target.import_backup(tampered)
    with pytest.raises(ValueError,match='append-only'):
        target.import_backup(backup,replace=True)
    source.close(); target.close()


@pytest.fixture
def http_api(tmp_path, monkeypatch):
    import socket
    import threading
    import http.client
    import json
    for key in ('RESULTS_PREDICTOR_HOSTED','TURSO_DATABASE_URL','TURSO_AUTH_TOKEN','RESULTS_PREDICTOR_WRITE_TOKEN','RESULTS_PREDICTOR_PUBLIC_READ','RENDER'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(socket.socket, 'connect', _connect)
    monkeypatch.setattr(socket, 'create_connection', _create_connection)
    from results_predictor.web import make_server
    server=make_server(port=0,db_path=tmp_path/'http-db')
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    def request(path, body=None, headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
        conn.request('GET' if body is None else 'POST',path,
                     body=None if body is None else json.dumps(body),
                     headers={'Content-Type':'application/json',**(headers or {})})
        response=conn.getresponse()
        raw=response.read()
        conn.close()
        return response.status,json.loads(raw) if 'application/json' in response.getheader('Content-Type', '') else raw.decode()
    yield request
    server.shutdown(); server.server_close(); worker.join()


def test_http_security_health_backup(http_api,monkeypatch):
    monkeypatch.setenv('RESULTS_PREDICTOR_WRITE_TOKEN','test-token')
    body=dict(start='2026-11-09',end='2026-11-13',offline=True,options=False)
    assert http_api('/api/scan',body)[0]==401
    headers={'X-Write-Token':'test-token'}
    assert http_api('/api/scan',body,headers)[0]==200
    assert http_api('/api/scan',dict(body,end='2026-12-10'),headers)[0]==400
    status,health=http_api('/health')
    assert status==200 and health==dict(ok=True,storage='local',hosted=False,db_ok=True)
    status,backup=http_api('/api/backup')
    assert status==200 and backup['tables']['predictions_log']
    assert http_api('/api/backup/import',backup)[0]==401
    assert http_api('/api/backup/import',backup,headers)[0]==200
    monkeypatch.setenv('RESULTS_PREDICTOR_PUBLIC_READ','0')
    assert http_api('/api/report')[0]==401
    assert http_api('/api/backup',headers=headers)[0]==200


def test_rate_limit(http_api):
    # Invalid requests also consume the quota, avoiding expensive validation abuse.
    for _ in range(10): assert http_api('/api/scan',{})[0]==400
    assert http_api('/api/score',{})[0]==429


def test_live_failure_is_503(http_api,monkeypatch):
    import results_predictor.web as web
    def failed(*args,**kwargs): raise RuntimeError('provider unavailable')
    monkeypatch.setattr(web,'scan',failed)
    status,body=http_api('/api/scan',dict(start='2026-11-09',end='2026-11-13',offline=False))
    assert status==503 and body['coverage_gaps'] and not body.get('rows')


def test_python_append_only_without_triggers(tmp_path):
    db=Database(tmp_path/'guards')
    db.save_prediction(dict(symbol='X'))
    for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
        db.conn.execute('DROP TRIGGER '+row[0])
    for table in ('predictions_log','actuals','scores'):
        with pytest.raises(db.IntegrityError): db.conn.execute('DELETE FROM '+table)
        with pytest.raises(db.IntegrityError): db.conn.cursor().execute('UPDATE '+table+' SET prediction_id=1')
    with pytest.raises(db.IntegrityError): db.log_prediction(1,dict(symbol='Y'))
    db.close()


def test_remote_adapter_without_context_manager(tmp_path,monkeypatch):
    import sqlite3
    import sys
    from types import SimpleNamespace
    class Remote:
        # Emulates DB-API HTTP driver with no __enter__/__exit__.
        def __init__(self): self.raw=sqlite3.connect(':memory:')
        @property
        def row_factory(self): return self.raw.row_factory
        @row_factory.setter
        def row_factory(self,value): self.raw.row_factory=value
        def __getattr__(self,name): return getattr(self.raw,name)
    def connect(url,*,auth_token):
        assert url=='https://example.invalid' and auth_token=='fake'
        return Remote()
    monkeypatch.setitem(sys.modules,'turso_serverless',SimpleNamespace(connect=connect,IntegrityError=sqlite3.IntegrityError))
    monkeypatch.setenv('RESULTS_PREDICTOR_HOSTED','1')
    monkeypatch.setenv('TURSO_DATABASE_URL','https://example.invalid')
    monkeypatch.setenv('TURSO_AUTH_TOKEN','fake')
    path=tmp_path/'must-not-exist'
    db=Database(path)
    ident=db.save_prediction(dict(symbol='X',metric='sales'))
    db.transition(ident,'AWAITING_FILING')
    db.record_actual(ident,dict(actual=123),'BEAT')
    assert db.storage=='turso' and db.report()['rows'][0]['actual']==123
    assert not path.exists()
    db.close()


def test_ledger_rejects_upsert_and_ignore(tmp_path):
    db=Database(tmp_path/'upsert')
    db.save_prediction(dict(symbol='X'))
    with pytest.raises(db.IntegrityError):
        db.conn.execute('INSERT OR IGNORE INTO predictions_log SELECT * FROM predictions_log')
    with pytest.raises(db.IntegrityError):
        db.conn.execute("INSERT INTO predictions_log SELECT * FROM predictions_log WHERE true ON CONFLICT(prediction_id) DO UPDATE SET symbol='Y'")
    db.close()


def test_async_scan_authenticated(http_api, monkeypatch):
    import time
    monkeypatch.setenv('RESULTS_PREDICTOR_WRITE_TOKEN', 'test-token')
    body = dict(start='2026-11-09', end='2026-11-13', offline=True, options=False, **{'async': True})
    status, error = http_api('/api/scan', body)
    assert status == 401 and error['error'] == 'Write token required'
    headers = {'X-Write-Token': 'test-token'}
    status, job = http_api('/api/scan', body, headers)
    assert status == 202 and job['status'] == 'running'
    path = '/api/jobs/' + job['job_id']
    assert http_api(path)[0] == 401
    deadline = time.monotonic() + 120
    while True:
        status, result = http_api(path, headers=headers)
        assert status == 200
        if result['status'] != 'running':
            break
        assert time.monotonic() < deadline
        time.sleep(.05)
    assert result['status'] == 'done'
    assert result['rows'] and result['events'] and result['offline']
    assert http_api('/api/jobs/missing', headers=headers)[0] == 404
    assert http_api('/api/scan', dict(body, end='bad'), headers)[0] == 400


def test_async_failure(http_api, monkeypatch):
    import time
    import results_predictor.web as web
    monkeypatch.delenv('RESULTS_PREDICTOR_WRITE_TOKEN', raising=False)
    monkeypatch.delenv('RESULTS_PREDICTOR_PUBLIC_READ', raising=False)
    def failed(*args, **kwargs):
        raise RuntimeError('provider unavailable')
    monkeypatch.setattr(web, 'scan', failed)
    status, job = http_api('/api/scan', dict(start='2026-11-09', end='2026-11-13', offline=False, **{'async': True}))
    assert status == 202
    deadline = time.monotonic() + 5
    while True:
        status, result = http_api('/api/jobs/' + job['job_id'])
        if result['status'] != 'running':
            break
        assert time.monotonic() < deadline
        time.sleep(.01)
    assert result['status'] == 'error' and result['error'] == 'Data unavailable'


def test_token_inside_scan_form(http_api, monkeypatch):
    from html.parser import HTMLParser
    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.form = False
            self.token_in_form = False
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'form': self.form = attrs.get('id') == 'form'
            if attrs.get('id') == 'writeToken': self.token_in_form = self.form
        def handle_endtag(self, tag):
            if tag == 'form': self.form = False
    monkeypatch.setenv('RESULTS_PREDICTOR_WRITE_TOKEN', 'test-token')
    status, page = http_api('/')
    parser = Elements()
    parser.feed(page)
    assert status == 200 and parser.token_in_form
    monkeypatch.delenv('RESULTS_PREDICTOR_WRITE_TOKEN')
    assert 'id="writeToken"' not in http_api('/')[1]
