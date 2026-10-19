"""Build Executive Briefing snapshots (LIVE Athena; approval required for live runs).

    python scripts/run_briefing.py --as-of 2026-03-31
    python scripts/run_briefing.py --all-history            # every month-end with 12+ months of history
    python scripts/run_briefing.py --find-demo-months       # rank month-ends by size of KPI moves (aggregate only)

Snapshots are written to briefings/<as-of>/briefing.json with status DRAFT. Approve with approve_briefing.py.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bi_investigate import analyses as A  # noqa: E402
from bi_investigate.engine import BriefingEngine, QueryRunner  # noqa: E402
from bi_investigate.playbook import load_playbook  # noqa: E402
from bi_investigate.sql_templates import Templates  # noqa: E402
from bi_investigate.store import StoreError, save  # noqa: E402
from bi_investigate.windows import shift_months  # noqa: E402
from bi_service.query_service import QueryService  # noqa: E402


def month_ends(runner: QueryRunner, t: Templates) -> tuple[date, date]:
    b = runner.run(t.bounds())[0]
    return date.fromisoformat(str(b["first_date"])[:10]), date.fromisoformat(str(b["last_date"])[:10])


def find_demo_months(svc, runner, t) -> None:
    first, last = month_ends(runner, t)
    for kpi in ("par_90_plus_ratio", "gross_npa_ratio", "collection_efficiency", "total_outstanding"):
        rows = runner.run(t.trend(kpi, first, last))
        ratio = svc.model.metrics[kpi].measure.aggregation == "ratio"
        vals = [(str(r["d"])[:10], A.ratio(r["num"], r["den"]) if ratio else A.f(r["num"])) for r in rows]
        deltas = sorted(((vals[i][0], vals[i][1] - vals[i - 1][1]) for i in range(13, len(vals))
                         if vals[i][1] is not None and vals[i - 1][1] is not None), key=lambda x: -abs(x[1]))
        fmt = (lambda d: f"{d * 100:+.2f} pp") if ratio else (lambda d: f"{d / 1e7:+.2f} Cr")
        print(f"{kpi:<24} biggest MoM moves: " + ", ".join(f"{d} ({fmt(x)})" for d, x in deltas[:5]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", type=date.fromisoformat)
    ap.add_argument("--all-history", action="store_true")
    ap.add_argument("--find-demo-months", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    svc = QueryService.from_defaults()
    runner = QueryRunner(svc.executor)
    t = Templates(svc.model)
    if args.find_demo_months:
        find_demo_months(svc, runner, t)
        return 0
    pb = load_playbook(svc.model)
    first, last = month_ends(runner, t)
    if args.all_history:
        dates, d = [], last
        while d >= shift_months(first, 12):
            dates.append(d)
            d = shift_months(d, -1)
    else:
        dates = [args.as_of or last]
    for d in dates:
        engine = BriefingEngine(svc.model, pb, runner, workers=args.workers)
        try:
            b = engine.build(d)
            p = save(b)
            un = sum(len(q.unavailable) for q in b.questions)
            print(f"{d}  DRAFT  {len(b.top_findings)} top findings  {runner.count} queries so far  "
                  f"{runner.bytes / 1e6:.1f} MB  unavailable={un}  -> {p.relative_to(ROOT)}")
        except StoreError as e:
            print(f"{d}  skipped: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
