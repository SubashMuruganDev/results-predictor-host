"""Old model (secondary, for comparison): seasonal-naive expectation 'E1'.

E1 = value in the same quarter last year, moved by the MEDIAN of the last four
same-quarter-YoY changes (at least 3 needed). Sales uses % growth; PAT (Rs cr) and margin (pp) use absolute changes.
This is the expectation behind the nightly dashboards' frozen '#63' pipeline.

The frozen #63 pipeline additionally turned features into BEAT probabilities with
pickled logistic models that are NOT included here. ``old_call`` is a transparent
proxy: it leans with the sign of the latest YoY change versus the median trend.
"""
import numpy as np
from .features import shift, value, yoy_change


def e1(records, q, metric, financial):
    """Seasonal-naive expectation (same math as the pipeline's rp.exp_records)."""
    base = records.get(shift(q, -4))
    if base is None or (metric == "margin" and base["revenue"] <= 0):
        return np.nan
    changes = []
    for lag in range(1, 5):
        a, b = records.get(shift(q, -lag)), records.get(shift(q, -lag - 4))
        if a is None or b is None:
            continue
        av, bv = value(a, metric, financial), value(b, metric, financial)
        v = yoy_change(av, bv, "sales") if metric == "sales" else av - bv
        if np.isfinite(v):
            changes.append(v)
    if len(changes) < 3:
        return np.nan
    med = float(np.median(changes))
    bv = value(base, metric, financial)
    return bv * (1 + med) if metric == "sales" else bv + med
