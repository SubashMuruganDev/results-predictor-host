import json
import sqlite3
import threading
import pytest
from results_predictor.storage.db import Database


def test_immutable_log_and_actuals(tmp_path):
    db=Database(tmp_path/'db')
    ident=db.save_prediction(dict(symbol='OIL',metric='sales',result_date='2020-01-01',basis='consolidated',E1=100,v2_call='BEAT',v2_lean='BEAT',v2_confidence='HIGH',old_call='MISS'))
    assert db.conn.execute('SELECT prediction_id FROM predictions_log').fetchone()[0]==ident
    for op in ('UPDATE predictions_log SET symbol="X"','DELETE FROM predictions_log'):
        with pytest.raises(sqlite3.IntegrityError): db.conn.execute(op)
    from results_predictor.scoring.auto import reconcile
    reconcile(db,ident,dict(actual=110,basis='consolidated',reaction_pct=2))
    assert db.conn.execute('SELECT reaction_pct FROM actuals').fetchone()[0]==2
    assert len(db.scoreboard())==2
    assert db.report()['rows'][0]['actual']==110
    db.close()


def test_persistent_cache(tmp_path):
    from results_predictor.providers.nse import NSEProvider
    p=NSEProvider(True,cache_path=tmp_path/'cache')
    first=p.financials('OIL')
    other=NSEProvider(True,cache_path=tmp_path/'cache')
    other.fixture=lambda name: pytest.fail('fresh cache must skip source')
    assert len(other.financials('OIL'))==len(first)


def test_suggestion_listed_strike():
    from results_predictor.options_context.suggestion import suggest
    context=dict(liquidity='OK',implied_move_pct=4)
    chain={'records':{'underlyingValue':103,'data':[{'strikePrice':105,'expiryDate':'2099-01-01','CE':{'lastPrice':5}}]}}
    line=suggest(context,'BEAT',chain,'2026-11-12')
    assert '105' in line and 'CE' in line and 'ANALYSIS ONLY' in line
    assert 'N/A' in suggest(dict(liquidity='THIN'),'BEAT',chain)


def test_offline_server(tmp_path,monkeypatch):
    import socket
    # Permit loopback only while retaining the suite's external-network prohibition.
    original_connect=socket.socket.connect
    def local_connect(sock,address):
        if address[0] not in ('127.0.0.1','::1'): return original_connect(sock,address)
        return _connect(sock,address)
    monkeypatch.setattr(socket.socket,'connect',local_connect)
    monkeypatch.setattr(socket,'create_connection',_create_connection)
    from results_predictor.web import make_server
    server=make_server(port=0,db_path=tmp_path/'db')
    worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
    import http.client
    def get(path,body=None):
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
        conn.request('POST' if body else 'GET',path,body=json.dumps(body) if body else None,headers={'Content-Type':'application/json'})
        response=conn.getresponse(); data=response.read(); conn.close(); assert response.status==200; return data
    try:
        assert b'Analysis only' in get('/')
        assert b'Analysis only' in get('/report')
        scan=json.loads(get('/api/scan',dict(start='2026-11-09',end='2026-11-13',offline=True)))
        assert scan['rows'] and scan['coverage_gaps']
        report=json.loads(get('/api/report'))
        assert len(report['rows'])==len(scan['rows'])
        assert b'symbol' in get('/api/export')
        scored=json.loads(get('/api/score',dict(offline=True)))
        assert len(scored['rows'])==len(scan['rows'])
    finally: server.shutdown(); server.server_close(); worker.join()

# Capture before the autouse fixture replaces it.
import socket
_connect=socket.socket.connect


def test_proxy_call_and_ist_log(tmp_path):
    from results_predictor.features import load_panel
    from results_predictor.prediction.v2 import predict
    from pathlib import Path
    row=predict(load_panel(Path('data/sample_fundamentals.csv')),'OIL','2026-11-12','2026-11-05T22:26')[0]
    assert row['old_call'] in ('BEAT','MISS','NO CALL')
    db=Database(tmp_path/'db')
    ident=db.save_prediction(row)
    assert db.conn.execute('SELECT created_at FROM predictions_log WHERE prediction_id=?',(ident,)).fetchone()[0].endswith('+05:30')
    db.close()


def test_cache_expiry_and_refresh(tmp_path,monkeypatch):
    from results_predictor.storage.cache import Cache
    import results_predictor.storage.cache as module
    clock=[1000]; monkeypatch.setattr(module.time,'time',lambda:clock[0])
    cache=Cache(tmp_path/'cache'); calls=[]
    def fetch(): calls.append(1); return len(calls)
    assert cache.load('chain','options',fetch)==1
    assert cache.load('chain','options',fetch)==1
    clock[0]+=901
    assert cache.load('chain','options',fetch)==2
    assert cache.load('chain','options',fetch,True)==3
    assert cache.load('filing','filing',fetch)==4
    clock[0]+=10**8
    assert cache.load('filing','filing',fetch)==4

_create_connection=socket.create_connection


def test_suggestion_does_not_replace_missing_atm_with_far_strike():
    from results_predictor.options_context.suggestion import suggest
    chain={'records':{'underlyingValue':103,'data':[
        {'strikePrice':105,'expiryDate':'2099-01-01','CE':{}},
        {'strikePrice':120,'expiryDate':'2099-01-01','CE':{'lastPrice':5}}]}}
    assert 'N/A' in suggest(dict(liquidity='OK',implied_move_pct=4),'BEAT',chain)


def test_audit_csv_and_actuals_immutable(tmp_path,monkeypatch):
    import csv
    from results_predictor.scoring.auto import reconcile
    monkeypatch.setenv('RESULTS_PREDICTOR_REPORTS',str(tmp_path/'reports'))
    db=Database(tmp_path/'db')
    p=dict(symbol='X',metric='sales',basis='consolidated',result_date='2020-01-01',E1=100,threshold=.05,v2_call='BEAT',old_call='MISS')
    ident=db.save_prediction(p)
    original=db.conn.execute('SELECT payload FROM predictions_log').fetchone()[0]
    reconcile(db,ident,dict(actual=110,basis='consolidated'))
    reconcile(db,ident,dict(actual=90,basis='consolidated'))
    assert db.conn.execute('SELECT payload FROM predictions_log').fetchone()[0]==original
    assert db.conn.execute('SELECT actual,reaction_pct FROM actuals').fetchone()[:]==(110,None)
    for table in ('actuals','scores'):
        for sql in (f'DELETE FROM {table}',f'UPDATE {table} SET prediction_id=prediction_id'):
            with pytest.raises(sqlite3.IntegrityError): db.conn.execute(sql)
    with (tmp_path/'reports'/'predictions_log.csv').open() as f:
        rows=list(csv.DictReader(f))
    assert len(rows)==1 and json.loads(rows[0]['thresholds'])=={'threshold':.05}
    assert db.scoreboard()[0]['model']=='old'
    assert {r['model']:r['hit_rate'] for r in db.scoreboard()}=={'old':0,'v2':1}
    db.close()


def test_embedded_script_preserves_csv_newline_escape():
    from results_predictor.web import HTML
    script=HTML.split('<script>')[1].split('</script>')[0]
    assert ".join('\\r\\n')" in script
    assert '\r' not in script


def test_log_rejects_insert_or_replace(tmp_path):
    db=Database(tmp_path/'db')
    db.save_prediction(dict(symbol='ORIGINAL'))
    columns=[r[1] for r in db.conn.execute('PRAGMA table_info(predictions_log)')]
    expressions=["'REPLACED'" if c=='symbol' else c for c in columns]
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute('INSERT OR REPLACE INTO predictions_log SELECT '+','.join(expressions)+' FROM predictions_log')
    db.close()
