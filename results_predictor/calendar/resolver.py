"""Resolve exchange authority without silently accepting conflicting dates."""
from ..schemas import CalendarEvent

def resolve(symbol, nse_dates, secondary_dates, previous=None, cancelled=False, filed=False):
    nse, secondary = sorted(set(nse_dates)), sorted(set(secondary_dates))
    day = nse[-1] if nse else secondary[-1] if secondary else ''
    status = 'CONFIRMED_NSE' if nse else 'SECONDARY_ONLY'
    if secondary and nse and set(nse) != set(secondary) or len(nse) > 1:
        status = 'DATE_CONFLICT'
    if previous and day != previous:
        status = 'RESCHEDULED'
    if cancelled:
        status = 'CANCELLED'
    if filed:
        status = 'FILED'
    return CalendarEvent(symbol, day, status, 'NSE' if nse else 'secondary', previous_date=previous, secondary_date=secondary[-1] if secondary else None)
