"""Run one governed query from the command line.

    python scripts/run_query.py '{"metric": "total_outstanding", "dimensions": ["state_name"]}'
    python scripts/run_query.py --example q1_outstanding_by_state
    python scripts/run_query.py --compile-only --example q3_monthly_disbursement_fy2026
    python scripts/run_query.py --list

Executes against Athena (workgroup analytics_poc_wg) unless --compile-only is given.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bi_service.config import ServiceConfig  # noqa: E402
from bi_service.query_service import QueryService  # noqa: E402
from bi_service.semantic_loader import load_semantic_model  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", nargs="?", help="QuerySpec as JSON")
    ap.add_argument("--file", type=Path, help="read the QuerySpec JSON from a file (easier in PowerShell)")
    ap.add_argument("--example", help="run a golden example from semantic/examples.yaml")
    ap.add_argument("--compile-only", action="store_true", help="print SQL, do not execute")
    ap.add_argument("--list", action="store_true", help="list approved metrics, dimensions and examples")
    args = ap.parse_args()

    model = load_semantic_model()
    if args.list:
        for m in model.metrics.values():
            print(f"metric    {m.id:<26} {m.behavior:<8} {m.status:<17} {m.name}")
        for d in model.dimensions.values():
            print(f"dimension {d.id:<26} {d.kind:<9} {'filter' if d.filterable else ''}")
        for e in model.examples.values():
            print(f"example   {e.id:<38} {e.question}")
        return 0

    if args.example:
        spec = model.examples[args.example].spec
    elif args.file:
        spec = json.loads(args.file.read_text(encoding="utf-8"))
    elif args.spec:
        spec = json.loads(args.spec)
    else:
        ap.error("give a spec, --file or --example")
    config = ServiceConfig.load()
    if args.compile_only:
        _, cq = QueryService(model, None, config).compile(spec)
        print(cq.sql)
        return 0
    result = QueryService.from_defaults().run(spec)
    print(result.model_dump_json(indent=1, exclude={"sql"}))
    return 0 if result.status in ("success", "empty") else 1


if __name__ == "__main__":
    sys.exit(main())
