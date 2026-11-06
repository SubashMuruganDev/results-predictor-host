"""Discover financial-results meetings in the dated F&O equity universe."""
from collections import defaultdict
from datetime import date
from .resolver import resolve
from ..fundamentals.normalize import now
import pandas as pd

def date_range(text):
    try:
        a,b=text.split(':')
        start,end=date.fromisoformat(a),date.fromisoformat(b)
        if start>end:
            raise ValueError('start follows end')
        return start,end
    except ValueError as exc:
        raise ValueError('date range must be YYYY-MM-DD:YYYY-MM-DD with start <= end') from exc

def scan(provider,db,start,end):
    universe=set(provider.universe(db))
    primary=provider.meetings(start,end)
    secondary=provider.secondary(start,end)
    groups=defaultdict(lambda: {'nse':[],'secondary':[],'cancelled':False,'filed':False})
    for source,rows in [('nse',primary),('secondary',secondary)]:
        for row in rows:
            symbol=str(row.get('symbol') or row.get('bm_symbol') or '').upper()
            purpose=' '.join(str(row.get(k,'')) for k in ('purpose','bm_purpose','bm_desc')).lower()
            if symbol not in universe or not any(w in purpose for w in ('financial result','quarterly result','financial statement','results')):
                continue
            raw=row.get('date') or row.get('meetingDate') or row.get('bm_date')
            day=pd.to_datetime(raw,errors='coerce',dayfirst=not str(raw).startswith('20'))
            if pd.isna(day) or not start<=day.date()<=end:
                continue
            group=groups[symbol]
            group[source].append(day.date().isoformat())
            group['cancelled'] |= str(row.get('status','')).upper()=='CANCELLED'
            group['filed'] |= str(row.get('status','')).upper()=='FILED'
    events=[]
    for symbol,g in sorted(groups.items()):
        previous=db.calendar_event(symbol)
        e=resolve(symbol,g['nse'],g['secondary'],previous=previous['result_date'] if previous else None,cancelled=g['cancelled'],filed=g['filed'])
        e.fetched_at=now()
        db.save_event(e)
        events.append(e)
    db.metadata('last_calendar_scan',now())
    db.metadata('last_calendar_mode','offline fixture' if provider.offline else 'online')
    return events
