"""Refresh semantic/dimension_values.yaml: distinct values of every string attribute dimension.

    python scripts/refresh_values.py            # runs ONE aggregate Athena query (approval required)

Only dimensions in dimensions.yaml are queried (never identifiers or PII). Output is the value
and its row count per dimension; review the diff before committing. Boolean dimensions are skipped.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bi_service.query_service import QueryService  # noqa: E402
from bi_service.sql_compiler import assert_safe_sql, ident  # noqa: E402

OUT = ROOT / "semantic" / "dimension_values.yaml"


def main() -> int:
    svc = QueryService.from_defaults()
    m = svc.model
    dims = [d for d in m.dimensions.values() if d.kind == "attribute" and d.type == "string"]
    parts = [
        f"SELECT '{d.id}' AS dimension, {ident(d.column)} AS value, count(*) AS n "
        f"FROM {m.views[d.view].qualified} GROUP BY {ident(d.column)}"
        for d in dims
    ]
    sql = "SELECT * FROM (\n" + "\nUNION ALL\n".join(parts) + "\n) ORDER BY dimension, value"
    assert_safe_sql(sql, {v.qualified for v in m.views.values()})
    out = svc.executor.execute(sql, max_rows=5000)
    if out.state != "SUCCEEDED":
        print(f"failed: {out.state} {out.error_message}")
        return 1
    values: dict[str, list] = {d.id: [] for d in dims}
    for dim, value, n in out.rows:
        if value is not None:
            values[dim].append(value)
    doc = {
        "generated": f"by scripts/refresh_values.py at {datetime.now(timezone.utc):%Y-%m-%dT%H:%MZ}; do not edit",
        "version": 1,
        "values": values,
    }
    OUT.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}  query_id={out.query_id}  bytes={out.bytes_scanned}")
    for d, v in values.items():
        print(f"  {d:<16} {len(v):>3} values")
    return 0


if __name__ == "__main__":
    sys.exit(main())
