"""Phase A metadata inventory: Glue/Athena catalog only, no data is read.

    python scripts/discover_athena.py [--out docs/metadata_inventory.json]

Reads identity, workgroup config, and every table/view in the database (columns, types,
view SQL). Classifies view columns into dates / measures / dimensions / identifiers and
lists candidate join keys (shared column names). Candidates are NOT approved relationships.
"""
import argparse
import base64
import json
import re
from collections import defaultdict
from pathlib import Path

import boto3

from catalog import ROOT, load_catalog, resolved_columns

MEASURE_TYPES = ("decimal", "double", "real")
JOIN_HINT = re.compile(r"(_id|_number|_code|_date|^od_days$|^par_bucket$)")


def view_sql(table: dict) -> str | None:
    text = table.get("ViewOriginalText") or ""
    m = re.match(r"/\* Presto View: (.+) \*/", text.strip())
    if not m:
        return None
    return json.loads(base64.b64decode(m.group(1)))["originalSql"]


def classify(col_type: str, name: str, meta: dict | None) -> str:
    if meta and meta.get("pii") == "identifier":
        return "identifier"
    if col_type in ("date", "timestamp"):
        return "date"
    if col_type.startswith(MEASURE_TYPES):
        return "measure"
    if col_type == "boolean":
        return "flag"
    if col_type in ("int", "integer", "bigint") and (name.endswith("_order") or name.endswith("_counter") or name.endswith("_offset")):
        return "sort_or_helper"
    if col_type in ("int", "integer", "bigint"):
        return "numeric_attribute"
    return "dimension"


def main() -> None:
    cat = load_catalog()
    aws = cat["aws"]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=aws["profile"])
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "metadata_inventory.json")
    args = ap.parse_args()

    s = boto3.Session(profile_name=args.profile, region_name=aws["region"])
    ident = s.client("sts").get_caller_identity()
    wg = s.client("athena").get_work_group(WorkGroup=aws["workgroup"])["WorkGroup"]["Configuration"]
    glue = s.client("glue")
    tables = []
    for page in glue.get_paginator("get_tables").paginate(DatabaseName=aws["database"]):
        tables += page["TableList"]

    yaml_meta = {f"v_{t}": {c["name"]: c for c in resolved_columns(v)} for t, v in cat["tables"].items()}
    inv = {
        "identity": {"arn": ident["Arn"], "account": ident["Account"], "region": s.region_name},
        "workgroup": {
            "name": aws["workgroup"],
            "enforced": wg.get("EnforceWorkGroupConfiguration"),
            "bytes_scanned_cutoff": wg.get("BytesScannedCutoffPerQuery"),
            "output": wg.get("ResultConfiguration", {}).get("OutputLocation"),
            "encryption": wg.get("ResultConfiguration", {}).get("EncryptionConfiguration", {}).get("EncryptionOption"),
        },
        "database": aws["database"],
        "objects": {},
        "candidate_join_keys": {},
    }
    key_owners = defaultdict(list)
    for t in sorted(tables, key=lambda x: x["Name"]):
        is_view = t.get("TableType") == "VIRTUAL_VIEW"
        cols = t["StorageDescriptor"]["Columns"]
        entry = {"type": "view" if is_view else "external_table", "column_count": len(cols)}
        if is_view:
            meta = yaml_meta.get(t["Name"], {})
            groups = defaultdict(list)
            for c in cols:
                m = meta.get(c["Name"])
                kind = classify(c["Type"], c["Name"], m)
                quality = m.get("quality") if m else None
                groups[kind].append(c["Name"] + (f" [{quality}]" if quality else ""))
                if JOIN_HINT.search(c["Name"]) and c["Type"] not in MEASURE_TYPES:
                    key_owners[c["Name"]].append(t["Name"])
            entry["columns_by_role"] = dict(groups)
            sql = view_sql(t)
            entry["definition_retrieved"] = sql is not None
            entry["source"] = re.findall(rf"FROM\s+({re.escape(aws['database'])}\.\w+)", sql or "")
        else:
            entry["location"] = t["StorageDescriptor"].get("Location")
        inv["objects"][t["Name"]] = entry
    inv["candidate_join_keys"] = {k: v for k, v in sorted(key_owners.items()) if len(v) > 1}

    args.out.parent.mkdir(exist_ok=True)
    args.out.write_text(json.dumps(inv, indent=1), encoding="utf-8")
    print(json.dumps({k: inv[k] for k in ("identity", "workgroup", "candidate_join_keys")}, indent=1))
    for name, e in inv["objects"].items():
        roles = {k: len(v) for k, v in e.get("columns_by_role", {}).items()}
        print(f"{name:<22} {e['type']:<15} cols={e['column_count']:<4} {roles if roles else e.get('location')}"
              + (f" def={'yes' if e['definition_retrieved'] else 'no'} src={e['source']}" if e["type"] == "view" else ""))
    print(f"\nwrote {args.out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
