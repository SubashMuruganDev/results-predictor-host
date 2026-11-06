"""Compatibility helpers for opt-in Screener downloads, without lxml.

Missing publication timestamps are never replaced with estimated dates.
"""
import hashlib
from pathlib import Path
import pandas as pd
from .providers.http import PoliteClient
from .providers.screener import parse

_client=None

def get(url, cache_dir='cache', refresh=False, timeout=20, gap=2.0):
    """Retain the cache API; all live requests use the stricter polite policy."""
    global _client
    path=Path(cache_dir)/(hashlib.sha256(url.encode()).hexdigest()+'.html')
    if path.exists() and not refresh:
        return path.read_text()
    if _client is None:
        _client=PoliteClient()
    response=_client.get(url)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(response.text)
    return response.text

def parse_screener_quarters(html,symbol,basis):
    return parse(html,symbol,basis)

def fetch_screener(symbol,consolidated=True,cache_dir='cache',refresh=False):
    url=f'https://www.screener.in/company/{symbol}/'+('consolidated/' if consolidated else '')
    return parse(get(url,cache_dir,refresh),symbol,'consolidated' if consolidated else 'standalone')

def build_panel_csv(symbols,out_csv,cache_dir='cache',refresh=False):
    rows=[]
    for symbol in symbols:
        rows.extend(fetch_screener(symbol,True,cache_dir,refresh) or fetch_screener(symbol,False,cache_dir,refresh))
    path=Path(out_csv)
    path.parent.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(rows).to_csv(path,index=False)
    return path
