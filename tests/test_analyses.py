"""Tier 2 analyses on hand-built data with planted, known answers. No AWS."""
from datetime import date

import pytest

from bi_investigate import analyses as A
from bi_investigate.windows import comparison, shift_months


# ---- windows ---------------------------------------------------------------------------------------

def test_windows():
    d = date(2026, 3, 31)
    mom, qoq, yoy, fytd = (comparison(k, d) for k in ("mom", "qoq", "yoy", "fytd"))
    assert (mom.a.end, mom.b.end, mom.a.label, mom.b.label) == (date(2026, 2, 28), d, "Feb 2026", "Mar 2026")
    assert (qoq.a.start, qoq.a.end, qoq.b.start, qoq.b.label) == (date(2025, 10, 1), date(2025, 12, 31), date(2026, 1, 1), "Q4 FY26")
    assert yoy.a.end == date(2025, 3, 31)
    assert (fytd.a.start, fytd.a.end, fytd.b.start, fytd.b.label) == (date(2024, 4, 1), date(2025, 3, 31), date(2025, 4, 1), "FY26 YTD")
    assert shift_months(date(2024, 3, 31), -1) == date(2024, 2, 29)


# ---- change, contributions -------------------------------------------------------------------------

def test_change_amount_and_ratio():
    c = A.change(100, 120)
    assert (c.delta, round(c.delta_pct, 4)) == (20, 0.2)
    r = A.change(2, 6, 100, 200)
    assert (r.value_a, r.value_b, round(r.delta, 6)) == (0.02, 0.03, 0.01)


def test_amount_contributions_sum_and_share():
    seg = {"East": (50, None, 80, None), "West": (50, None, 40, None), "North": (0, None, 10, None)}
    cs = A.contributions("branch", seg, False, 30)          # +30 -10 +10
    assert [c.segment for c in cs] == ["East", "West", "North"]
    assert cs[0].delta == 30 and cs[0].share == 1.0 and sum(c.delta for c in cs) == 30


def test_mix_vs_rate_planted_pure_mix():
    # rates unchanged (East 1%, West 10%); book shifts toward West -> ratio rises purely by mix
    seg = {"East": (1, 100, 0.5, 50), "West": (10, 100, 15, 150)}
    tot = A.change(11, 15.5, 200, 200).delta
    cs = A.contributions("branch", seg, True, tot)
    mr = A.mix_rate_total(cs)
    assert abs(mr["rate"]) < 1e-12 and abs(mr["mix"] - tot) < 1e-12


def test_mix_vs_rate_planted_pure_rate():
    seg = {"East": (1, 100, 3, 100), "West": (10, 100, 10, 100)}   # same weights, East rate worsens
    tot = A.change(11, 13, 200, 200).delta
    mr = A.mix_rate_total(A.contributions("branch", seg, True, tot))
    assert abs(mr["mix"]) < 1e-12 and abs(mr["rate"] - tot) < 1e-12


def test_ratio_contributions_handle_new_and_empty_segments():
    seg = {"East": (1, 100, 2, 100), "New": (0, 0, 5, 50), "Gone": (3, 30, 0, 0)}
    tot = A.change(4, 7, 130, 150).delta
    cs = A.contributions("branch", seg, True, tot)
    assert abs(sum(c.delta for c in cs) - tot) < 1e-12


def test_reconciliation_failure_raises():
    with pytest.raises(A.ReconciliationError):
        A.contributions("branch", {"East": (1, None, 2, None)}, False, 5)


def test_num_den_split_exact_and_attribution():
    shrink = A.num_den(10, 1000, 10, 500)            # same NPA, book halves -> all denominator effect
    assert abs(shrink["numerator"]) < 1e-15 and abs(shrink["denominator"] - 0.01) < 1e-12
    grow = A.num_den(10, 1000, 20, 1000)              # NPA doubles, same book -> all numerator effect
    assert abs(grow["denominator"]) < 1e-15 and abs(grow["numerator"] - 0.01) < 1e-12


# ---- bridge ----------------------------------------------------------------------------------------

def _row(event, fb, tb, n, a, b, fn=False, tn=False):
    return {"event": event, "from_bucket": fb, "to_bucket": tb, "from_npa": fn, "to_npa": tn, "n": n,
            "p90_a": a, "p90_b": b, "npa_a": a if fn else 0, "npa_b": b if tn else 0}


def test_par90_bridge_planted_roll_in_dominates():
    rows = [
        _row("continuing", "90+ DPD", "90+ DPD", 10, 1000, 1050),   # stayers +50
        _row("continuing", "61-90 DPD", "90+ DPD", 3, 0, 300),      # roll-in +300
        _row("continuing", "90+ DPD", "Current", 1, 100, 0),        # cure -100
        _row("written_off", "90+ DPD", "90+ DPD", 1, 80, 0),        # write-off -80
        _row("exited", "90+ DPD", None, 1, 20, 0),                  # exit -20
        _row("new", None, "90+ DPD", 1, 0, 10),                     # new +10
        _row("continuing", "Current", "Current", 100, 0, 0),
    ]
    b = A.bridge(rows, "par90")
    steps = {s.label: s.amount for s in b.steps}
    assert b.opening == 1200 and b.closing == 1360
    assert steps["Rolled in from 61-90 DPD"] == 300 and steps["Cured to Current"] == -100
    assert steps["Written off"] == -80 and steps["Closed / exited"] == -20 and steps["New accounts"] == 10
    assert b.steps[0].label == "Rolled in from 61-90 DPD"
    assert sum(steps.values()) == b.closing - b.opening


def test_npa_bridge():
    rows = [_row("continuing", "61-90 DPD", "90+ DPD", 2, 0, 200, fn=False, tn=True),
            _row("continuing", "90+ DPD", "Current", 1, 50, 0, fn=True, tn=False)]
    steps = {s.label: s.amount for s in A.bridge(rows, "npa").steps}
    assert steps == {"New NPA": 200, "Upgraded to standard": -50}


# ---- roll rates, concentration, unusual ------------------------------------------------------------

def test_roll_rates_vs_history():
    flows = [_row("continuing", "61-90 DPD", "90+ DPD", 6, 0, 0), _row("continuing", "61-90 DPD", "61-90 DPD", 4, 0, 0)]
    hist = []
    for m in range(12):
        hist += [{"d": f"2025-{m + 1:02d}-28", "from_bucket": "61-90 DPD", "to_bucket": "90+ DPD", "n": 3},
                 {"d": f"2025-{m + 1:02d}-28", "from_bucket": "61-90 DPD", "to_bucket": "61-90 DPD", "n": 7}]
    rr = {(r.from_bucket, r.to_bucket): r for r in A.roll_rates(flows, hist)}
    r = rr[("61-90 DPD", "90+ DPD")]
    assert r.rate == 0.6 and abs(r.avg_rate_12m - 0.3) < 1e-12 and A.is_forward(r)


def test_concentration_and_unusual():
    c = A.concentration({"n_changed": 40, "gross": 1000.0, "n50": 3, "n80": 9})
    assert (c.accounts_for_50pct, c.accounts_for_80pct) == (3, 9)
    series = [(f"m{i}", 100.0 + i) for i in range(14)] + [("m14", 130.0)]   # steady +1, then +16
    u = A.unusual(series)
    assert u.rank_of_13 == 1 and u.z is None   # constant prior deltas -> no z (sd = 0), still ranked first
    series2 = [(f"m{i}", float(v)) for i, v in enumerate([0, 1, 3, 4, 6, 7, 9, 10, 12, 13, 15, 16, 18, 19, 40])]
    assert A.unusual(series2).z > 3
