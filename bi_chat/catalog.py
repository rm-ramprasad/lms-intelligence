"""Business catalog: glossary + value aliases + dimension values + language rules, cross-validated
against the bi_service semantic model and compiled into a phrase index.

Every phrase maps to exactly one entry. A phrase that legitimately means two KPIs must be
declared under `ambiguous:` in glossary.yaml; any other collision is a configuration error.
"""
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from bi_service.config import SEMANTIC_DIR
from bi_service.semantic_loader import SemanticModel

from .normalizer import phrase_key


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    kind: str          # metric | metric_weak | metric_ambiguous | metric_pending | dimension | value
    target: object     # metric id | (metric ids) | dimension id | (dimension id, value)

    def describe(self) -> str:
        return f"{self.kind}:{self.target}"


@dataclass(frozen=True)
class Kpi:
    metric_id: str
    name: str
    short: str
    long: str
    formula: str
    synonyms: tuple[str, ...]
    behavior_note: str
    owner: str
    status: str
    related: tuple[str, ...] = ()
    weak_synonyms: tuple[str, ...] = ()


@dataclass
class Catalog:
    kpis: dict[str, Kpi]
    dimension_synonyms: dict[str, tuple[str, ...]]
    values: dict[str, list]                 # dimension id -> canonical values
    terms: list[dict]
    language: dict
    index: dict[tuple[str, ...], Entry] = field(default_factory=dict)
    code_index: dict[str, Entry] = field(default_factory=dict)   # UPPERCASE token -> value entry
    max_phrase_len: int = 1

    def stop_words(self) -> set[str]:
        return set(self.language["stop_words"])


def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _add(cat: Catalog, phrase: str, entry: Entry, where: str) -> None:
    key = phrase_key(phrase)
    if not key:
        raise CatalogError(f"{where}: empty phrase {phrase!r}")
    prev = cat.index.get(key)
    if prev is not None and prev != entry:
        raise CatalogError(f"phrase '{phrase}' maps to both {prev.describe()} and {entry.describe()} ({where})")
    cat.index[key] = entry
    cat.max_phrase_len = max(cat.max_phrase_len, len(key))


def load_catalog(model: SemanticModel, semantic_dir: Path = SEMANTIC_DIR) -> Catalog:
    g = _read(semantic_dir / "glossary.yaml")
    aliases = _read(semantic_dir / "value_aliases.yaml")["dimensions"]
    values = _read(semantic_dir / "dimension_values.yaml")["values"]
    lang = _read(semantic_dir / "language.yaml")

    kpis = {}
    for k in g["kpis"]:
        mid = k["metric_id"]
        if mid not in model.metrics:
            raise CatalogError(f"glossary kpi {mid}: not in metrics.yaml")
        if (k["status"] == "approved") != model.metrics[mid].approved:
            raise CatalogError(f"glossary kpi {mid}: status {k['status']} disagrees with metrics.yaml")
        if mid in kpis:
            raise CatalogError(f"glossary kpi {mid}: duplicate")
        kpis[mid] = Kpi(mid, k["name"], k["short"], k["long"], k["formula"], tuple(k["synonyms"]),
                        k["behavior_note"], k["owner"], k["status"], tuple(k.get("related", ())),
                        tuple(k.get("weak_synonyms", ())))
    missing = {m for m, x in model.metrics.items() if x.approved} - set(kpis)
    if missing:
        raise CatalogError(f"approved metrics without glossary entry: {sorted(missing)}")

    dim_syn = {}
    for d in g["dimensions"]:
        if d["dimension_id"] not in model.dimensions:
            raise CatalogError(f"glossary dimension {d['dimension_id']}: not in dimensions.yaml")
        dim_syn[d["dimension_id"]] = tuple(d["synonyms"])

    cat = Catalog(kpis, dim_syn, values, g.get("terms", []), lang)

    for k in kpis.values():
        kind = "metric" if k.status == "approved" else "metric_pending"
        for s in k.synonyms:
            _add(cat, s, Entry(kind, k.metric_id), f"kpi {k.metric_id}")
        _add(cat, k.name, Entry(kind, k.metric_id), f"kpi {k.metric_id} name")
    weak: dict[tuple, Entry] = {}
    for k in kpis.values():
        for s in k.weak_synonyms:
            weak[phrase_key(s)] = Entry("metric_weak", k.metric_id)
    for phrase, ids in (g.get("ambiguous") or {}).items():
        for i in ids:
            if i not in kpis or kpis[i].status != "approved":
                raise CatalogError(f"ambiguous '{phrase}': {i} is not an approved KPI")
        _add(cat, phrase, Entry("metric_ambiguous", tuple(ids)), "ambiguous")
    for key, e in weak.items():   # weak phrases only fill gaps; they never override strong phrases
        if key not in cat.index:
            cat.index[key] = e
            cat.max_phrase_len = max(cat.max_phrase_len, len(key))

    for did, syns in dim_syn.items():
        for s in syns:
            _add(cat, s, Entry("dimension", did), f"dimension {did}")

    for did, spec in aliases.items():
        dim = model.dimensions.get(did)
        if dim is None:
            raise CatalogError(f"value_aliases: unknown dimension {did}")
        if dim.type == "boolean":
            allowed = None
        else:
            allowed = values.get(did)
            if allowed is None:
                raise CatalogError(f"value_aliases {did}: no values in dimension_values.yaml (run refresh_values.py)")
            if spec.get("match_values", True):
                for v in allowed:
                    _add(cat, v, Entry("value", (did, v)), f"value {did}")
        for phrase, target in (spec.get("aliases") or {}).items():
            if allowed is None and not isinstance(target, bool):
                raise CatalogError(f"value_aliases {did}.{phrase}: boolean dimension needs true/false")
            if allowed is not None and target not in allowed:
                raise CatalogError(f"value_aliases {did}.{phrase}: '{target}' is not a known value")
            _add(cat, str(phrase), Entry("value", (did, target)), f"alias {did}")
        for code, target in (spec.get("code_aliases") or {}).items():
            if target not in (allowed or []):
                raise CatalogError(f"value_aliases {did}.{code}: '{target}' is not a known value")
            cat.code_index[str(code)] = Entry("value", (did, target))
    for did in values:
        if did not in aliases and did in model.dimensions:   # values of dimensions without an alias block
            for v in values[did]:
                _add(cat, v, Entry("value", (did, v)), f"value {did}")

    stop = cat.stop_words()
    for key, e in cat.index.items():
        if len(key) == 1 and key[0] in stop and e.kind in ("value",):
            raise CatalogError(f"value phrase '{key[0]}' is also a stop word")
    return cat
