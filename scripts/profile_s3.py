"""Profile source CSVs to refine typed-view types. Aggregate output only.

    python scripts/profile_s3.py [--table account_summary] [--json out.json]

Streams each object into memory (never to disk). Only columns that reach the typed views
are profiled; quasi/sensitive/direct PII columns are skipped without being inspected.
Output per column: fill counts, masked shapes (digits->9, letters->A), numeric parse
rates and ranges, and literal values ONLY for low-cardinality non-identifier columns.
"""
import argparse
import csv
import io
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation

import boto3

from catalog import load_catalog, resolved_columns, view_columns

MAX_DOMAIN = 12          # show literal values only if a column has <= this many distinct values
NULL_LITERALS = {"", "NULL", "null", "NaN", "nan", "None", "NA", "N/A"}


def shape(v: str) -> str:
    return re.sub(r"[A-Za-z]", "A", re.sub(r"[0-9]", "9", v))


def to_dec(v: str):
    try:
        return Decimal(v.replace(",", ""))
    except InvalidOperation:
        return None


def profile_table(s3, bucket: str, table: dict) -> dict:
    key = table["location"].split(f"s3://{bucket}/", 1)[1] + table["source_file"]
    text = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader)
    all_cols = resolved_columns(table)
    keep = {c["name"]: c for c in view_columns(table)}
    idx = [(i, c) for i, c in enumerate(all_cols) if c["name"] in keep]

    stats = {c["name"]: {"nonnull": 0, "nulls": Counter(), "shapes": Counter(), "values": Counter(),
                         "dec_ok": 0, "int_ok": 0, "min": None, "max": None, "lead_ws": 0}
             for _, c in idx}
    rows = bad_width = 0
    for rec in reader:
        rows += 1
        if len(rec) != len(header):
            bad_width += 1
            continue
        for i, c in idx:
            v = rec[i]
            s = stats[c["name"]]
            if v != v.strip():
                s["lead_ws"] += 1
            v = v.strip()
            if v in NULL_LITERALS:
                s["nulls"][v or "<empty>"] += 1
                continue
            s["nonnull"] += 1
            s["shapes"][shape(v)] += 1
            if c["pii"] != "identifier" and len(s["values"]) <= MAX_DOMAIN:
                s["values"][v] += 1
            d = to_dec(v)
            if d is not None and d.is_finite():
                s["dec_ok"] += 1
                if d == d.to_integral_value():
                    s["int_ok"] += 1
                if c["pii"] != "identifier":
                    s["min"] = d if s["min"] is None else min(s["min"], d)
                    s["max"] = d if s["max"] is None else max(s["max"], d)

    cols = {}
    for _, c in idx:
        s = stats[c["name"]]
        out = {
            "declared": c["type"],
            "nonnull": s["nonnull"],
            "nulls": dict(s["nulls"]),
            "top_shapes": s["shapes"].most_common(4),
            "n_shapes": len(s["shapes"]),
            "dec_pct": round(100 * s["dec_ok"] / s["nonnull"], 1) if s["nonnull"] else None,
            "int_pct": round(100 * s["int_ok"] / s["nonnull"], 1) if s["nonnull"] else None,
        }
        if s["lead_ws"]:
            out["untrimmed"] = s["lead_ws"]
        if s["min"] is not None:
            out["range"] = [str(s["min"]), str(s["max"])]
        if c["pii"] != "identifier" and len(s["values"]) <= MAX_DOMAIN:
            out["domain"] = dict(s["values"].most_common())
        cols[c["name"]] = out
    return {"rows": rows, "bad_width_rows": bad_width, "header_ok": len(header) == len(all_cols), "columns": cols}


def main() -> None:
    cat = load_catalog()
    aws = cat["aws"]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=aws["profile"])
    ap.add_argument("--table", choices=list(cat["tables"]))
    ap.add_argument("--json", help="also write the aggregate profile to this file")
    args = ap.parse_args()

    s3 = boto3.Session(profile_name=args.profile, region_name=aws["region"]).client("s3")
    result = {}
    for tname, t in cat["tables"].items():
        if args.table and tname != args.table:
            continue
        p = result[tname] = profile_table(s3, aws["source_bucket"], t)
        print(f"\n===== {tname}: rows={p['rows']} bad_width_rows={p['bad_width_rows']} header_ok={p['header_ok']}")
        for name, c in p["columns"].items():
            line = f"  {name:<42} {c['declared']:<9} nn={c['nonnull']:<7} dec%={c['dec_pct']} int%={c['int_pct']} shapes={c['top_shapes']}"
            if c["nulls"]:
                line += f" nulls={c['nulls']}"
            if "range" in c:
                line += f" range={c['range']}"
            if "domain" in c:
                line += f" domain={c['domain']}"
            if "untrimmed" in c:
                line += f" untrimmed={c['untrimmed']}"
            print(line)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=1, default=str)


if __name__ == "__main__":
    main()
