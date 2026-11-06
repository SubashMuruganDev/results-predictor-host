"""Opt-in Screener reader using stdlib HTML parsing and the polite client.

Publication times are unavailable: rows remain ineligible for point-in-time calls.
"""
from html.parser import HTMLParser
import re
import pandas as pd
from ..fundamentals.normalize import normalize

class QuarterTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_section=False
        self.in_table=False
        self.in_cell=False
        self.rows=[]
        self.row=[]
        self.cell=''
    def handle_starttag(self,tag,attrs):
        if dict(attrs).get('id')=='quarters': self.in_section=True
        if tag=='table' and self.in_section: self.in_table=True
        if tag=='tr' and self.in_table: self.row=[]
        if tag in ('td','th') and self.in_table: self.in_cell=True; self.cell=''
    def handle_data(self,data):
        if self.in_cell: self.cell+=data
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.in_cell: self.row.append(self.cell.strip()); self.in_cell=False
        if tag=='tr' and self.in_table: self.rows.append(self.row)
        if tag=='table' and self.in_table: self.in_table=False; self.in_section=False

def parse(html,symbol,basis='consolidated'):
    parser=QuarterTable(); parser.feed(html)
    if not parser.rows: return []
    table=parser.rows
    rows=[]
    names={'sales':'revenue','revenue':'revenue','net profit':'pat','profit before tax':'pbt','other income':'other_income','operating profit':'ebitda'}
    for col,header in enumerate(table[0][1:],1):
        if 'TTM' in header: continue
        q=pd.to_datetime('01 '+header,errors='coerce')
        if pd.isna(q): continue
        row=dict(symbol=symbol,quarter_end=q.to_period('Q').end_time.date().isoformat(),basis=basis,source='Screener; unavailable publication time')
        for cells in table[1:]:
            key=names.get(re.sub(r'[^a-z ]','',cells[0].lower()).strip()) if cells else None
            if key and len(cells)>col: row[key]=cells[col]
        rows.append(row)
    return normalize(rows,symbol)


def fetch(symbol,client):
    html=client.get(f'https://www.screener.in/company/{symbol}/consolidated/').text
    return parse(html,symbol)
