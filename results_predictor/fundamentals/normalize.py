"""Explicit field mapping; unsupported financial data stays missing."""
import math
import re
from datetime import datetime, timezone
import pandas as pd
from ..features import stamp, family

def now():
    return datetime.now(timezone.utc).isoformat()

def number(x):
    try:
        v=float(str(x).replace(',',''))
        return v if math.isfinite(v) else None
    except (ValueError,TypeError):
        return None

def records(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ('data','results'):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []

def exchange_date(value):
    iso=bool(re.match(r'^\d{4}-',str(value)))
    return pd.to_datetime(value,errors='coerce',dayfirst=not iso)

def normalize(payload, symbol=None, fetched_at=None):
    out=[]
    for item in records(payload):
        basis = str(item.get('basis',item.get('consolidated',item.get('consolidatedStatus','')))).lower()
        basis = {'c':'consolidated','s':'standalone','con':'consolidated','non-consolidated':'standalone'}.get(basis,basis)
        basis=family(basis)
        if basis not in ('standalone','consolidated'):
            continue
        quarter=item.get('quarter_end') or item.get('toDate') or item.get('periodEnded')
        q=exchange_date(quarter)
        if pd.isna(q):
            continue
        if item.get('fromDate'):
            begin=exchange_date(item['fromDate'])
            if pd.isna(begin) or not 75 <= (q-begin).days <= 100:
                continue
        filed=item.get('filing_timestamp') or item.get('broadcastDate') or item.get('filingDate')
        available=item.get('available_at') or item.get('availability_ts') or filed
        row=dict(symbol=str(item.get('symbol') or symbol or '').upper(),quarter_end=q.normalize(),basis=basis,
                 filing_timestamp=filed,available_at=available,availability_ts=stamp(exchange_date(available)),
                 fetched_at=fetched_at or item.get('fetched_at') or now(),source=item.get('source','NSE'),is_financial=item.get('is_financial',False))
        fields={'revenue':('revenue','revenueFromOperations','re_net_sale'), 'pat':('pat','netProfit','re_net_profit'),
                'ebitda':('ebitda',), 'other_income':('other_income','otherIncome'), 'pbt':('pbt','profitBeforeTax'),
                'operating_profit_before_provisions':('operating_profit_before_provisions',)}
        for name, aliases in fields.items():
            row[name]=next((number(item[a]) for a in aliases if item.get(a) is not None),None)
            if row[name] is None:
                row[name]=float('nan')
        row['reaction_pct']=number(item.get('reaction_pct'))
        out.append(row)
    return out

def panel_from_rows(rows, cutoff=None):
    """Pick one family per symbol before making the legacy quarter-keyed panel."""
    cutoff=stamp(cutoff) if cutoff is not None else pd.Timestamp.now(tz="Asia/Kolkata")
    panel={}
    for symbol in sorted({r['symbol'] for r in rows}):
        group=[r for r in rows if r['symbol']==symbol and pd.notna(r['availability_ts']) and r['availability_ts']<cutoff]
        if not group:
            continue
        # Prefer consolidated consistently, never overwrite a quarter with another family.
        basis='consolidated' if any(r['basis']=='consolidated' for r in group) else 'standalone'
        selected={}
        for row in sorted(group,key=lambda r:r['availability_ts']):
            if row['basis']==basis:
                selected[row['quarter_end']]=row
        panel[symbol]=list(selected.values())
    return panel
