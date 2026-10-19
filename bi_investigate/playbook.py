"""Load and validate semantic/executive_playbook.yaml against the governed semantic model."""
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from bi_service.config import SEMANTIC_DIR
from bi_service.semantic_loader import SemanticModel

ANALYSES = {"change", "segments", "mix_rate", "num_den", "bridge_par90", "bridge_npa", "roll_rates", "concentration",
            "unusual", "deterioration", "concentration_shares", "summary"}
COMPARISONS = {"mom", "qoq", "yoy", "fytd"}
RATIO_ONLY = {"mix_rate", "num_den"}


class PlaybookError(ValueError):
    pass


@dataclass(frozen=True)
class Question:
    id: str
    theme: str
    question: str
    analyses: tuple[str, ...]
    owner: str
    kpi: str | None = None
    comparisons: tuple[str, ...] = ()
    rag: dict | None = None
    caveats: tuple[str, ...] = ()
    drilldowns: tuple[dict, ...] = ()
    concentration_metric: str | None = None
    deterioration_kpis: tuple[str, ...] = ()
    deterioration_dimensions: tuple[str, ...] = ()


@dataclass
class Playbook:
    version: str
    segment_dimensions: list[str]
    min_share: float
    max_per_dimension: int
    tiles: list[dict]
    questions: list[Question] = field(default_factory=list)


def load_playbook(model: SemanticModel, path: Path = SEMANTIC_DIR / "executive_playbook.yaml") -> Playbook:
    text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    approved = {m for m, x in model.metrics.items() if x.approved}

    def check_metric(mid, ctx):
        if mid not in approved:
            raise PlaybookError(f"{ctx}: '{mid}' is not an approved metric")

    def check_dim(did, ctx):
        if did not in model.dimensions:
            raise PlaybookError(f"{ctx}: unknown dimension '{did}'")

    for d in raw["segment_dimensions"]:
        check_dim(d, "segment_dimensions")
        if model.dimensions[d].kind != "attribute":
            raise PlaybookError(f"segment dimension {d} must be an attribute")
    for t in raw["tiles"]:
        check_metric(t["kpi"], "tile")

    qs, ids = [], set()
    for q in raw["questions"]:
        ctx = f"question {q['id']}"
        if q["id"] in ids:
            raise PlaybookError(f"{ctx}: duplicate id")
        ids.add(q["id"])
        unknown = set(q["analyses"]) - ANALYSES
        if unknown:
            raise PlaybookError(f"{ctx}: unknown analyses {sorted(unknown)}")
        if set(q.get("comparisons", [])) - COMPARISONS:
            raise PlaybookError(f"{ctx}: unknown comparison")
        if q.get("kpi"):
            check_metric(q["kpi"], ctx)
            is_ratio = model.metrics[q["kpi"]].measure.aggregation == "ratio"
            if RATIO_ONLY & set(q["analyses"]) and not is_ratio:
                raise PlaybookError(f"{ctx}: mix_rate/num_den need a ratio KPI")
        elif q["analyses"] != ["summary"]:
            raise PlaybookError(f"{ctx}: kpi required")
        for m in [q.get("concentration_metric")] + list(q.get("deterioration_kpis", [])):
            if m:
                check_metric(m, ctx)
        if "concentration" in q["analyses"]:
            cm = q.get("concentration_metric") or q["kpi"]
            if model.metrics[cm].measure.aggregation != "sum":
                raise PlaybookError(f"{ctx}: concentration_metric must be a sum metric")
        for d in q.get("deterioration_dimensions", []):
            check_dim(d, ctx)
        for dd in q.get("drilldowns", []):
            check_metric(dd["metric"], f"{ctx} drilldown")
            for d in dd.get("dimensions", []):
                check_dim(d, f"{ctx} drilldown")
        qs.append(Question(
            id=q["id"], theme=q["theme"], question=q["question"], analyses=tuple(q["analyses"]), owner=q["owner"],
            kpi=q.get("kpi"), comparisons=tuple(q.get("comparisons", [])), rag=q.get("rag"),
            caveats=tuple(q.get("caveats", [])), drilldowns=tuple(q.get("drilldowns", [])),
            concentration_metric=q.get("concentration_metric"),
            deterioration_kpis=tuple(q.get("deterioration_kpis", [])),
            deterioration_dimensions=tuple(q.get("deterioration_dimensions", []))))
    return Playbook(version=hashlib.sha256(text.encode()).hexdigest()[:12],
                    segment_dimensions=raw["segment_dimensions"], min_share=raw["materiality"]["min_share"],
                    max_per_dimension=raw["materiality"]["max_per_dimension"], tiles=raw["tiles"], questions=qs)
