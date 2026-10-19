"""Deterministic analyses (pure functions over aggregated rows). Every decomposition reconciles exactly to its
parent change; otherwise ReconciliationError is raised and the analysis is withheld."""
import math
import statistics

from .models import Bridge, BridgeStep, ChangeSummary, Concentration, Contribution, RollRate, Unusual

PAR90 = "90+ DPD"
BUCKET_ORDER = ["Current", "1-30 DPD", "31-60 DPD", "61-90 DPD", "90+ DPD"]


class ReconciliationError(ValueError):
    pass


def f(x) -> float | None:
    return None if x is None else float(x)


def _tol(x: float) -> float:
    return max(0.01, abs(x) * 1e-9)


def reconcile(parts: float, total: float, what: str) -> None:
    if abs(parts - total) > _tol(total):
        raise ReconciliationError(f"{what}: components {parts} != total {total}")


def ratio(num, den) -> float | None:
    num, den = f(num), f(den)
    return None if num is None or not den else num / den


# ---- A1 change -------------------------------------------------------------------------------------

def change(num_a, num_b, den_a=None, den_b=None) -> ChangeSummary:
    is_ratio = den_a is not None or den_b is not None
    va = ratio(num_a, den_a) if is_ratio else f(num_a)
    vb = ratio(num_b, den_b) if is_ratio else f(num_b)
    delta = None if va is None or vb is None else vb - va
    pct = None if delta is None or not va else delta / abs(va)
    return ChangeSummary(value_a=va, value_b=vb, delta=delta, delta_pct=pct, num_a=f(num_a), num_b=f(num_b),
                         den_a=f(den_a), den_b=f(den_b))


# ---- A2 / A3 segment contribution (with mix vs rate for ratios) ------------------------------------

def contributions(dimension: str, seg: dict[str, tuple], is_ratio: bool, total_delta: float) -> list[Contribution]:
    """seg: segment -> (num_a, den_a, num_b, den_b) (den None for amounts). Σ delta == total_delta (exact)."""
    out = []
    if is_ratio:
        wa_tot = sum(f(v[1]) or 0 for v in seg.values())
        wb_tot = sum(f(v[3]) or 0 for v in seg.values())
        for s, (na, da, nb, db) in seg.items():
            na, da, nb, db = (f(x) or 0.0 for x in (na, da, nb, db))
            wa = da / wa_tot if wa_tot else 0.0
            wb = db / wb_tot if wb_tot else 0.0
            ra = na / da if da else 0.0
            rb = nb / db if db else 0.0
            mix = (wb - wa) * (ra + rb) / 2
            rate = (wa + wb) / 2 * (rb - ra)
            out.append(Contribution(dimension=dimension, segment=s, value_a=ra if da else None,
                                    value_b=rb if db else None, delta=mix + rate,
                                    share=(mix + rate) / total_delta if total_delta else None, mix=mix, rate=rate))
        tol_total = total_delta
        parts = sum(c.delta for c in out)
        if abs(parts - tol_total) > max(1e-9, abs(tol_total) * 1e-6):
            raise ReconciliationError(f"{dimension}: segment ratio contributions {parts} != {tol_total}")
    else:
        for s, (na, _, nb, _) in seg.items():
            na, nb = f(na) or 0.0, f(nb) or 0.0
            d = nb - na
            out.append(Contribution(dimension=dimension, segment=s, value_a=na, value_b=nb, delta=d,
                                    share=d / total_delta if total_delta else None))
        reconcile(sum(c.delta for c in out), total_delta, f"{dimension} segment contributions")
    return sorted(out, key=lambda c: -abs(c.delta))


def mix_rate_total(contribs: list[Contribution]) -> dict[str, float]:
    return {"mix": sum(c.mix or 0 for c in contribs), "rate": sum(c.rate or 0 for c in contribs)}


def material(contribs: list[Contribution], threshold: float, max_n: int) -> list[Contribution]:
    return [c for c in contribs if c.share is not None and abs(c.share) >= threshold][:max_n]


# ---- A6 numerator vs denominator (Shapley, exact) --------------------------------------------------

def num_den(num_a, den_a, num_b, den_b) -> dict[str, float]:
    na, da, nb, db = (f(x) for x in (num_a, den_a, num_b, den_b))
    num_eff = 0.5 * ((nb - na) / da + (nb - na) / db)
    den_eff = 0.5 * ((na / db - na / da) + (nb / db - nb / da))
    total = nb / db - na / da
    if abs(num_eff + den_eff - total) > max(1e-12, abs(total) * 1e-9):
        raise ReconciliationError("numerator/denominator split does not reconcile")
    return {"numerator": num_eff, "denominator": den_eff, "total": total}


# ---- A5 bridge -------------------------------------------------------------------------------------

def bridge(rows: list[dict], kind: str) -> Bridge:
    """rows from the flows template: event, from_bucket, to_bucket, from_npa, to_npa, n, p90_a, p90_b, npa_a, npa_b.
    Every account contributes (amount_b - amount_a) to exactly one step, so steps sum to closing - opening."""
    steps: dict[str, list] = {}
    opening = closing = 0.0
    for r in rows:
        a, b = (f(r["p90_a"]) or 0.0, f(r["p90_b"]) or 0.0) if kind == "par90" else (f(r["npa_a"]) or 0.0, f(r["npa_b"]) or 0.0)
        opening += a
        closing += b
        if a == 0 and b == 0:
            continue
        ev = r["event"]
        if kind == "par90":
            a_in, b_in = r.get("from_bucket") == PAR90, r.get("to_bucket") == PAR90
        else:
            a_in, b_in = bool(r.get("from_npa")), bool(r.get("to_npa"))
        if ev == "new":
            label = "New accounts"
        elif ev in ("exited", "closed"):
            label = "Closed / exited"
        elif ev == "written_off":
            label = "Written off"
        elif a_in and b_in:
            label = "Balance change (stayed 90+)" if kind == "par90" else "Balance change (stayed NPA)"
        elif b_in:
            label = f"Rolled in from {r.get('from_bucket')}" if kind == "par90" else "New NPA"
        elif a_in:
            label = f"Cured to {r.get('to_bucket')}" if kind == "par90" else "Upgraded to standard"
        else:
            label = "Other balance change"
        acc = steps.setdefault(label, [0.0, 0])
        acc[0] += b - a
        acc[1] += int(r["n"])
    out = [BridgeStep(label=k, amount=v[0], accounts=v[1]) for k, v in steps.items()]
    reconcile(sum(s.amount for s in out), closing - opening, f"{kind} bridge")
    order = lambda s: (0 if s.amount >= 0 else 1, -abs(s.amount))  # noqa: E731
    return Bridge(kind=kind, opening=opening, closing=closing, steps=sorted(out, key=order))


# ---- A4 roll rates ---------------------------------------------------------------------------------

def roll_rates(flow_rows: list[dict], monthly_rows: list[dict]) -> list[RollRate]:
    """flow_rows: MoM flows (continuing accounts). monthly_rows: d, from_bucket, to_bucket, n for the prior 12 months."""
    cur: dict[tuple, int] = {}
    for r in flow_rows:
        if r["event"] in ("continuing", "written_off", "closed") and r.get("from_bucket") and r.get("to_bucket"):
            k = (r["from_bucket"], r["to_bucket"])
            cur[k] = cur.get(k, 0) + int(r["n"])
    by_month: dict[str, dict[tuple, int]] = {}
    for r in monthly_rows:
        by_month.setdefault(str(r["d"]), {})[(r["from_bucket"], r["to_bucket"])] = int(r["n"])

    def rates(counts: dict[tuple, int]) -> dict[tuple, float]:
        totals: dict[str, int] = {}
        for (a, _), n in counts.items():
            totals[a] = totals.get(a, 0) + n
        return {k: n / totals[k[0]] for k, n in counts.items() if totals[k[0]]}

    cur_rates = rates(cur)
    hist = [rates(m) for m in by_month.values()]
    out = []
    for (a, b), r in cur_rates.items():
        hv = [h.get((a, b), 0.0) for h in hist if any(k[0] == a for k in h)]
        out.append(RollRate(from_bucket=a, to_bucket=b, accounts=cur[(a, b)], rate=r,
                            avg_rate_12m=statistics.mean(hv) if hv else None))
    key = lambda x: (BUCKET_ORDER.index(x.from_bucket) if x.from_bucket in BUCKET_ORDER else 9,  # noqa: E731
                     BUCKET_ORDER.index(x.to_bucket) if x.to_bucket in BUCKET_ORDER else 9)
    return sorted(out, key=key)


def is_forward(r: RollRate) -> bool:
    try:
        return BUCKET_ORDER.index(r.to_bucket) > BUCKET_ORDER.index(r.from_bucket)
    except ValueError:
        return False


# ---- A7 concentration ------------------------------------------------------------------------------

def concentration(row: dict) -> Concentration:
    return Concentration(accounts_changed=int(row.get("n_changed") or 0), gross_movement=f(row.get("gross")) or 0.0,
                         accounts_for_50pct=row.get("n50"), accounts_for_80pct=row.get("n80"))


# ---- A8 unusualness --------------------------------------------------------------------------------

def unusual(series: list[tuple[str, float | None]]) -> Unusual:
    """series: chronological (month-end, value) ending at the as-of month. Latest Δ vs the previous 12 Δs."""
    vals = [(d, v) for d, v in series if v is not None]
    deltas = [vals[i][1] - vals[i - 1][1] for i in range(1, len(vals))]
    if len(deltas) < 4:
        return Unusual(z=None, rank_of_13=None, series=series)
    last, prev = deltas[-1], deltas[-13:-1]
    sd = statistics.pstdev(prev) if len(prev) > 1 else 0.0
    z = (last - statistics.mean(prev)) / sd if sd > 0 else None
    window = [abs(x) for x in deltas[-13:]]
    rank = sorted(window, reverse=True).index(abs(last)) + 1
    return Unusual(z=None if z is None or math.isnan(z) else z, rank_of_13=rank, series=series)
