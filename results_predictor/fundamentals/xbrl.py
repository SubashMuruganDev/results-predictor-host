"""Conservative XBRL reader: explicit contexts and units only, no derived EBITDA."""
import xml.etree.ElementTree as ET
from .normalize import normalize, now

def local(tag):
    return tag.rsplit('}',1)[-1]

def parse_xbrl(content,symbol,metadata):
    try:
        root=ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError('invalid XBRL XML') from exc
    contexts={}
    units={}
    for node in root.iter():
        if local(node.tag)=='unit':
            units[node.get('id')]=' '.join(n.text or '' for n in node.iter() if local(n.tag)=='measure')
        if local(node.tag)=='context':
            dates={local(n.tag):n.text for n in node.iter() if local(n.tag) in ('startDate','endDate')}
            dimensions=[n for n in node.iter() if local(n.tag) in ('explicitMember','typedMember')]
            # Segment, geography and other dimensions are never company totals.
            if any(local(n.tag)=='typedMember' or not any(x in (n.text or '').lower() for x in ('consolidatedmember','standalonemember')) for n in dimensions):
                continue
            members=' '.join(n.text or '' for n in dimensions).lower()
            basis='consolidated' if 'consolidated' in members and 'standalone' not in members else 'standalone' if 'standalone' in members else str(metadata.get('basis') or metadata.get('consolidated') or '').lower()
            if 'endDate' in dates and 'startDate' in dates:
                import pandas as pd
                days=(pd.Timestamp(dates['endDate'])-pd.Timestamp(dates['startDate'])).days
                if 75 <= days <= 100:
                    contexts[node.get('id')]=(dates['endDate'],basis)
    aliases={'RevenueFromOperations':'revenue','RevenueFromOperationsNet':'revenue','ProfitLossForPeriod':'pat','ProfitBeforeTax':'pbt','OtherIncome':'other_income','EBITDA':'ebitda'}
    grouped={}
    for node in root.iter():
        name=aliases.get(local(node.tag))
        context=contexts.get(node.get('contextRef'))
        if not name or not context or 'INR' not in units.get(node.get('unitRef'),''):
            continue
        row=grouped.setdefault(context,dict(symbol=symbol,quarter_end=context[0],basis=context[1],filing_timestamp=metadata.get('broadcastDate') or metadata.get('filingDate'),source='NSE XBRL'))
        try:
            row[name]=float(node.text)*10**int(node.get('scale','0'))/1e7
        except (TypeError,ValueError):
            continue
    return normalize(list(grouped.values()))
