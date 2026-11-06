"""Portable terminal tables and full-fidelity CSV reports."""
from pathlib import Path
import json
import pandas as pd

def table(rows,columns=None):
    frame=pd.DataFrame(rows)
    if frame.empty:
        print('No rows available.')
    else:
        print(frame[[c for c in (columns or frame.columns) if c in frame.columns]].to_string(index=False))
    return frame

def csv_report(rows,path,columns=None):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    frame=pd.DataFrame(rows)
    if frame.empty and len(frame.columns)==0:
        frame=pd.DataFrame(columns=columns or ['symbol','result_date','quarter_end','metric','basis','E1','threshold','v2_expectation','v2_call','reason'])
    for column in frame.columns:
        if frame[column].map(lambda v:isinstance(v,(dict,list))).any():
            frame[column]=frame[column].map(lambda v:json.dumps(v,default=str) if isinstance(v,(dict,list)) else v)
    frame.to_csv(path,index=False)
    return path

def coverage(gaps):
    if gaps:
        print('\nCOVERAGE GAP')
        for gap in dict.fromkeys(gaps): print('- '+gap)
