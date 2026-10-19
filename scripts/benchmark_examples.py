"""Run the golden examples through the query service and report latency and bytes scanned.

    python scripts/benchmark_examples.py [--label csv] [--out docs/benchmarks.jsonl]

Live Athena (runtime profile); aggregate results only. Values are summarised as a checksum so
before/after runs can be compared without printing data.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bi_service.query_service import QueryService  # noqa: E402


def checksum(rows) -> str:
    return hashlib.sha256(json.dumps(sorted(map(str, rows))).encode()).hexdigest()[:12]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "benchmarks.jsonl")
    args = ap.parse_args()
    svc = QueryService.from_defaults()
    total_ms = total_bytes = 0
    with open(args.out, "a", encoding="utf-8") as f:
        for ex in svc.model.examples.values():
            r = svc.run(ex.spec)
            rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "label": args.label,
                   "example": ex.id, "status": r.status, "ms": r.execution_time_ms, "bytes": r.bytes_scanned,
                   "rows": r.row_count, "checksum": checksum(r.rows)}
            f.write(json.dumps(rec) + "\n")
            total_ms += r.execution_time_ms or 0
            total_bytes += r.bytes_scanned or 0
            print(f"{ex.id:<42} {r.status:<8} {r.execution_time_ms:>6} ms {(r.bytes_scanned or 0) / 1e6:>7.2f} MB  {rec['checksum']}")
    print(f"{'TOTAL':<42} {'':<8} {total_ms:>6} ms {total_bytes / 1e6:>7.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
