"""Results-season orchestration, kept separate from the frozen model pipeline."""
from dataclasses import asdict
from datetime import datetime
import json
import uuid
from .options_context.suggestion import suggest
from pathlib import Path
import platform
import sqlite3
import pandas as pd
import requests
from .calendar.scanner import date_range, scan
from .fundamentals.normalize import normalize, panel_from_rows, now
from .providers.nse import NSEProvider, FIXTURES
from .providers.http import StopNetwork
from .storage.db import Database
from .prediction.v2 import predict
from .reports import table, csv_report, coverage

ERRORS=(requests.RequestException,StopNetwork,ValueError,OSError)

def run(a):
    db=None
    provider=NSEProvider(a.offline,refresh=getattr(a,"refresh",False))
    gaps=provider.gaps
    if a.offline: gaps.append('OFFLINE DEMO: calendar/universe/options fixtures; sample fundamentals are not live coverage')
    try:
        db=Database(a.db)
        if a.cmd=='status':
            print('Database:',db.path.resolve())
            print('Predictions by state:',json.dumps(db.counts(),sort_keys=True))
            snapshot=db.latest_snapshot('universe')
            age=(pd.Timestamp.now(tz='UTC')-pd.Timestamp(snapshot['fetched_at'])).total_seconds()/3600 if snapshot else None
            print('Universe snapshot age (hours):',round(age,2) if age is not None else 'missing')
            if snapshot: print('Universe source:',snapshot['source'],'effective_date:',snapshot['effective_date'],'sha256:',snapshot['sha256'])
            print('Last calendar scan:',db.metadata('last_calendar_scan') or 'never',db.metadata('last_calendar_mode') or '')
        elif a.cmd=='doctor':
            print('Python:',platform.python_version())
            print('pandas:',pd.__version__,'requests:',requests.__version__)
            # A real SQLite write verifies permissions even when the DB already exists.
            db.metadata('doctor_last_write',now())
            print('Database writable:',db.path.resolve())
            required=['board_meetings.json','underlyings.json','financial_results.json','option_chain.json','ban_list.csv']
            missing=[p for p in required if not (FIXTURES/p).is_file()]
            print('Fixtures:', 'missing '+', '.join(missing) if missing else 'present')
            if not a.offline:
                # Exactly ONE homepage request: no retries, no API warmup, no bypass.
                response=requests.Session().get('https://www.nseindia.com',timeout=(5,20),allow_redirects=False,headers={'User-Agent':'Mozilla/5.0 (results_predictor doctor)'})
                print('NSE reachability: HTTP',response.status_code)
                if response.status_code>=400: gaps.append('NSE homepage is not accessible')
            else: print('Network: skipped (--offline)')
            return 1 if missing or (not a.offline and response.status_code>=400) else 0
        elif a.cmd=='score':
            from .scoring.auto import score_auto
            print('Predictions by state:',json.dumps(score_auto(db,provider),sort_keys=True))
        else:
            start,end=date_range(a.date_range)
            events=scan(provider,db,start,end)
            if a.cmd=='calendar':
                rows=[asdict(e) for e in events]
                table(rows)
                print('CSV:',csv_report(rows,Path('reports')/f'calendar_{start}_{end}.csv',columns=['symbol','result_date','status','source','fetched_at','previous_date','secondary_date']))
                return 0
            rows=predict_events(a,db,provider,events,gaps)
            table(rows,['symbol','result_date','metric','basis','E1','v2_expectation','v2_call','v2_confidence','reason'])
            report=Path('reports')/f'predictions_{start}_{end}_{datetime.now():%Y%m%dT%H%M%S%f}.csv'
            print('CSV:',csv_report(rows,report))
            if a.out: csv_report(rows,a.out)
        return 0
    except (NotImplementedError,sqlite3.Error)+ERRORS as exc:
        gaps.append(str(exc))
        return 1
    finally:
        coverage(gaps)
        if db is not None: db.close()


def predict_events(a,db,provider,events,gaps):
    rows=[]
    run_id=str(uuid.uuid4())
    if a.options_mode=='broker':
        from .options_context import broker_context
        broker_context()
    for event in events:
        if event.status in ('CANCELLED','FILED','DATE_CONFLICT','SECONDARY_ONLY'):
            gaps.append(f'{event.symbol}: {event.status}; prediction skipped')
            continue
        cutoff=a.cutoff or min(pd.Timestamp.now(tz='Asia/Kolkata'),pd.Timestamp(event.result_date).tz_localize('Asia/Kolkata'))
        try:
            if a.offline:
                raw=pd.read_csv(a.data).to_dict('records')
                for r in raw: r['source']='offline sample fundamentals'
                financials=normalize(raw)
                cutoff=a.cutoff or min(pd.Timestamp.now(tz='Asia/Kolkata'),pd.Timestamp(event.result_date).tz_localize('Asia/Kolkata'))
                panel=panel_from_rows(financials,cutoff=cutoff)
            else:
                financials=provider.financials(event.symbol)
                if not financials and a.screener:
                    from .providers.screener import fetch
                    financials=fetch(event.symbol,provider.client)
                    gaps.append(f'{event.symbol}: Screener has no verified publication times; data excluded from point-in-time calls')
                cutoff=a.cutoff or min(pd.Timestamp.now(tz='Asia/Kolkata'),pd.Timestamp(event.result_date).tz_localize('Asia/Kolkata'))
                panel=panel_from_rows(financials,cutoff=cutoff)
                if not financials: gaps.append(f'{event.symbol}: fundamentals unavailable; NO CALL')
        except ERRORS as exc:
            gaps.append(f'{event.symbol}: fundamentals unavailable: {exc}')
            panel={}
            financials=[]
        db.snapshot('fundamentals:'+event.symbol,financials,'offline sample' if a.offline else 'NSE/Screener')
        prediction_rows=predict(panel,event.symbol,event.result_date,a.cutoff)
        context=None
        chain=None
        if a.options_mode!='none':
            try:
                from .options_context import build_context,read_snapshot
                if a.offline and a.options_mode=='public':
                    raise ValueError('public options unavailable offline; choose snapshot')
                if a.options_mode=='snapshot':
                    chain=read_snapshot(a.options_snapshot or FIXTURES/'option_chain.json')
                    chain_symbol=chain.get('symbol') or chain.get('records',{}).get('symbol')
                    if chain_symbol!=event.symbol:
                        raise ValueError(f'option snapshot symbol {chain_symbol!r} does not match {event.symbol}')
                else: chain=provider.option_chain(event.symbol)
                banned=None
                try: banned=provider.bans()
                except ERRORS as exc: gaps.append(f'Ban list unavailable: {exc}')
                quote=chain.get('records',chain).get('timestamp')
                quote_stamp=pd.to_datetime(quote,errors='coerce')
                if pd.isna(quote_stamp):
                    raise ValueError('option chain quote timestamp missing or invalid')
                if quote_stamp.tzinfo is None: quote_stamp=quote_stamp.tz_localize('Asia/Kolkata')
                age=(pd.Timestamp.now(tz='Asia/Kolkata')-quote_stamp).total_seconds()
                if age < -300 or age > 86400:
                    raise ValueError(f'option snapshot stale or from the future: {quote}')
                context=asdict(build_context(chain,pd.Timestamp.now(tz='Asia/Kolkata').date(),in_ban=banned is not None and event.symbol in banned,event_date=event.result_date))
                context.update(source=str(a.options_snapshot or FIXTURES/'option_chain.json') if a.options_mode=='snapshot' else 'NSE option-chain API',quote_timestamp=str(quote),fetched_at=now())
                gaps.append('Historical event price moves unavailable; hist_abs_move_pct missing and hist_n=0')
            except ERRORS as exc:
                gaps.append(f'{event.symbol}: options unavailable: {exc}')
        if context is not None:
            print(event.symbol+' options context: '+json.dumps(context))
        for row in prediction_rows:
            row.update(run_id=run_id,offline=a.offline,calendar_status=event.status,created_at=pd.Timestamp.now(tz='Asia/Kolkata').isoformat(),options_context=context, data_timestamps={'cutoff':str(cutoff),'calendar_fetched_at':event.fetched_at,'fundamentals':[{k:str(r.get(k)) for k in ('quarter_end','filing_timestamp','available_at','availability_ts','fetched_at','source')} for r in financials]}, analysis=suggest(context,row.get('v2_lean'),chain,event.result_date))
            ident=db.save_prediction(row)
            row['prediction_id']=ident
            rows.append(row)
    return rows
