"""Deterministic interpreter: question (+ conversation state) -> Interpreted | Clarify | Refuse.

Decision rules (approved plan, Milestone 3):
  refuse   identifiers / customer-level / why-forecast questions, or a pending (unapproved) KPI
  clarify  no KPI, more than one KPI (D7), an ambiguous KPI phrase, unrecognised words,
           a flow KPI without a period, or a spec the query service rejects
  execute  exactly one KPI and every meaningful word recognised

The Interpreter protocol is the seam for a future LLM interpreter (Milestone 4): it must return
the same result types.
"""
import copy
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Protocol

from pydantic import ValidationError

from bi_service.query_validator import QueryValidationError
from bi_service.semantic_loader import SemanticModel

from .catalog import Catalog
from .example_matcher import ExampleMatcher
from .matcher import Matcher, Parse


@dataclass
class Option:
    label: str
    action: dict      # {"question": q, "force_metric": id} | {"question": q, "force_period": {...}}
                      # | {"question": q, "ignore": [...]} | {"example": id}


@dataclass
class Interpreted:
    spec: dict
    understood: list[tuple[str, str]] = field(default_factory=list)
    is_followup: bool = False
    notes: list[str] = field(default_factory=list)
    source: str = "catalog"          # catalog | ai
    meta: dict = field(default_factory=dict)   # AI: model, tokens, cost, latency, prompt hash


@dataclass
class Clarify:
    message: str
    options: list[Option] = field(default_factory=list)
    reason: str = "other"            # no_kpi | unrecognised | ambiguous | multi_kpi | period | invalid | other
    source: str = "catalog"
    meta: dict = field(default_factory=dict)


@dataclass
class Refuse:
    message: str
    options: list[Option] = field(default_factory=list)
    reason: str = "other"            # identifier | out_of_scope | pending | guardrail | other
    source: str = "catalog"
    meta: dict = field(default_factory=dict)


Result = Interpreted | Clarify | Refuse


@dataclass
class ConversationState:
    last_spec: dict | None = None


class Interpreter(Protocol):
    def interpret(self, question: str, state: ConversationState | None, *, anchor: date, **kw) -> Result: ...


REFUSALS = {
    "identifier": "I can only answer portfolio-level questions. Customer- or account-level details are not available here.",
    "out_of_scope": "I can report approved KPIs for past periods, but I can't explain causes or make forecasts.",
}


class DeterministicInterpreter:
    def __init__(self, model: SemanticModel, catalog: Catalog, validate: Callable[[dict], object]):
        self.model = model
        self.cat = catalog
        self.matcher = Matcher(catalog)
        self.examples = ExampleMatcher(model.examples, catalog.stop_words())
        self.validate = validate   # e.g. QueryService.compile; raises on invalid specs

    # ---- helpers -----------------------------------------------------------------------------
    def _name(self, metric_id: str) -> str:
        return self.cat.kpis[metric_id].name if metric_id in self.cat.kpis else metric_id

    def _suggestions(self, question: str) -> list[Option]:
        return [Option(q, {"example": ex_id}) for ex_id, q in self.examples.suggest(question)]

    def _filters(self, p: Parse) -> list[dict]:
        out = []
        for dim_id, f in p.filters.items():
            dim = self.model.dimensions[dim_id]
            vals, neg = f["values"], f["negate"]
            if dim.type == "boolean":
                v = vals[-1]
                out.append({"dimension": dim_id, "op": "eq", "value": (not v) if neg else v})
            elif len(vals) == 1:
                out.append({"dimension": dim_id, "op": "ne" if neg else "eq", "value": vals[0]})
            else:
                out.append({"dimension": dim_id, "op": "not_in" if neg else "in", "value": list(vals)})
        return out

    def _apply_top(self, spec: dict, p: Parse) -> None:
        if p.top:
            direction, n = p.top
            spec["sort"] = [{"by": "value", "direction": direction}]
            if n:
                spec["limit"] = n

    def _finalise(self, spec: dict, anchor: date) -> list[str]:
        notes = []
        metric = self.model.metrics[spec["metric"]]
        dims = [self.model.dimensions[d] for d in spec.get("dimensions", []) if d in self.model.dimensions]
        has_time = any(d.kind == "time" for d in dims)
        if has_time and not spec.get("period"):
            spec["period"] = self.matcher.periods.trend_default(anchor)
            notes.append(f"No period given for the trend; showing the last {self.matcher.periods.trend_default_months} months.")
        spec["include_total"] = bool(dims) and not (metric.behavior == "snapshot" and has_time)
        return notes

    # ---- main entry --------------------------------------------------------------------------
    def interpret(self, question: str, state: ConversationState | None = None, *, anchor: date,
                  force_metric: str | None = None, force_period: dict | None = None,
                  ignore: list[str] | None = None) -> Result:
        p = self.matcher.parse(question, anchor)
        if p.refuse:
            return Refuse(REFUSALS[p.refuse], self._suggestions(question), reason=p.refuse)
        if ignore:
            p.leftovers = [w for w in p.leftovers if w not in ignore]

        if force_metric:
            p.metrics, p.ambiguous, p.weak_metrics, p.pending = [force_metric], [], [], []
        if force_period:
            from .period_parser import PeriodMatch
            p.period = PeriodMatch(force_period, set(), "")

        # metric resolution
        metric = None
        if len(p.metrics) > 1:
            return Clarify("Please ask about one KPI at a time. Which one?",
                           [Option(self._name(m), {"question": question, "force_metric": m}) for m in p.metrics],
                           reason="multi_kpi")
        if p.metrics:
            metric = p.metrics[0]
        elif p.pending:
            return Refuse(f"'{self._name(p.pending[0])}' is not an approved KPI yet.", self._suggestions(question),
                          reason="pending")
        elif p.ambiguous:
            ids = p.ambiguous[0]
            return Clarify("Which KPI do you mean?",
                           [Option(self._name(m), {"question": question, "force_metric": m}) for m in ids],
                           reason="ambiguous")
        elif p.weak_metrics:
            metric = p.weak_metrics[0]

        has_parts = bool(p.dimensions or p.filters or p.period or p.top or p.clear_filters)
        prev = state.last_spec if state else None
        followup = prev is not None and (metric is None or p.followup_cue) and (has_parts or metric is not None)

        if metric is None and not followup:
            opts = self._suggestions(question) or [
                Option(k.name, {"question": question, "force_metric": k.metric_id})
                for k in list(self.cat.kpis.values())[:6] if k.status == "approved"]
            return Clarify("Which KPI would you like? Try one of these:", opts, reason="no_kpi")

        if p.leftovers:
            words = ", ".join(f"'{w}'" for w in dict.fromkeys(p.leftovers))
            opts = [Option(f"Ignore {words} and continue", {"question": question, "ignore": list(p.leftovers),
                                                             **({"force_metric": metric} if metric else {})})]
            return Clarify(f"I didn't recognise {words}.", opts + self._suggestions(question), reason="unrecognised")

        # build the spec
        if followup:
            spec = copy.deepcopy(prev)
            if metric:
                spec["metric"] = metric
            if p.dimensions:
                spec["dimensions"] = list(dict.fromkeys(spec.get("dimensions", []) + p.dimensions)) if p.also \
                    else list(p.dimensions)
            if p.clear_filters:
                spec["filters"] = []
            if p.filters:
                new = self._filters(p)
                touched = {f["dimension"] for f in new}
                spec["filters"] = [f for f in spec.get("filters", []) if f["dimension"] not in touched] + new
            if p.period:
                spec["period"] = p.period.period
            if p.top:
                spec.pop("sort", None)
                spec.pop("limit", None)
            spec.pop("include_total", None)
        else:
            spec = {"metric": metric, "dimensions": list(p.dimensions), "filters": self._filters(p)}
            if p.period:
                spec["period"] = p.period.period
        self._apply_top(spec, p)
        if spec.get("sort") and not any(s["by"] == "value" for s in spec["sort"]):
            spec.pop("sort")

        m = self.model.metrics[spec["metric"]]
        has_time = any(self.model.dimensions[d].kind == "time" for d in spec.get("dimensions", [])
                       if d in self.model.dimensions)
        if m.period_required and not spec.get("period") and not has_time:
            q = question
            return Clarify(f"{self._name(m.id)} is a flow over a period. Which period?", [
                Option("Latest month", {"question": q, "force_metric": m.id, "force_period": {"kind": "latest"}}),
                Option("This fiscal year to date", {"question": q, "force_metric": m.id,
                                                    "force_period": self.matcher.periods.parse(["ytd"], anchor).period}),
                Option("Last fiscal year", {"question": q, "force_metric": m.id,
                                            "force_period": self.matcher.periods.parse(["last", "year"], anchor).period}),
            ], reason="period")

        notes = self._finalise(spec, anchor)
        try:
            self.validate(spec)
        except QueryValidationError as e:
            msgs = "; ".join(msg for _, msg, _ in e.errors)
            return Clarify(f"I can't run that as asked: {msgs}.", self._suggestions(question), reason="invalid")
        except ValidationError:
            return Clarify("I couldn't turn that into a valid query.", self._suggestions(question), reason="invalid")
        return Interpreted(spec, p.understood, followup, notes)
