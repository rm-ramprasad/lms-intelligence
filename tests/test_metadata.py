"""Offline metadata tests: no AWS calls, no data access."""
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from catalog import EXCLUDED_PII, PII_LEVELS, QUALITY, TYPES, load_catalog, resolved_columns, view_columns  # noqa: E402
import generate_sql  # noqa: E402
from verify_athena import ALLOWED, FORBIDDEN, split_statements  # noqa: E402

CAT = load_catalog()
TABLES = CAT["tables"]
SQL = {p.name: p.read_text(encoding="utf-8") for p in (ROOT / "sql").glob("*.sql")}
IDENT = re.compile(r"^[a-z][a-z0-9_]*$")


@pytest.mark.parametrize("tname", TABLES)
def test_column_count_matches_header(tname):
    assert len(TABLES[tname]["columns"]) == TABLES[tname]["header_column_count"]


@pytest.mark.parametrize("tname", TABLES)
def test_column_names_valid_and_unique(tname):
    names = [c["name"] for c in resolved_columns(TABLES[tname])]
    assert all(IDENT.match(n) for n in names), [n for n in names if not IDENT.match(n)]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("tname", TABLES)
def test_types_and_pii_levels_known(tname):
    for c in resolved_columns(TABLES[tname]):
        assert c["type"] in TYPES, c
        assert c["pii"] in PII_LEVELS, c
        assert c["quality"] in QUALITY | {None}, c


@pytest.mark.parametrize("tname", TABLES)
def test_location_is_one_folder_under_source_prefix(tname):
    aws = CAT["aws"]
    loc = TABLES[tname]["location"]
    assert loc.startswith(f"s3://{aws['source_bucket']}/{aws['source_prefix']}")
    assert loc.endswith("/")


def test_generated_sql_is_in_sync():
    for path, text in generate_sql.outputs(CAT).items():
        assert SQL[path.name] == text, f"{path.name} is stale: run scripts/generate_sql.py"


def test_customer_table_defaults_to_direct_pii():
    assert TABLES["dim_customers"]["default_pii"] == "direct"


def test_pii_override_only_where_approved():
    assert {t for t, v in TABLES.items() if v.get("expose_pii_in_view")} <= {"dim_customers", "dim_adm"}


@pytest.mark.parametrize("tname", [t for t in TABLES if not TABLES[t].get("expose_pii_in_view")])
def test_excluded_pii_never_in_views_or_validation(tname):
    excluded = [c["name"] for c in resolved_columns(TABLES[tname]) if c["pii"] in EXCLUDED_PII]
    db = CAT["aws"]["database"]
    own = [s for _, s in split_statements(SQL["create_typed_views.sql"]) if f"{db}.v_{tname} " in s]
    own += [s for label, s in split_statements(SQL["validate_data.sql"]) if label.startswith(f"{tname}.")]
    assert own
    for stmt in own:
        for name in excluded:
            assert f'"{name}"' not in stmt, f"{tname}.{name} leaked"


def test_known_direct_identifiers_excluded():
    must_exclude = {"first_name", "last_name", "pan_number", "phone_1", "email", "address_1", "uid_num",
                    "account_number_1", "passport", "voter_id", "customer_name", "udyam_registration_no"}
    for t in TABLES.values():
        if t.get("expose_pii_in_view"):
            continue
        assert not must_exclude & {c["name"] for c in view_columns(t)}


def test_validation_queries_are_aggregate_only():
    for label, stmt in split_statements(SQL["validate_data.sql"]):
        assert stmt.upper().startswith("SELECT"), label
        assert re.search(r"count\(|min\(|max\(|sum\(|approx_distinct\(", stmt), label
        if "date_shapes" in label:
            assert "regexp_replace" in stmt and "'[0-9]', '9'" in stmt


def test_all_sql_statements_pass_runner_guard():
    for name in ("create_database.sql", "create_raw_tables.sql", "create_typed_views.sql", "validate_data.sql"):
        for label, stmt in split_statements(SQL[name]):
            assert ALLOWED.match(stmt) and not FORBIDDEN.search(stmt), f"{name}: {label}"


def test_no_forbidden_services_in_sql():
    code = "\n".join(l for t in SQL.values() for l in t.splitlines() if not l.strip().startswith("--")).lower()
    for word in ("crawler", "bedrock", "rds", "insert into", "drop table"):
        assert not re.search(rf"\b{word}\b", code), word



def test_workgroup_guardrails():
    wg = json.loads((ROOT / "infrastructure" / "config" / "workgroup.json").read_text())
    assert wg["EnforceWorkGroupConfiguration"] is True
    assert 0 < wg["BytesScannedCutoffPerQuery"] <= CAT["aws"]["bytes_scanned_cutoff"]
    rc = wg["ResultConfiguration"]
    assert rc["EncryptionConfiguration"]["EncryptionOption"] in ("SSE_S3", "SSE_KMS")
    assert rc["OutputLocation"].startswith(f"s3://{CAT['aws']['results_bucket']}/")


def test_iam_policy_least_privilege():
    pol = json.loads((ROOT / "infrastructure" / "policies" / "analytics-dev-user-policy.json").read_text())
    allows = [s for s in pol["Statement"] if s["Effect"] == "Allow"]
    for s in allows:
        actions = s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
        assert not any(a.endswith(":*") or a == "*" for a in actions), s["Sid"]
        if any(a.startswith("s3:Put") or a.startswith("s3:Delete") for a in actions):
            assert all(CAT["aws"]["source_bucket"] not in r for r in s["Resource"]), s["Sid"]
    denied = {a for s in pol["Statement"] if s["Effect"] == "Deny" for a in s["Action"]}
    assert {"glue:CreateCrawler", "bedrock:*", "rds:*", "s3:PutObject", "s3:DeleteObject"} <= denied


def test_no_credentials_in_repo():
    pattern = re.compile(r"AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|aws_secret_access_key\s*=", re.IGNORECASE)
    for p in ROOT.rglob("*"):
        if p.is_file() and ".venv" not in p.parts and p.suffix in {".py", ".sql", ".yaml", ".json", ".md", ".txt"}:
            assert not pattern.search(p.read_text(encoding="utf-8", errors="ignore")), p
