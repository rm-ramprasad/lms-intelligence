"""Verify the Athena setup, and run approved SQL files.

    python scripts/verify_athena.py                          # read-only checks (default)
    python scripts/verify_athena.py --execute sql/FILE.sql   # run statements after typed approval

Read-only checks: caller identity, workgroup guardrails (enforced config, scan cutoff,
encrypted results), Glue database and expected tables/views.

--execute shows every statement and requires typing EXECUTE. Only CREATE DATABASE,
CREATE EXTERNAL TABLE, CREATE OR REPLACE VIEW, SELECT/WITH, SHOW and DESCRIBE are allowed.
Queries always run in the restricted workgroup, which enforces the results location.
"""
import argparse
import re
import sys
import time
from pathlib import Path

import boto3

from catalog import load_catalog

ALLOWED = re.compile(
    r"^\s*(CREATE\s+DATABASE|CREATE\s+EXTERNAL\s+TABLE|CREATE\s+OR\s+REPLACE\s+VIEW|SELECT|WITH|SHOW|DESCRIBE)\b",
    re.IGNORECASE,
)
FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|UNLOAD|DROP|ALTER|MSCK|VACUUM|OPTIMIZE)\b|\bCREATE\s+TABLE\b", re.IGNORECASE)
MAX_ROWS_PRINTED = 200


def split_statements(sql: str) -> list[tuple[str, str]]:
    """Return (label, statement) pairs. Labels come from '-- check: ...' comments."""
    out, label, buf = [], None, []
    for line in sql.splitlines():
        m = re.match(r"--\s*check:\s*(.+)", line)
        if m:
            label = m.group(1).strip()
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";").strip()
            if stmt:
                first = stmt.split("\n", 1)[0]
                out.append((label or first[:80], stmt))
            buf, label = [], None
    if "\n".join(buf).strip():
        raise ValueError("trailing SQL without terminating ';'")
    return out


def check(ok: bool, msg: str) -> bool:
    print(f"  [{'OK' if ok else 'FAIL'}] {msg}")
    return ok


def verify(session, cat: dict) -> bool:
    aws = cat["aws"]
    ok = True
    print("Identity")
    arn = session.client("sts").get_caller_identity()["Arn"]
    ok &= check(not arn.endswith(":root"), f"caller is not root ({arn})")

    print(f"Workgroup {aws['workgroup']}")
    athena = session.client("athena")
    try:
        wg = athena.get_work_group(WorkGroup=aws["workgroup"])["WorkGroup"]
        cfg = wg["Configuration"]
        rc = cfg.get("ResultConfiguration", {})
        ok &= check(wg["State"] == "ENABLED", "state ENABLED")
        ok &= check(cfg.get("EnforceWorkGroupConfiguration") is True, "workgroup configuration enforced")
        cutoff = cfg.get("BytesScannedCutoffPerQuery")
        ok &= check(cutoff is not None and cutoff <= aws["bytes_scanned_cutoff"], f"scan cutoff set ({cutoff} bytes)")
        ok &= check(rc.get("OutputLocation", "").startswith(f"s3://{aws['results_bucket']}/"), f"results -> {rc.get('OutputLocation')}")
        ok &= check(rc.get("EncryptionConfiguration", {}).get("EncryptionOption") in ("SSE_S3", "SSE_KMS"), "results encrypted")
    except athena.exceptions.InvalidRequestException:
        ok &= check(False, "workgroup exists")

    print(f"Glue database {aws['database']}")
    glue = session.client("glue")
    try:
        glue.get_database(Name=aws["database"])
        names = set()
        for page in glue.get_paginator("get_tables").paginate(DatabaseName=aws["database"]):
            names |= {t["Name"] for t in page["TableList"]}
        for t in cat["tables"]:
            ok &= check(f"raw_{t}" in names, f"raw_{t}")
            ok &= check(f"v_{t}" in names, f"v_{t}")
    except glue.exceptions.EntityNotFoundException:
        ok &= check(False, "database exists")
    return ok


def run_query(athena, aws: dict, sql: str) -> dict:
    qid = athena.start_query_execution(
        QueryString=sql,
        WorkGroup=aws["workgroup"],
        QueryExecutionContext={"Catalog": "AwsDataCatalog", "Database": aws["database"]},
    )["QueryExecutionId"]
    while True:
        q = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        if q["Status"]["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return q
        time.sleep(1)


def print_results(athena, qid: str) -> None:
    rows = []
    for page in athena.get_paginator("get_query_results").paginate(QueryExecutionId=qid):
        rows += [[d.get("VarCharValue", "") for d in r["Data"]] for r in page["ResultSet"]["Rows"]]
        if len(rows) > MAX_ROWS_PRINTED:
            break
    if not rows:
        return
    header, data = rows[0], rows[1:MAX_ROWS_PRINTED + 1]
    if len(header) > 6 and len(data) == 1:  # wide single-row profile: print vertically
        for h, v in zip(header, data[0]):
            print(f"    {h:<55} {v}")
    else:
        print("    " + " | ".join(header))
        for r in data:
            print("    " + " | ".join(r))


CTAS = re.compile(r"^\s*CREATE\s+TABLE\s+[\w.]+\s+WITH\s*\(.*?\)\s*AS\s+SELECT\b", re.IGNORECASE | re.DOTALL)


def _allowed(stmt: str, allow_ctas: bool) -> bool:
    if allow_ctas and CTAS.match(stmt):   # CREATE TABLE ... WITH (...) AS SELECT only; no other writes
        return not re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|UNLOAD|DROP|ALTER)\b", stmt, re.IGNORECASE)
    return bool(ALLOWED.match(stmt)) and not FORBIDDEN.search(stmt)


def execute(session, cat: dict, path: Path, assume_yes: bool, allow_ctas: bool = False) -> bool:
    aws = cat["aws"]
    stmts = split_statements(path.read_text(encoding="utf-8"))
    for label, s in stmts:
        if not _allowed(s, allow_ctas):
            print(f"REFUSED (statement type not allowed): {label}")
            return False
    print(f"{path} -> {len(stmts)} statement(s) in workgroup {aws['workgroup']}:")
    for i, (label, _) in enumerate(stmts, 1):
        print(f"  {i:>2}. {label}")
    if not assume_yes and input("Type EXECUTE to run them: ").strip() != "EXECUTE":
        print("Aborted; nothing was run.")
        return False

    athena = session.client("athena")
    total_bytes, ok = 0, True
    for label, s in stmts:
        q = run_query(athena, aws, s)
        state = q["Status"]["State"]
        scanned = q.get("Statistics", {}).get("DataScannedInBytes", 0)
        total_bytes += scanned
        print(f"\n[{state}] {label}  ({scanned / 1024 / 1024:.1f} MiB scanned)")
        if state != "SUCCEEDED":
            print("    " + q["Status"].get("StateChangeReason", ""))
            ok = False
            continue
        if q.get("StatementType") == "DML":
            print_results(athena, q["QueryExecutionId"])
    print(f"\nTotal scanned: {total_bytes / 1024 / 1024:.1f} MiB")
    return ok


def main() -> int:
    cat = load_catalog()
    aws = cat["aws"]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=aws["profile"])
    ap.add_argument("--execute", type=Path, metavar="SQL_FILE", help="run statements in this file after approval")
    ap.add_argument("--yes", action="store_true", help="skip the interactive prompt (explicit approval)")
    ap.add_argument("--workgroup", help="override the workgroup (e.g. the ETL workgroup for CTAS)")
    ap.add_argument("--allow-ctas", action="store_true", help="permit CREATE TABLE ... WITH (...) AS SELECT")
    args = ap.parse_args()
    if args.workgroup:
        cat["aws"]["workgroup"] = args.workgroup

    session = boto3.Session(profile_name=args.profile, region_name=aws["region"])
    ok = execute(session, cat, args.execute, args.yes, args.allow_ctas) if args.execute else verify(session, cat)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
