"""Score predictions against actual results (and optional price reaction).

Actual BEAT means actual strictly above E1 (equal counts as MISS). NO CALL is not scored.
Each model is also compared with an always-BEAT baseline on the same rows.
"""
import pandas as pd


def label(actual, e1):
    """BEAT/MISS/PENDING for an actual vs the seasonal expectation."""
    if pd.isna(actual) or pd.isna(e1):
        return "PENDING"
    return "BEAT" if actual > e1 else "MISS"


def score(predictions, actuals):
    """Merge prediction rows with actuals (symbol, quarter_end, metric, actual[, reaction_pct])."""
    p, a = pd.DataFrame(predictions), pd.DataFrame(actuals)
    d = p.merge(a, on=["symbol", "quarter_end", "metric"], how="left")
    d["actual_label"] = [label(x, e) for x, e in zip(d.get("actual"), d.E1)]
    d["v2_hit"] = [None if c == "NO CALL" or t == "PENDING" else c == t for c, t in zip(d.v2_call, d.actual_label)]
    if "reaction_pct" in d:
        d["reaction_dir_match"] = [None if c == "NO CALL" or pd.isna(r) else (r > 0) == (c == "BEAT") for c, r in zip(d.v2_call, d.reaction_pct)]
    return d


def scoreboard(d):
    """Per-model hit rate vs always-BEAT, by metric."""
    out = []
    for m, g in d[d.actual_label != "PENDING"].groupby("metric"):
        called = g[g.v2_hit.notna()]
        out.append(dict(model="v2", metric=m, called=len(called), hits=int(called.v2_hit.sum()),
                        hit_rate=called.v2_hit.mean() if len(called) else None,
                        always_beat=(called.actual_label == "BEAT").mean() if len(called) else None))
        out.append(dict(model="always-BEAT", metric=m, called=len(g), hits=int((g.actual_label == "BEAT").sum()),
                        hit_rate=(g.actual_label == "BEAT").mean(), always_beat=None))
    return pd.DataFrame(out)
