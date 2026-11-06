"""Preserve v2 forecasts and add the documented transparent old-model proxy."""
import numpy as np
import pandas as pd
from ..predict import predict as _predict
from ..features import history, target_quarter, shift, value, truth, yoy_change


def predict(panel,symbol,result_date,cutoff=None):
    rows=_predict(panel,symbol,result_date,cutoff)
    q=target_quarter(result_date)
    at=pd.Timestamp(cutoff) if cutoff is not None else min(pd.Timestamp.now(tz='Asia/Kolkata'),pd.Timestamp(result_date).tz_localize('Asia/Kolkata'))
    if at.tzinfo is None: at=at.tz_localize('Asia/Kolkata')
    h=history(panel,symbol.upper(),q,at)
    fin=truth(h[max(h)].get('is_financial',False)) if h else False
    for row in rows:
        changes=[]
        for lag in range(1,5):
            a,b=h.get(shift(q,-lag)),h.get(shift(q,-lag-4))
            if a is None or b is None: continue
            av,bv=value(a,row['metric'],fin),value(b,row['metric'],fin)
            delta=yoy_change(av,bv,'sales') if row['metric']=='sales' else av-bv
            if np.isfinite(delta): changes.append((lag,delta))
        call='NO CALL'
        if len(changes)>=3 and changes[0][0]==1 and np.isfinite(row['E1']):
            delta=changes[0][1]-float(np.median([v for _,v in changes]))
            call='BEAT' if delta>0 else 'MISS' if delta<0 else 'NO CALL'
        row.update(old_call=call,old_call_method='proxy: latest YoY change vs median of last four YoY changes; >=3 required')
    return rows
