from datetime import date
from types import SimpleNamespace

from results_predictor.providers.nse import NSEProvider


def test_placeholder_xbrl_links_leave_one_gap_and_preserve_all_valid_quarters(tmp_path, monkeypatch):
    urls = ['https://example.org/q1.xml', 'https://example.org/q2.xml']
    payload = [{'xbrl': value} for value in ('-', '', None, 'https://example.org/-', '  -  ', 'https://example.org/-  ')]
    for url, start, end in zip(urls, ['2026-01-01', '2026-04-01'], ['2026-03-31', '2026-06-30']):
        payload.append({'xbrl': url, 'start': start, 'end': end, 'basis': 'consolidated', 'filingDate': '2026-07-20'})
    # The alternate attachment field must remain supported too.
    payload[-1]['xbrlAttachment'] = payload[-1].pop('xbrl')

    class Client:
        def get(self, url):
            assert url in urls, f'Placeholder must never be fetched: {url!r}'
            record = next(r for r in payload if r.get('xbrl') == url or r.get('xbrlAttachment') == url)
            xml = f'''<xbrl><context id="c"><period><startDate>{record['start']}</startDate><endDate>{record['end']}</endDate></period></context><unit id="inr"><measure>iso4217:INR</measure></unit><RevenueFromOperations contextRef="c" unitRef="inr">1000000000</RevenueFromOperations></xbrl>'''
            return SimpleNamespace(content=xml.encode())

    provider = NSEProvider(client=Client(), cache_path=tmp_path/'cache.sqlite')
    monkeypatch.setattr(provider, 'api', lambda *args, **kwargs: payload)
    rows = provider.financials('COCHINSHIP')
    assert [str(r['quarter_end'].date()) for r in rows] == ['2026-03-31', '2026-06-30']
    assert all(r['revenue'] == 100 for r in rows)
    assert len(provider.gaps) == 1
    assert 'COCHINSHIP' in provider.gaps[0]
    assert '404' not in provider.gaps[0]
    provider._financials('COCHINSHIP')
    assert len(provider.gaps) == 1


def test_scan_real_nse_board_meeting_fixture(tmp_path):
    from results_predictor.calendar.scanner import scan
    from results_predictor.storage.db import Database

    provider = NSEProvider(offline=True, cache_path=tmp_path/'cache.sqlite', refresh=True)
    events = scan(provider, Database(tmp_path/'db.sqlite'), date(2026, 11, 9), date(2026, 11, 13))
    event = next(e for e in events if e.symbol == 'COCHINSHIP')
    assert event.result_date == '2026-11-13'
    assert event.status == 'CONFIRMED_NSE'
