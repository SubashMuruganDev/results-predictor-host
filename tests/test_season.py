import json
import socket
from pathlib import Path
import pytest

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


def test_resolver():
    from results_predictor.calendar.resolver import resolve
    assert resolve('A', ['2026-11-10'], ['2026-11-11']).status == 'DATE_CONFLICT'
    assert resolve('A', [], ['2026-11-11']).status == 'SECONDARY_ONLY'
    assert resolve('A', ['2026-11-10'], [], previous='2026-11-09').status == 'RESCHEDULED'
    assert resolve('A', ['2026-11-10'], [], cancelled=True).status == 'CANCELLED'
    assert resolve('A', ['2026-11-10'], [], filed=True).status == 'FILED'
    assert resolve('A', ['2026-11-10'], []).status == 'CONFIRMED_NSE'


def test_universe():
    from results_predictor.providers.nse import equity_symbols
    assert equity_symbols({'data': {'UnderlyingList': [{'symbol':'ABC'}, {'symbol':'NIFTY','instrument':'FUTIDX'}]}}) == ['ABC']


def test_normalize():
    from results_predictor.fundamentals.normalize import normalize, panel_from_rows
    rows = normalize([{'symbol':'A','quarter_end':'2026-06-30','basis':b,'revenue':v,'available_at':'2026-07-20'} for b,v in [('standalone',1),('consolidated',2)]])
    assert len(rows) == 2
    assert panel_from_rows(rows)['A'][0]['revenue'] == 2


@pytest.mark.parametrize('status',[403,429])
def test_stop_429(status):
    from results_predictor.providers.http import PoliteClient, StopNetwork
    class Response:
        status_code=status
        headers={'Retry-After':'0'}
    class Session:
        calls=0
        def get(self,*a,**kw):
            self.calls+=1
            assert kw['timeout'] == (5,20)
            return Response()
    session=Session()
    client=PoliteClient(session=session, sleep=lambda _:None)
    with pytest.raises(StopNetwork): client.get('https://example.org/a')
    with pytest.raises(StopNetwork): client.get('https://example.org/b')
    assert session.calls == 3


def test_options():
    from results_predictor.options_context import build_context
    c=build_context({'records':{'underlyingValue':100,'data':[{'strikePrice':100,'expiryDate':'24-Nov-2026','CE':{'lastPrice':3,'openInterest':100,'totalTradedVolume':100,'impliedVolatility':20},'PE':{'lastPrice':2,'openInterest':120,'totalTradedVolume':100,'impliedVolatility':22}}]}}, '2026-11-10')
    assert c.signal is None and c.implied_move_pct == 5
    assert c.expiry_dte == 14


def test_offline_cli(tmp_path, capsys):
    from results_predictor.__main__ import main
    out=tmp_path/'pred.csv'
    assert main(['predict','--date-range','2026-11-09:2026-11-13','--offline','--db',str(tmp_path/'db.sqlite'),'--out',str(out)]) == 0
    import pandas as pd
    d=pd.read_csv(out)
    assert len(d) == 24 and set(d.symbol) >= {'OIL','TATAPOWER'}
    assert 'COVERAGE GAP' in capsys.readouterr().out


def test_scoring_state_machine(tmp_path):
    from results_predictor.storage.db import Database
    from results_predictor.scoring.auto import reconcile
    db=Database(tmp_path/'db')
    ident=db.save_prediction({'symbol':'A','quarter_end':'2026-09-30','result_date':'2026-11-10','metric':'sales','basis':'consolidated','E1':100,'v2_call':'BEAT','threshold':.05})
    reconcile(db, ident, {'basis':'consolidated','actual':110})
    assert db.get_prediction(ident)['state'] == 'SCORED'
    assert db.get_prediction(ident)['outcome']['v2_hit'] is True
    assert db.transitions(ident) == ['PREDICTED','AWAITING_FILING','FILED','PARSED','RECONCILED','SCORED']


def test_scoring_exceptions_and_frozen_values(tmp_path):
    from results_predictor.storage.db import Database
    from results_predictor.scoring.auto import reconcile
    from results_predictor.model_v2 import LOCKED
    db=Database(tmp_path/'scores.db')
    payload=dict(symbol='A',quarter_end='2026-09-30',result_date='2026-11-10',metric='sales',basis='consolidated',E1=100,v2_call='BEAT',threshold=.05)
    ident=db.save_prediction(payload)
    reconcile(db,ident)
    assert db.get_prediction(ident)['state']=='NO_FILING'
    reconcile(db,ident,{'basis':'standalone','actual':110})
    assert db.get_prediction(ident)['state']=='BASIS_MISMATCH'
    reconcile(db,ident,{'basis':'consolidated','actual':None})
    assert db.get_prediction(ident)['state']=='PARSE_FAILED'
    # Runtime changes must not change stored thresholds or expectation.
    payload['threshold']=9
    payload['E1']=200
    reconcile(db,ident,{'basis':'consolidated','actual':110})
    stored=db.get_prediction(ident)
    assert stored['outcome']['threshold']==.05
    assert stored['outcome']['E1']==100
    assert stored['outcome']['v2_hit'] is True
    with pytest.raises(ValueError): db.transition(ident,'FILED')
    db.close()


def test_options_missing_is_not_zero():
    from results_predictor.options_context import build_context
    context=build_context({},'2026-11-10',in_ban=True)
    assert context.signal is None
    assert context.implied_move_pct is None and context.pcr_oi is None
    assert context.hist_n==0 and context.hist_abs_move_pct is None
    assert set(context.flags)=={'THIN_LIQUIDITY','IN_BAN_PERIOD'}


def test_xbrl_context_and_units():
    from results_predictor.fundamentals.xbrl import parse_xbrl
    xml=b'''<xbrl><context id="c"><period><startDate>2026-04-01</startDate><endDate>2026-06-30</endDate></period><scenario><explicitMember>ConsolidatedMember</explicitMember></scenario></context><unit id="inr"><measure>iso4217:INR</measure></unit><RevenueFromOperations contextRef="c" unitRef="inr">1000000000</RevenueFromOperations><ProfitLossForPeriod contextRef="c" unitRef="inr">100000000</ProfitLossForPeriod></xbrl>'''
    rows=parse_xbrl(xml,'A',{'filingDate':'2026-07-20'})
    assert len(rows)==1 and rows[0]['revenue']==100
    assert rows[0]['pat']==10 and rows[0]['basis']=='consolidated'
    assert rows[0]['available_at']=='2026-07-20'


def test_doctor_one_request(tmp_path,monkeypatch,capsys):
    import requests
    from results_predictor.__main__ import main
    calls=[]
    def get(self,url,**kwargs):
        calls.append(url)
        assert kwargs['allow_redirects'] is False
        return type('Response',(),{'status_code':200})()
    monkeypatch.setattr(requests.Session,'get',get)
    assert main(['doctor','--db',str(tmp_path/'db')])==0
    assert calls==['https://www.nseindia.com']
    calls.clear()
    assert main(['doctor','--offline','--db',str(tmp_path/'db')])==0
    assert not calls


def test_snapshot_cache_and_hash(tmp_path):
    import hashlib
    from results_predictor.storage.db import Database
    from results_predictor.providers.nse import NSEProvider
    db=Database(tmp_path/'db')
    p=NSEProvider(offline=True)
    first=p.universe(db)
    p.fixture=lambda name: (_ for _ in ()).throw(AssertionError('fresh cache should be used'))
    assert p.universe(db)==first
    row=db.conn.execute('SELECT * FROM snapshots').fetchone()
    assert hashlib.sha256(row['payload'].encode()).hexdigest()==row['sha256']
    assert row['effective_date'] and row['fetched_at'] and row['source']=='offline fixture'
    db.close()


def test_scanner_filtering(tmp_path):
    from datetime import date
    from results_predictor.calendar.scanner import scan
    from results_predictor.providers.nse import NSEProvider
    from results_predictor.storage.db import Database
    db=Database(tmp_path/'db')
    events=scan(NSEProvider(offline=True),db,date(2026,11,9),date(2026,11,13))
    assert len(events)==8
    assert all(e.status=='CONFIRMED_NSE' for e in events)
    assert next(e for e in events if e.symbol=='OIL').result_date=='2026-11-12'
    assert db.metadata('last_calendar_scan')
    db.close()


def test_panel_point_in_time_revisions_and_basis():
    from results_predictor.fundamentals.normalize import normalize,panel_from_rows
    rows=normalize([
        dict(symbol='A',quarter_end='2026-06-30',basis='standalone',revenue=100,available_at='2026-07-20'),
        dict(symbol='A',quarter_end='2026-06-30',basis='standalone',revenue=200,available_at='2026-11-20'),
        dict(symbol='A',quarter_end='2026-06-30',basis='consolidated',revenue=300,available_at='2026-11-21')])
    panel=panel_from_rows(rows,cutoff='2026-11-05')
    assert panel['A'][0]['basis']=='standalone'
    assert panel['A'][0]['revenue']==100


def test_xbrl_rejects_segment_facts():
    from results_predictor.fundamentals.xbrl import parse_xbrl
    periods='<period><startDate>2026-04-01</startDate><endDate>2026-06-30</endDate></period>'
    contexts=f'<context id="total">{periods}</context><context id="segment">{periods}<scenario><explicitMember dimension="SegmentAxis">SteelMember</explicitMember></scenario></context>'
    total='<RevenueFromOperations contextRef="total" unitRef="inr">1000000000</RevenueFromOperations>'
    segment='<RevenueFromOperations contextRef="segment" unitRef="inr">500000000</RevenueFromOperations>'
    for facts in [total+segment,segment+total]:
        xml=f'<xbrl>{contexts}<unit id="inr"><measure>iso4217:INR</measure></unit>{facts}</xbrl>'.encode()
        rows=parse_xbrl(xml,'A',{'basis':'consolidated','filingDate':'2026-07-20'})
        assert rows[0]['revenue']==100


def test_metric_specific_filings_and_provider_failure(tmp_path):
    import requests
    from results_predictor.storage.db import Database
    from results_predictor.scoring.auto import score_auto
    from results_predictor.fundamentals.normalize import normalize
    db=Database(tmp_path/'db')
    ids={}
    for symbol,metric in [('FAIL','sales'),('A','sales'),('A','pat')]:
        ids[symbol,metric]=db.save_prediction(dict(symbol=symbol,metric=metric,quarter_end='2020-09-30',result_date='2020-11-10',basis='consolidated',E1=10,v2_call='BEAT',threshold=.05))
    class Provider:
        offline=False
        gaps=[]
        def financials(self,symbol):
            if symbol=='FAIL': raise requests.Timeout('demo timeout')
            return normalize([dict(symbol='A',quarter_end='2020-09-30',basis='consolidated',revenue=100,available_at='2020-11-10'),dict(symbol='A',quarter_end='2020-09-30',basis='consolidated',pat=20,available_at='2020-11-11')])
    provider=Provider()
    score_auto(db,provider)
    assert db.get_prediction(ids['A','sales'])['state']=='SCORED'
    assert db.get_prediction(ids['A','pat'])['state']=='SCORED'
    assert provider.gaps and db.get_prediction(ids['FAIL','sales'])['state']=='PREDICTED'
    db.close()


def test_csv_options_symbol_and_event_expiry(tmp_path):
    from results_predictor.options_context import read_snapshot,build_context
    path=tmp_path/'chain.csv'
    path.write_text('symbol,timestamp,underlyingValue,strikePrice,expiryDate,CE_lastPrice,PE_lastPrice\nA,2026-11-05T12:00:00+05:30,100,100,10-Nov-2026,2,2\nA,2026-11-05T12:00:00+05:30,100,100,24-Nov-2026,3,3\n')
    chain=read_snapshot(path)
    assert chain['symbol']=='A'
    context=build_context(chain,'2026-11-05',event_date='2026-11-12')
    assert context.expiry_dte==19 and context.implied_move_pct==6


def test_normalize_exchange_dates_and_latest_revision():
    from results_predictor.fundamentals.normalize import normalize,panel_from_rows
    rows=normalize([dict(symbol='A',toDate='30-06-2026',consolidated='Consolidated',revenue=2,filingDate='05-11-2026 12:00:00'),dict(symbol='A',toDate='30-06-2026',consolidated='Consolidated',revenue=1,filingDate='20-07-2026 12:00:00')])
    assert rows[0]['availability_ts'].month==11
    panel=panel_from_rows(rows,cutoff='2026-11-06')
    assert panel['A'][0]['revenue']==2


def test_doctor_bad_db_path(tmp_path,capsys):
    from results_predictor.__main__ import main
    blocked=tmp_path/'file'
    blocked.write_text('not a directory')
    assert main(['doctor','--offline','--db',str(blocked/'db')])==1
    assert 'COVERAGE GAP' in capsys.readouterr().out


def test_score_resumes_after_filed(tmp_path):
    from results_predictor.storage.db import Database
    from results_predictor.scoring.auto import reconcile
    db=Database(tmp_path/'db')
    ident=db.save_prediction(dict(symbol='A',quarter_end='2020-09-30',result_date='2020-11-10',metric='sales',basis='consolidated',E1=100,v2_call='BEAT',threshold=.05))
    db.transition(ident,'AWAITING_FILING')
    db.transition(ident,'FILED')
    reconcile(db,ident,{'basis':'consolidated','actual':110})
    assert db.get_prediction(ident)['state']=='SCORED'
    db.close()


def test_season_offline_preserves_model_output(tmp_path):
    import math
    import pandas as pd
    from results_predictor.__main__ import main
    from results_predictor.features import load_panel
    from results_predictor.predict import predict
    data=Path(__file__).resolve().parents[1]/'data'
    cutoff='2026-11-05T22:26'
    out=tmp_path/'predictions.csv'
    main(['predict','--date-range','2026-11-09:2026-11-13','--offline','--cutoff',cutoff,'--db',str(tmp_path/'db'),'--out',str(out)])
    panel=load_panel(data/'sample_fundamentals.csv')
    for row in pd.read_csv(out).to_dict('records'):
        expected=next(r for r in predict(panel,row['symbol'],row['result_date'],cutoff) if r['metric']==row['metric'])
        assert row['basis']==expected['basis']
        assert row['reason']==expected['reason']
        assert row['v2_call']==expected['v2_call']
        assert (pd.isna(row['E1']) and pd.isna(expected['E1'])) or math.isclose(row['E1'],expected['E1'])


def test_retry_after_is_honored():
    from results_predictor.providers.http import PoliteClient
    calls=[]
    waits=[]
    class Response:
        def __init__(self,status): self.status_code=status; self.headers={'Retry-After':'9'}
        def raise_for_status(self): pass
    class Session:
        def get(self,*a,**kw):
            calls.append(a)
            return Response(429 if len(calls)==1 else 200)
    assert PoliteClient(session=Session(),sleep=waits.append).get('https://retry.example/a').status_code==200
    assert len(calls)==2 and 9 in waits


def test_screener_uses_no_estimated_availability():
    import pandas as pd
    from results_predictor.fetch import parse_screener_quarters
    html='<section id="quarters"><table><tr><th></th><th>Jun 2026</th></tr><tr><td>Sales +</td><td>1,000</td></tr><tr><td>Net Profit +</td><td>100</td></tr></table></section>'
    rows=parse_screener_quarters(html,'A','consolidated')
    assert rows[0]['revenue']==1000
    assert pd.isna(rows[0]['availability_ts'])


def test_options_snapshot_cli_provenance(tmp_path,monkeypatch,capsys):
    from datetime import datetime
    import pandas as pd
    from results_predictor.__main__ import main
    from results_predictor.providers.nse import NSEProvider
    # Use only OIL, with a timestamp relative to this test, so it stays hermetic.
    monkeypatch.setattr(NSEProvider,'meetings',lambda self,a,b:[dict(symbol='OIL',date='2026-11-12',purpose='Financial Results')])
    timestamp=pd.Timestamp.now(tz='Asia/Kolkata').isoformat()
    expiry=(pd.Timestamp.now()+pd.Timedelta(days=365)).date().isoformat()
    path=tmp_path/'chain.csv'
    path.write_text(f'symbol,timestamp,underlyingValue,strikePrice,expiryDate,CE_lastPrice,PE_lastPrice\nOIL,{timestamp},100,100,{expiry},3,3\n')
    out=tmp_path/'out.csv'
    args=['predict','--date-range','2026-11-09:2026-11-13','--offline','--options-mode','snapshot','--options-snapshot',str(path),'--db',str(tmp_path/'db'),'--out',str(out)]
    assert main(args)==0
    saved=json.loads(pd.read_csv(out).iloc[0].options_context)
    assert saved['signal'] is None and saved['quote_timestamp']==timestamp
    assert saved['source']==str(path)
    path.write_text(path.read_text().replace(timestamp,'2020-01-01T12:00:00+05:30'))
    assert main(args)==0
    assert 'stale' in capsys.readouterr().out
    assert pd.isna(pd.read_csv(out).iloc[0].options_context)
