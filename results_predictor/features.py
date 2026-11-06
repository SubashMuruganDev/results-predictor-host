"""Feature helpers: quarters, accounting basis, metric values and YoY changes.

All amounts are Rs crore. A *panel* is ``{symbol: [row dict, ...]}`` where each row
has quarter_end (Timestamp), basis, revenue, pat, ebitda,
operating_profit_before_provisions, other_income, pbt, is_financial, availability_ts.
"""
from functools import lru_cache
import numpy as np
import pandas as pd

METRICS = ("sales", "margin", "pat")
IST = "Asia/Kolkata"


def truth(x):
    """Lenient boolean parse for CSV values."""
    return str(x).lower() in ("true", "1", "1.0")


def stamp(x):
    """Parse a timestamp; naive values are treated as IST."""
    t = pd.to_datetime(x, errors="coerce")
    if pd.isna(t):
        return pd.NaT
    return t.tz_localize(IST) if t.tzinfo is None else t.tz_convert(IST)


@lru_cache(maxsize=8192)
def shift(q, n):
    """Calendar quarter end ``n`` quarters from ``q`` (negative = earlier)."""
    return (pd.Timestamp(q).to_period("Q") + n).end_time.normalize()


def target_quarter(result_date):
    """Latest completed calendar quarter strictly before the results date."""
    d = pd.Timestamp(result_date)
    return shift(d, -1) if d != shift(d, 0) else shift(d, -1)


def family(basis):
    """Accounting family: 'integrated-consolidated' -> 'consolidated'."""
    return str(basis).lower().replace("integrated-", "")


def value(row, metric, financial):
    """Metric value for one quarter row. Margin is % (EBITDA, or op. profit before provisions for financials)."""
    if row is None:
        return np.nan
    if metric == "sales":
        return row["revenue"]
    if metric == "pat":
        return row["pat"]
    rev = row["revenue"]
    num = row.get("operating_profit_before_provisions", np.nan) if financial else row.get("ebitda", np.nan)
    return 100 * num / rev if pd.notna(rev) and rev > 0 else np.nan


def yoy_change(cur, old, metric):
    """Same-quarter-last-year change: growth for sales/PAT, pp difference for margin."""
    if metric == "margin":
        return cur - old
    return cur / old - 1 if pd.notna(old) and old != 0 else np.nan


def history(panel, symbol, q, cutoff):
    """Quarters before ``q`` known strictly before ``cutoff``, restricted to the latest basis family.

    Standalone and consolidated series are never mixed.
    """
    h = {r["quarter_end"]: r for r in panel.get(symbol, [])
         if r["quarter_end"] < q and pd.notna(r["availability_ts"]) and r["availability_ts"] < cutoff}
    if h:
        fam = family(h[max(h)].get("basis", "unknown"))
        h = {k: r for k, r in h.items() if family(r.get("basis", "unknown")) == fam}
    return h


def consistent(h, q):
    """Calendar-contiguous same-family run ending at q-1 (no stitching over gaps).

    Returns (run, reason); reason is '' when the run has at least 5 quarters.
    """
    last = h.get(shift(q, -1))
    if last is None:
        return {}, "g1_missing_q1"
    fam, run = family(last["basis"]), {}
    for lag in range(1, 100):
        k = shift(q, -lag)
        r = h.get(k)
        if r is None or family(r["basis"]) != fam:
            break
        run[k] = r
    if len(run) < 5:
        return run, "g1_fewer_than_5_same_family"
    return run, ""


def load_panel(path):
    """Load a fundamentals CSV into the panel dict used by the models."""
    f = pd.read_csv(path)
    f["quarter_end"] = pd.to_datetime(f.quarter_end)
    f["availability_ts"] = f.availability_ts.map(stamp)
    f = f.drop_duplicates(["symbol", "quarter_end"], keep="last")
    return {s: g.to_dict("records") for s, g in f.groupby("symbol")}
