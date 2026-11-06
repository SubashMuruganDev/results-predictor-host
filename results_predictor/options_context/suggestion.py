"""Illustrative option analysis using only listed, dated chain contracts."""
import math
import pandas as pd
LABEL='ANALYSIS ONLY — not a trade recommendation, no order placed'

def suggest(context, lean, chain=None, event_date=None):
    def na(reason): return f'{LABEL} | N/A: {reason}'
    if not context: return na('options context unavailable')
    if context.get('liquidity')!='OK': return na('liquidity is THIN or unknown')
    if lean not in ('BEAT','MISS'): return na('v2 lean is not BEAT or MISS')
    if not chain: return na('listed chain unavailable')
    records=chain.get('records',chain)
    side='CE' if lean=='BEAT' else 'PE'
    try:
        spot=float(records['underlyingValue']); move=float(context['implied_move_pct'])
        if not math.isfinite(spot) or not math.isfinite(move) or min(spot,move)<=0: return na('spot or implied move unavailable')
        today=pd.Timestamp(pd.Timestamp.now(tz='Asia/Kolkata').date())
        day=max(today,pd.Timestamp(event_date).normalize().tz_localize(None)) if event_date else today
        eligible=[]
        for row in records.get('data',[]):
            expiry=pd.to_datetime(row.get('expiryDate'),errors='coerce')
            if pd.isna(expiry) or expiry.normalize()<day: continue
            strike=float(row['strikePrice'])
            if math.isfinite(strike): eligible.append((expiry,strike,row))
        if not eligible: return na('no eligible listed premium')
        expiry=min(r[0] for r in eligible)
        _,strike,contract=min((r for r in eligible if r[0]==expiry),key=lambda r:(abs(r[1]-spot),r[1]))
        premium=float(contract.get(side,{}).get('lastPrice',0))
        if not math.isfinite(premium) or premium<=0: return na('nearest listed ATM contract has no usable premium')
        stop=spot+(-1 if side=='CE' else 1)*spot*move/200
        return f'{LABEL} | ATM {side} {strike:g}, expiry {expiry.date()}, entry premium {premium:g}; illustrative exit if premium falls 50% to {premium/2:g} OR underlying moves against by 0.5× implied move to {stop:g}.'
    except (KeyError,TypeError,ValueError): return na('incomplete chain or implied move')
