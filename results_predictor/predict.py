"""Run both models for a symbol and results date."""
from datetime import datetime
import pandas as pd
from .features import IST, METRICS, history, target_quarter, truth
from . import model_old, model_v2


def predict(panel, symbol, result_date, cutoff=None):
    """Return per-metric rows (v2 call + old-model E1) for ``symbol`` reporting on ``result_date``.

    ``cutoff`` (default: now in IST, capped at result-date midnight) limits which
    quarters count as already published, so you never use the result being predicted.
    """
    q = target_quarter(result_date)
    if cutoff is None:
        cutoff = min(pd.Timestamp(datetime.now()).tz_localize(IST), pd.Timestamp(result_date).tz_localize(IST))
    cutoff = pd.Timestamp(cutoff)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize(IST)
    rows = []
    for m in METRICS:
        r = model_v2.classify(panel, symbol.upper(), q, cutoff, m)
        r.update(result_date=str(pd.Timestamp(result_date).date()), quarter_end=str(q.date()))
        rows.append(r)
    lean, level = model_v2.confidence([r["v2_call"] for r in rows])
    for r in rows:
        r.update(v2_lean=lean, v2_confidence=level)
    return rows
