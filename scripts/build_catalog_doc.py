"""Render docs/catalog.md from the governed catalog YAML (same source as the app's Catalog page).

    python scripts/build_catalog_doc.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bi_chat.catalog import load_catalog  # noqa: E402
from bi_service.semantic_loader import load_semantic_model  # noqa: E402

UNITS = {"currency": "₹ Cr", "percent": "%", "integer": "count"}


def build() -> str:
    model = load_semantic_model()
    cat = load_catalog(model)
    out = ["# KPI catalog", "",
           "_Generated from `semantic/glossary.yaml`, `value_aliases.yaml` and `dimension_values.yaml` "
           "by `scripts/build_catalog_doc.py`. Do not edit by hand._", "", "## KPIs", "",
           "| KPI | Definition | Formula | Unit | Behavior | Synonyms | Owner | Status |",
           "|---|---|---|---|---|---|---|---|"]
    for k in cat.kpis.values():
        m = model.metrics[k.metric_id]
        out.append(f"| **{k.name}** (`{k.metric_id}`) | {k.long} | {k.formula} | {UNITS.get(m.format.get('type'), '')} | "
                   f"{k.behavior_note} | {', '.join(k.synonyms)} | {k.owner} | {k.status} |")
    out += ["", "## Breakdowns and filters", "", "| Dimension | Synonyms | Values |", "|---|---|---|"]
    for d in model.dimensions.values():
        vals = cat.values.get(d.id, ["Yes", "No"] if d.type == "boolean" else ["(time grain)"])
        out.append(f"| {d.label} (`{d.id}`) | {', '.join(cat.dimension_synonyms.get(d.id, ()))} | "
                   f"{', '.join(map(str, vals))} |")
    out += ["", "## Terms", ""] + [f"- **{t['term']}**: {t['definition']}" for t in cat.terms]
    out += ["", "## Example questions", ""]
    for ex in model.examples.values():
        out.append(f"- {ex.question}" + (f" _(also: {'; '.join(ex.paraphrases)})_" if ex.paraphrases else ""))
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    path = ROOT / "docs" / "catalog.md"
    path.write_text(build(), encoding="utf-8", newline="\n")
    print(f"wrote {path.relative_to(ROOT)}")
