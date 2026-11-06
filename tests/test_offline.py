"""Offline tests: parity with the existing pipeline's v2 output, guards and scoring."""
from pathlib import Path
import math
import pandas as pd
import pytest
from results_predictor.features import load_panel, target_quarter, consistent
from results_predictor.predict import predict
from results_predictor.scoring import score, scoreboard
from results_predictor.__main__ import main

DATA = Path(__file__).resolve().parent.parent / "data"
CUTOFF = "2026-11-05T22:26"  # when the reference file was produced (IST)


@pytest.fixture(scope="module")
def panel():
    return load_panel(DATA / "sample_fundamentals.csv")


def close(a, b):
    return (pd.isna(a) and pd.isna(b)) or math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)


def test_parity_with_pipeline(panel):
    ref = pd.read_csv(DATA / "reference_v2_nextweek.csv")
    for r in ref.itertuples():
        mine = {x["metric"]: x for x in predict(panel, r.symbol, r.result_date, CUTOFF)}[r.metric]
        assert mine["v2_call"] == r.v2_call, (r.symbol, r.metric)
        assert mine["reason"] == r.reason, (r.symbol, r.metric)
        assert close(mine["E1"], r.E1) and close(mine["v2_expectation"], r.v2_expectation)


def test_target_quarter():
    assert str(target_quarter("2026-11-11").date()) == "2026-09-30"


def test_missing_history_is_no_call(panel):
    rows = predict(panel, "NOSUCH", "2026-11-11", CUTOFF)
    assert all(r["v2_call"] == "NO CALL" and r["basis"] == "missing" for r in rows)


def test_basis_switch_breaks_run():
    q = pd.Timestamp("2026-09-30")
    h = {pd.Timestamp(d): dict(basis=b) for d, b in [("2026-06-30", "consolidated"), ("2026-03-31", "standalone")]}
    run, why = consistent(h, q)
    assert len(run) == 1 and why == "g1_fewer_than_5_same_family"


def test_scoring_and_cli(panel, tmp_path, capsys):
    preds = predict(panel, "OIL", "2026-11-12", CUTOFF)
    acts = [dict(symbol="OIL", quarter_end="2026-09-30", metric="sales", actual=1e9, reaction_pct=1.0)]
    d = score(preds, acts)
    assert d.loc[d.metric == "sales", "v2_hit"].item() is True
    assert not scoreboard(d).empty
    out = tmp_path / "p.csv"
    assert main(["predict", "--symbol", "TATAPOWER", "--date", "2026-11-11", "--cutoff", CUTOFF, "--out", str(out)]) == 0
    assert main(["score", str(out), str(DATA / "sample_actuals.csv")]) == 0
    assert "Scoreboard" in capsys.readouterr().out
