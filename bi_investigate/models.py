"""Briefing snapshot schema (persisted JSON) and in-memory analysis results."""
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class Window(BaseModel):
    label: str              # e.g. "Feb 2026", "Q3 FY26", "FY26 YTD"
    start: date
    end: date


class Comparison(BaseModel):
    kind: Literal["mom", "qoq", "yoy", "fytd"]
    a: Window
    b: Window


class ChangeSummary(BaseModel):
    value_a: float | None
    value_b: float | None
    delta: float | None
    delta_pct: float | None
    num_a: float | None = None
    num_b: float | None = None
    den_a: float | None = None
    den_b: float | None = None


class Contribution(BaseModel):
    dimension: str
    segment: str
    value_a: float | None
    value_b: float | None
    delta: float                 # contribution to the total change (sums to total Δ across a dimension)
    share: float | None          # delta / total Δ
    mix: float | None = None     # ratios only
    rate: float | None = None    # ratios only


class BridgeStep(BaseModel):
    label: str
    amount: float
    accounts: int


class Bridge(BaseModel):
    kind: Literal["par90", "npa"]
    opening: float
    closing: float
    steps: list[BridgeStep]


class RollRate(BaseModel):
    from_bucket: str
    to_bucket: str
    accounts: int
    rate: float
    avg_rate_12m: float | None


class Concentration(BaseModel):
    accounts_changed: int
    gross_movement: float
    accounts_for_50pct: int | None
    accounts_for_80pct: int | None


class Unusual(BaseModel):
    z: float | None
    rank_of_13: int | None           # 1 = largest absolute monthly move in the last 13 months
    series: list[tuple[str, float | None]] = Field(default_factory=list)   # (month-end ISO, value) for charts


class Finding(BaseModel):
    question_id: str
    kind: str                        # change | contributor | bridge | mix_rate | num_den | roll_rate | deterioration | concentration
    text: str
    score: float                     # ranking for "what leadership should look at"
    kpi: str | None = None
    dimension: str | None = None
    segment: str | None = None
    drilldown: dict | None = None    # Tier 1 QuerySpec for "drill down in Ask"


class QuestionResult(BaseModel):
    id: str
    theme: str
    question: str
    answer: str
    rag: Literal["green", "amber", "red", "none"] = "none"
    kpi: str | None = None
    comparisons: dict[str, ChangeSummary] = Field(default_factory=dict)   # kind -> summary
    comparison_windows: dict[str, Comparison] = Field(default_factory=dict)
    contributions: dict[str, list[Contribution]] = Field(default_factory=dict)  # dimension -> rows (mom)
    mix_rate: dict[str, dict[str, float]] | None = None   # dimension -> {"mix", "rate"}
    num_den: dict[str, float] | None = None
    bridge: Bridge | None = None
    roll_rates: list[RollRate] = Field(default_factory=list)
    concentration: Concentration | None = None
    unusual: Unusual | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    drilldowns: list[dict] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)   # analyses skipped (e.g. no data before window)


class KpiTile(BaseModel):
    kpi: str
    label: str
    value: float | None
    delta: float | None
    delta_label: str
    rag: Literal["green", "amber", "red", "none"] = "none"


class Briefing(BaseModel):
    as_of: date
    status: Literal["DRAFT", "APPROVED"] = "DRAFT"
    approved_by: str | None = None
    approved_at: str | None = None
    generated_at: str
    playbook_version: str
    tiles: list[KpiTile]
    top_findings: list[Finding]
    questions: list[QuestionResult]
    query_count: int = 0
    bytes_scanned: int = 0
    elapsed_ms: int = 0
    notes: list[str] = Field(default_factory=list)
