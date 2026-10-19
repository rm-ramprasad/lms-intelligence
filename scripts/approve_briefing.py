"""Approve a DRAFT briefing so executives can see it. Approved briefings are immutable.

    python scripts/approve_briefing.py --as-of 2026-03-31 --by "Jane Doe (Finance)"
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bi_investigate.store import StoreError, approve  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", type=date.fromisoformat, required=True)
    ap.add_argument("--by", required=True)
    args = ap.parse_args()
    try:
        b = approve(args.as_of, args.by)
    except StoreError as e:
        print(f"not approved: {e}")
        return 1
    print(f"{b.as_of} APPROVED by {b.approved_by} at {b.approved_at}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
