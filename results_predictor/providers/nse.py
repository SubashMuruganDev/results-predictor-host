"""Best-effort public exchange endpoints, with no authentication bypass."""
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
import requests
from .http import PoliteClient, StopNetwork
from ..fundamentals.normalize import normalize, records

ROOT=Path(__file__).resolve().parents[2]
FIXTURES=ROOT/'tests'/'fixtures'
BASE='https://www.nseindia.com'
BAN_URL='https://nsearchives.nseindia.com/content/fo/fo_secban.csv'
INDICES={'NIFTY','BANKNIFTY','FINNIFTY','MIDCPNIFTY','NIFTYNXT50','NIFTYIT','NIFTY50','NIFTYBANK'}

def equity_symbols(payload):
    if isinstance(payload,dict):
        data=payload.get('data',payload)
        if isinstance(data,dict):
            rows=data.get('UnderlyingList',data.get('underlyings',[]))
        else:
            rows=data
    else:
        rows=payload
    out=set()
    for row in rows:
        if isinstance(row,str):
            row={'symbol':row}
        symbol=str(row.get('symbol',row.get('underlying',''))).strip().upper()
        instrument=str(row.get('instrument',row.get('instrumentType',''))).upper()
        if symbol and symbol not in INDICES and 'IDX' not in instrument and 'INDEX' not in instrument:
            out.add(symbol)
    return sorted(out)

class NSEProvider:
    def __init__(self, offline=False, client=None, fixtures=FIXTURES, cache_path=None, refresh=False):
        from ..storage.cache import Cache
        self.cache=Cache(cache_path)
        self.refresh=refresh
        self.offline=offline
        self.client=client or PoliteClient()
        self.fixtures=Path(fixtures)
        self.warmed=False
        self.gaps=[]

    def fixture(self,name):
        return json.loads((self.fixtures/name).read_text())

    def api(self,path,**params):
        if self.offline:
            raise RuntimeError('network disabled')
        if not self.warmed:
            self.client.get(BASE)
            self.warmed=True
        return self.client.get(BASE+path,params=params).json()

    def universe(self,db):
        source='offline fixture' if self.offline else 'NSE underlying-information'
        cached=db.latest_snapshot('universe',source)
        if not self.offline:
            fallback=db.latest_snapshot('universe','NSE fo_mktlots.csv')
            if fallback and (not cached or fallback['fetched_at']>cached['fetched_at']):
                cached=fallback
        if cached and not self.refresh and (datetime.now(timezone.utc)-datetime.fromisoformat(cached['fetched_at'])).total_seconds()<86400:
            return cached['payload']
        if self.offline:
            symbols=equity_symbols(self.fixture('underlyings.json'))
        else:
            try:
                symbols=equity_symbols(self.api('/api/underlying-information'))
                if not symbols:
                    raise ValueError('empty underlying list')
            except StopNetwork:
                raise
            except (requests.RequestException,ValueError,TypeError,KeyError) as exc:
                self.gaps.append(f'Universe API: {exc}; trying NSE market-lots CSV')
                text=self.client.get('https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv').text
                rows=csv.DictReader(io.StringIO(text))
                symbols=equity_symbols([{'symbol':next((v for k,v in r.items() if k and k.strip().upper()=='SYMBOL'),'')} for r in rows])
                source='NSE fo_mktlots.csv'
        if not symbols:
            raise ValueError('No equity F&O underlyings available')
        db.snapshot('universe',symbols,source)
        return symbols

    def meetings(self,start,end):
        key=str((self.offline,str(self.fixtures.resolve()),'meetings',f'{start}:{end}'))
        result=self.cache.load(key,'calendar',lambda:self._meetings(start,end),self.refresh)
        return result

    def _meetings(self,start,end):
        if self.offline:
            return records(self.fixture('board_meetings.json'))
        return records(self.api('/api/corporate-board-meetings',index='equities',from_date=start.strftime('%d-%m-%Y'),to_date=end.strftime('%d-%m-%Y')))

    def secondary(self,start,end):
        key=str((self.offline,str(self.fixtures.resolve()),'secondary',f'{start}:{end}'))
        result=self.cache.load(key,'calendar',lambda:self._secondary(start,end),self.refresh)
        return result

    def _secondary(self,start,end):
        if self.offline:
            self.gaps.append('Secondary calendar unavailable in offline fixtures')
            return []
        # BSE requires its own published scrip codes. Do not infer mappings.
        try:
            payload=self.client.get('https://api.bseindia.com/BseIndiaAPI/api/Corpforthresults/w',params={'scripcode':'','fromdate':start.strftime('%Y%m%d'),'todate':end.strftime('%Y%m%d')}).json()
            rows=payload.get('Table',[]) if isinstance(payload,dict) else []
            usable=[r for r in rows if r.get('symbol') and r.get('date')]
            if not usable:
                self.gaps.append('BSE secondary calendar unavailable: response has no verified NSE symbol/date mapping')
            return usable
        except (requests.RequestException,ValueError,StopNetwork) as exc:
            self.gaps.append(f'BSE secondary calendar unavailable: {exc}')
            return []

    def financials(self,symbol):
        key=str((self.offline,str(self.fixtures.resolve()),'financials',symbol))
        result=self.cache.load(key,'fundamentals',lambda:self._financials(symbol),self.refresh)
        return normalize(result,symbol)

    def _financials(self,symbol):
        if self.offline:
            payload=self.fixture('financial_results.json')
            return normalize([r for r in records(payload) if r.get('symbol')==symbol],symbol)
        payload=self.api('/api/corporates-financial-results',index='equities',symbol=symbol,period='Quarterly')
        rows=normalize(payload,symbol)
        # API often returns only links. Parse supported XBRL facts; never infer EBITDA.
        for record in records(payload):
            url=str(record.get('xbrl') or record.get('xbrlAttachment') or '').strip()
            if not url or url=='-' or url.endswith('/-'):
                gap=f'{symbol} XBRL unavailable: missing or placeholder link'
                if gap not in self.gaps:
                    self.gaps.append(gap)
                continue
            if url:
                try:
                    from ..fundamentals.xbrl import parse_xbrl
                    rows.extend(self.cache.load(str((self.offline,symbol,url,record)),'filing',lambda:parse_xbrl(self.client.get(url).content,symbol,record),self.refresh))
                except (ValueError,requests.RequestException) as exc:
                    self.gaps.append(f'{symbol} XBRL unavailable: {exc}')
        return rows

    def option_chain(self,symbol):
        key=str((self.offline,str(self.fixtures.resolve()),'option_chain',symbol))
        result=self.cache.load(key,'options',lambda:self._option_chain(symbol),self.refresh)
        return result

    def _option_chain(self,symbol):
        return self.fixture('option_chain.json') if self.offline else self.api('/api/option-chain-equities',symbol=symbol)

    def bans(self):
        text=(self.fixtures/'ban_list.csv').read_text() if self.offline else self.client.get(BAN_URL).text
        return {str(v).strip().upper() for row in csv.reader(io.StringIO(text)) for v in row if str(v).strip().upper() not in ('SYMBOL','SR. NO.','')}
