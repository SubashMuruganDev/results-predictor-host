"""Results-season CLI, with legacy symbol/file prediction and CSV scoring."""
import argparse
import sys
from pathlib import Path
import pandas as pd
from .features import load_panel
from .prediction.v2 import predict

DEFAULT_DATA=Path(__file__).resolve().parent.parent/'data'/'sample_fundamentals.csv'

def main(argv=None):
    ap=argparse.ArgumentParser(prog='results_predictor',description=__doc__)
    ap.add_argument('--refresh',action='store_true',help='Force fresh provider data')
    sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('predict',help='Discover results or predict legacy explicit symbols')
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--symbol'); g.add_argument('--file',type=Path); g.add_argument('--date-range')
    p.add_argument('--date')
    p.add_argument('--data',type=Path,default=DEFAULT_DATA)
    p.add_argument('--cutoff')
    p.add_argument('--out',type=Path)
    p.add_argument('--options-mode',choices=['none','public','snapshot','broker'],default='none')
    p.add_argument('--options-snapshot',type=Path)
    p.add_argument('--screener',action='store_true')
    c=sub.add_parser('calendar',help='Discover financial-results meetings')
    c.add_argument('--date-range',required=True)
    s=sub.add_parser('score',help='Score stored predictions or legacy CSV pairs')
    s.add_argument('--auto',action='store_true')
    s.add_argument('predictions',nargs='?',type=Path)
    s.add_argument('actuals',nargs='?',type=Path)
    web=sub.add_parser('serve',help='Launch the browser UI')
    web.add_argument('--host',default='127.0.0.1')
    import os
    web.add_argument('--port',type=int,default=int(os.environ.get('PORT','8765')))
    web.add_argument('--db',type=Path)
    sub.add_parser('scoreboard',help='Stored hit rates by month, metric and model')
    sub.add_parser('status')
    sub.add_parser('doctor')
    f=sub.add_parser('fetch',help='Legacy explicit Screener fetch (opt-in)')
    f.add_argument('symbols',nargs='+')
    f.add_argument('--out',type=Path,default=Path('data/fundamentals.csv'))
    f.add_argument('--refresh',action='store_true',default=argparse.SUPPRESS)
    for parser in (p,c,s,sub.choices['status'],sub.choices['doctor'],sub.choices['scoreboard']):
        parser.add_argument('--offline',action='store_true')
        parser.add_argument('--db',type=Path)
    for parser in (p,c,s,web,sub.choices['scoreboard']):
        parser.add_argument('--refresh',action='store_true',default=argparse.SUPPRESS)
    a=ap.parse_args(argv)
    pd.set_option('display.width',200)
    if a.cmd=='serve':
        from .web import make_server
        server=make_server(a.host,a.port,a.db,refresh=a.refresh)
        print(f'Analysis only UI: http://{a.host}:{server.server_port}',flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()
        return 0
    if a.cmd=='scoreboard':
        from .storage.db import Database
        from .reports import table
        db=Database(a.db)
        try: table(db.scoreboard())
        finally: db.close()
        return 0
    if a.cmd=='predict' and not a.date_range:
        if not a.date: ap.error('legacy --symbol/--file requires --date')
        panel=load_panel(a.data)
        syms=[a.symbol] if a.symbol else a.file.read_text().split()
        rows=[r for symbol in syms for r in predict(panel,symbol,a.date,a.cutoff)]
        from .storage.db import Database
        from uuid import uuid4
        from .options_context.suggestion import suggest
        db=Database(a.db)
        run_id=str(uuid4())
        try:
            for row in rows:
                row.update(run_id=run_id,offline=True,created_at=pd.Timestamp.now(tz='Asia/Kolkata').isoformat(),options_context=None,analysis=suggest(None,row.get('v2_lean')),data_timestamps={'source':str(a.data),'cutoff':a.cutoff})
                row['prediction_id']=db.save_prediction(row)
        finally: db.close()
        from .reports import table,csv_report
        table(rows,['symbol','metric','basis','E1','v2_expectation','v2_call','v2_lean','v2_confidence','reason'])
        if a.out: csv_report(rows,a.out)
    elif a.cmd=='score' and not a.auto:
        if not a.predictions or not a.actuals: ap.error('score requires --auto or predictions.csv actuals.csv')
        from .scoring import score,scoreboard
        d=score(pd.read_csv(a.predictions),pd.read_csv(a.actuals))
        print(d[['symbol','metric','E1','actual','v2_call','actual_label','v2_hit']].to_string(index=False))
        print('\nScoreboard\n'+scoreboard(d).to_string(index=False))
    elif a.cmd=='fetch':
        from .providers.screener import fetch
        from .providers.http import PoliteClient
        from .reports import csv_report
        client=PoliteClient()
        rows=[r for symbol in a.symbols for r in fetch(symbol,client)]
        print(csv_report(rows,a.out))
    else:
        if a.cmd=='score' and (a.predictions or a.actuals): ap.error('--auto cannot be combined with CSV inputs')
        if a.cmd=='predict' and a.date: ap.error('--date-range cannot be combined with --date')
        from .season import run
        return run(a)
    return 0

if __name__=='__main__':
    sys.exit(main())
