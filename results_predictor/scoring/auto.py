"""Audited filing reconciliation, independently for each prediction metric."""
import math
import pandas as pd
import requests
from ..providers.http import StopNetwork
from ..features import family, truth, value
from ..fundamentals.normalize import now

def reconcile(db,ident,actual=None,parse_error=None):
    stored=db.get_prediction(ident)
    if stored['state']=='SCORED': return
    if stored['state'] in ('PREDICTED','NO_FILING','PARSE_FAILED','BASIS_MISMATCH'):
        db.transition(ident,'AWAITING_FILING')
    if actual is None:
        if db.get_prediction(ident)['state']=='AWAITING_FILING':
            db.transition(ident,'NO_FILING',detail='No target-quarter filing available')
        return
    if db.get_prediction(ident)['state']=='AWAITING_FILING':
        db.transition(ident,'FILED',outcome={'filing':actual})
    if parse_error:
        db.transition(ident,'PARSE_FAILED',detail=parse_error)
        return
    if db.get_prediction(ident)['state']=='FILED':
        db.transition(ident,'PARSED')
    payload=stored['payload']
    if family(actual.get('basis'))!=family(payload['basis']):
        db.transition(ident,'BASIS_MISMATCH',detail='Prediction and filing accounting families differ')
        return
    number=actual.get('actual')
    try:
        valid=math.isfinite(float(number))
    except (ValueError,TypeError):
        valid=False
    if not valid:
        db.transition(ident,'PARSE_FAILED',detail='Metric missing/non-finite in filing')
        return
    if db.get_prediction(ident)['state']=='PARSED':
        db.transition(ident,'RECONCILED')
    e1=payload.get('E1')
    label='PENDING' if e1 is None else 'BEAT' if number>e1 else 'MISS'
    hit=None if label=='PENDING' or payload['v2_call']=='NO CALL' else payload['v2_call']==label
    db.record_actual(ident,actual,label)
    db.transition(ident,'SCORED',outcome=dict(actual=number,actual_label=label,v2_hit=hit,E1=e1,threshold=payload.get('threshold'),filing=actual))

def score_auto(db,provider):
    cached={}
    current=pd.Timestamp.now(tz='Asia/Kolkata').date()
    for stored in db.pending():
        p=stored['payload']
        if pd.Timestamp(p['result_date']).date()>current:
            continue
        # Offline predictions cannot silently become live observations (and vice versa).
        if bool(p.get('offline',False))!=provider.offline:
            continue
        symbol=p['symbol']
        if symbol not in cached:
            try:
                cached[symbol]=provider.financials(symbol)
            except (requests.RequestException,StopNetwork,ValueError,OSError) as exc:
                provider.gaps.append(f'{symbol}: scoring fetch unavailable: {exc}')
                cached[symbol]=None
        if cached[symbol] is None:
            continue
        rows=[r for r in cached[symbol] if str(pd.Timestamp(r['quarter_end']).date())==p['quarter_end']
              and pd.notna(r['availability_ts']) and r['availability_ts']<=pd.Timestamp.now(tz='Asia/Kolkata')]
        if not rows:
            reconcile(db,stored['id'])
            continue
        matching=[r for r in rows if family(r['basis'])==family(p['basis'])]
        candidates=matching or rows
        usable=[r for r in candidates if pd.notna(value(r,p['metric'],truth(r.get('is_financial',False))))]
        row=max(usable or candidates,key=lambda r:r['availability_ts'])
        metric=value(row,p['metric'],truth(row.get('is_financial',False)))
        actual={k:str(row.get(k) or '') for k in ('basis','source','filing_timestamp','available_at','fetched_at')}
        actual['actual']=metric
        actual['reaction_pct']=row.get('reaction_pct')
        reconcile(db,stored['id'],actual)
    return db.counts()
