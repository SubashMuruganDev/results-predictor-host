"""Model v2 (primary): guarded YoY forecast compared with the seasonal expectation E1.

Forecast F2 = same quarter last year x (1 + latest YoY change) (margin: + latest pp change),
computed only on a clean, contiguous, same-basis history. Call:
  BEAT if F2 is above E1 by more than the threshold, MISS if below, else NO CALL.
Thresholds (locked on train+validation, 2019-2025): sales 5%, PAT 5%, margin 2pp.
Any guard failure -> NO CALL with the reason.
"""
import numpy as np
from .features import METRICS, consistent, family, history, shift, truth, value, yoy_change
from .model_old import e1

LOCKED = {"sales": ("F2", 0.05), "margin": ("F2", 2.0), "pat": ("F2", 0.05)}


def f2(h, q, metric, financial):
    """Same quarter last year moved by the latest YoY change."""
    v = lambda k: value(h.get(shift(q, -k)), metric, financial)
    b = v(4)
    with np.errstate(divide="ignore", invalid="ignore"):
        ch = yoy_change(v(1), v(5), metric) if metric == "margin" else (v(1) / v(5) - 1 if v(5) != 0 else np.nan)
        return b + ch if metric == "margin" else b * (1 + ch)


def guards(h, q, metric, expectation, forecast):
    """Caution flags. g2 = implausible size, g3 = tiny/sign-flipping PAT, g4 = one-off other income."""
    b, last = h.get(shift(q, -4)), h.get(shift(q, -1))
    if b is None:
        return ["missing_base"]
    fin = truth(last.get("is_financial", False)) if last else False
    base, reasons = value(b, metric, fin), []
    for x in (expectation, forecast):
        if not np.isfinite(x):
            continue
        if metric == "margin" and not 0 <= x <= 100:
            reasons.append("g2_margin_range")
        elif metric != "margin" and (metric == "sales" or base > 0) and base != 0 and not .5 <= x / base <= 2:
            reasons.append("g2_expectation_ratio")
    if metric == "pat":
        if abs(base) < .01 * abs(b["revenue"]):
            reasons.append("g3_small_pat")
        lv = value(last, metric, fin)
        if np.isfinite(lv) and base * lv < 0:
            reasons.append("g3_pat_sign_change")
        oi = b.get("other_income", np.nan)
        if np.isfinite(oi) and oi > 0 and any(np.isfinite(x) and x > 0 and oi > .5 * x for x in (b.get("pbt", np.nan), b.get("pat", np.nan))):
            reasons.append("g4_one_off")
    return sorted(set(reasons))


def classify(panel, symbol, q, cutoff, metric):
    """One v2 call for one metric. Returns a dict with call, numbers and reason."""
    forecaster, t = LOCKED[metric]
    h = history(panel, symbol, q, cutoff)
    last = h.get(max(h)) if h else {}
    fin = truth(last.get("is_financial", False))
    raw_e1 = e1(h, q, metric, fin)
    run, why = consistent(h, q)
    clean_e1, clean_f = e1(run, q, metric, fin), f2(run, q, metric, fin)
    reasons = ([why] if why else []) + guards(run, q, metric, clean_e1, clean_f)
    if not all(np.isfinite(v) for v in (raw_e1, clean_e1, clean_f)):
        reasons.append("missing_consistent_forecast")
    call = "NO CALL"
    if not reasons:
        delta = clean_f - raw_e1 if metric == "margin" else ((clean_f - raw_e1) / abs(raw_e1) if raw_e1 else np.nan)
        call = "BEAT" if delta > t else "MISS" if delta < -t else "NO CALL"
        if call == "NO CALL":
            reasons.append("within_locked_threshold")
    base = h.get(shift(q, -4))
    return dict(symbol=symbol, metric=metric, basis=family(last["basis"]) if h else "missing",
                base=value(base, metric, fin), E1=raw_e1, v2_expectation=clean_f, forecaster=forecaster,
                threshold=t, v2_call=call, reason=";".join(sorted(set(reasons))) or "guarded_forecast_vs_E1")


def confidence(calls):
    """Simple overall lean/confidence: agreement of directional calls across the 3 metrics."""
    d = [c for c in calls if c != "NO CALL"]
    if not d:
        return "NO CALL", "none"
    beats = d.count("BEAT")
    lean = "BEAT" if beats > len(d) - beats else "MISS" if beats < len(d) - beats else "MIXED"
    level = "high" if len(d) == 3 and lean != "MIXED" else "medium" if lean != "MIXED" and len(d) >= 2 else "low"
    return lean, level
