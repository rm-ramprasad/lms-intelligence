"""Load and resolve semantic/tables.yaml. Pure local code, no AWS calls."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TABLES_YAML = ROOT / "semantic" / "tables.yaml"

TYPES = {
    "string": "varchar",
    "int": "integer",
    "bigint": "bigint",
    "amount": "decimal(18,2)",
    "rate": "decimal(12,8)",
    "date": "date",
    "timestamp": "timestamp",
    "bool": "boolean",
}
PII_LEVELS = {"none", "identifier", "quasi", "sensitive", "direct"}
EXCLUDED_PII = {"quasi", "sensitive", "direct"}
QUALITY = {"placeholder", "all_zero", "constant"}


def snake(src: str) -> str:
    s = re.sub(r"[^0-9A-Za-z]+", "_", src)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s = re.sub(r"([A-Za-z])([0-9])", r"\1_\2", s)
    s = re.sub(r"([0-9])([A-Za-z])", r"\1_\2", s)
    return re.sub(r"_+", "_", s).strip("_").lower()


def load_catalog(path: Path = TABLES_YAML) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolved_columns(table: dict) -> list[dict]:
    default_pii = table.get("default_pii", "none")
    return [
        {
            "src": c["src"],
            "name": c.get("name", snake(c["src"])),
            "type": c.get("type", "string"),
            "pii": c.get("pii", default_pii),
            "review": c.get("review"),
            "quality": c.get("quality"),
        }
        for c in table["columns"]
    ]


def view_columns(table: dict) -> list[dict]:
    cols = resolved_columns(table)
    if table.get("expose_pii_in_view"):  # mock-data override; classifications are kept
        return cols
    return [c for c in cols if c["pii"] not in EXCLUDED_PII]
