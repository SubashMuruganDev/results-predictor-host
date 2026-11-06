"""Neutral option-chain context. No directional signal is produced."""
import json
from pathlib import Path
import pandas as pd
from ..schemas import OptionsContext
from ..fundamentals.normalize import number

def read_snapshot(path):
    path=Path(path)
    if path.suffix.lower()=='.csv':
        df=pd.read_csv(path)
        if df.empty or 'symbol' not in df or df.symbol.nunique()!=1:
            raise ValueError('CSV option snapshot must contain exactly one symbol')
        rows=[]
        for _,r in df.iterrows():
            row=dict(strikePrice=r.get('strikePrice'),expiryDate=r.get('expiryDate'))
            for side in ('CE','PE'):
                row[side]={key:r.get(side+'_'+key) for key in ('lastPrice','openInterest','totalTradedVolume','impliedVolatility')}
            rows.append(row)
        return {'symbol':str(df.iloc[0]['symbol']),'records':{'timestamp':df.iloc[0].get('timestamp'),'underlyingValue':df.iloc[0].get('underlyingValue'),'data':rows}}
    return json.loads(path.read_text())

def build_context(chain, as_of, historical_moves=None, in_ban=False, event_date=None):
    result=OptionsContext()
    records=chain.get('records',chain)
    spot=number(records.get('underlyingValue'))
    today=pd.Timestamp(as_of).normalize().tz_localize(None)
    event=pd.Timestamp(event_date).normalize().tz_localize(None) if event_date else today
    eligible=[]
    for r in records.get('data',[]):
        expiry=pd.to_datetime(r.get('expiryDate'),errors='coerce')
        strike=number(r.get('strikePrice'))
        if pd.notna(expiry) and expiry.normalize()>=max(today,event) and strike is not None:
            eligible.append((expiry.normalize(),strike,r))
    if eligible and spot and spot>0:
        expiry=min(x[0] for x in eligible)
        same=[x for x in eligible if x[0]==expiry]
        _,_,atm=min(same,key=lambda x:abs(x[1]-spot))
        ce,pe=atm.get('CE',{}),atm.get('PE',{})
        call,put=number(ce.get('lastPrice')),number(pe.get('lastPrice'))
        if call is not None and put is not None and call>0 and put>0:
            result.implied_move_pct=100*(call+put)/spot
        iv=[number(s.get('impliedVolatility')) for s in (ce,pe)]
        if all(v is not None and v>0 for v in iv): result.atm_iv_pct=sum(iv)/2
        coi=[number(x[2].get('CE',{}).get('openInterest')) for x in same]
        poi=[number(x[2].get('PE',{}).get('openInterest')) for x in same]
        if all(x is not None for x in coi+poi) and sum(coi)>0: result.pcr_oi=sum(poi)/sum(coi)
        result.expiry_dte=(expiry-today).days
        # Descriptive quality rule, not an exchange liquidity standard.
        liquid=all((number(s.get('openInterest')) or 0)>=100 and (number(s.get('totalTradedVolume')) or 0)>=100 for s in (ce,pe))
        result.liquidity='OK' if liquid else 'THIN'
    history=[abs(v) for v in (number(x) for x in (historical_moves or [])) if v is not None]
    result.hist_n=len(history)
    if history: result.hist_abs_move_pct=sum(history)/len(history)
    if result.implied_move_pct is not None and result.hist_abs_move_pct is not None:
        if result.implied_move_pct>result.hist_abs_move_pct: result.flags.append('IMPLIED_MOVE_ABOVE_HISTORICAL')
        if result.implied_move_pct<result.hist_abs_move_pct: result.flags.append('IMPLIED_MOVE_BELOW_HISTORICAL')
    if result.liquidity=='THIN': result.flags.append('THIN_LIQUIDITY')
    if result.expiry_dte is not None and result.expiry_dte<=5: result.flags.append('NEAR_EXPIRY_PHYSICAL_SETTLEMENT')
    if in_ban: result.flags.append('IN_BAN_PERIOD')
    return result

def broker_context(*args,**kwargs):
    raise NotImplementedError('Broker options mode is a stub; use public or a local snapshot. No broker credentials are accepted.')
