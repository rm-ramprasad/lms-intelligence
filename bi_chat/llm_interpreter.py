"""LLM interpreter (Bedrock tool use). Same result types as the deterministic interpreter.

The model can only (a) propose a QuerySpec through `run_query`, (b) ask a clarification limited to
approved KPI ids, or (c) refuse. Every proposed spec is validated by bi_service; one corrective retry
with the error codes, then a clarification. Invalid specs never reach Athena.
"""
from datetime import date

from pydantic import ValidationError

from bi_service.query_validator import QueryValidationError

from .interpreter import Clarify, ConversationState, DeterministicInterpreter, Interpreted, Option, Refuse
from .llm_client import BedrockClient, LlmError
from .prompt_builder import build_system_prompt, build_tools, build_user_message, prompt_hash

REFUSE_TEXT = {
    "customer_or_account_level": "I can only answer portfolio-level questions. Customer- or account-level details are not available here.",
    "explain_or_forecast": "I can report approved KPIs for past periods, but I can't explain causes or make forecasts.",
    "not_an_approved_kpi": "That isn't one of the approved KPIs. See the KPI catalog for what's available.",
}


class LlmInterpreter:
    def __init__(self, det: DeterministicInterpreter, client: BedrockClient):
        self.det = det
        self.model = det.model
        self.cat = det.cat
        self.client = client
        self.tools = build_tools(self.model, self.cat)

    # ---- tool input -> QuerySpec dict --------------------------------------------------------
    def _to_spec(self, inp: dict) -> dict:
        spec = {"metric": inp.get("metric"), "dimensions": list(dict.fromkeys(inp.get("dimensions") or [])),
                "filters": []}
        for f in inp.get("filters") or []:
            dim = self.model.dimensions.get(f.get("dimension"))
            op, val = f.get("op", "eq"), f.get("value")
            if dim is None:
                spec["filters"].append(f)        # let the validator reject it with a clear code
                continue
            if dim.type == "boolean":
                if isinstance(val, list):
                    val = val[0] if val else None
                if isinstance(val, str):
                    val = {"true": True, "yes": True, "false": False, "no": False}.get(val.strip().lower(), val)
                if op == "ne" and isinstance(val, bool):
                    op, val = "eq", not val
                op = "eq"
            else:
                if op in ("in", "not_in") and not isinstance(val, list):
                    val = [val]
                if op in ("eq", "ne") and isinstance(val, list):
                    op = "in" if op == "eq" else "not_in"
                if isinstance(val, list) and len(val) == 1 and op in ("in", "not_in"):
                    op, val = ("eq" if op == "in" else "ne"), val[0]
            spec["filters"].append({"dimension": f.get("dimension"), "op": op, "value": val})
        period = inp.get("period")
        if period and period.get("kind"):
            spec["period"] = {k: v for k, v in period.items() if v is not None}
        if inp.get("rank"):
            spec["sort"] = [{"by": "value", "direction": "desc" if inp["rank"] == "highest_first" else "asc"}]
        if inp.get("limit"):
            spec["limit"] = int(inp["limit"])
        return spec

    def _validate(self, spec: dict, anchor: date) -> tuple[list[str], list[str]]:
        notes = self.det._finalise(spec, anchor)
        try:
            self.det.validate(spec)
            return [], notes
        except QueryValidationError as e:
            return [f"{c}: {m}" for c, m, _ in e.errors], notes
        except ValidationError as e:
            return [f"invalid_request: {err['msg']} at {'.'.join(map(str, err['loc']))}" for err in e.errors()], notes

    # ---- main entry ----------------------------------------------------------------------------
    def interpret(self, question: str, state: ConversationState | None = None, *, anchor: date,
                  force_metric: str | None = None, **_ignored):
        system = build_system_prompt(self.model, self.cat, anchor)
        phash = prompt_hash(system, self.tools)
        prev = state.last_spec if state else None
        messages = [{"role": "user", "content": [{"text": build_user_message(question, prev, force_metric)}]}]
        meta = {"model": self.client.s.model_label, "prompt_hash": phash, "calls": 0,
                "input_tokens": 0, "output_tokens": 0, "latency_ms": 0, "cost_usd": 0.0,
                "guardrail": self.client.use_guardrail}

        for attempt in range(2):
            r = self.client.converse(system, messages, self.tools)     # LlmError propagates to the router
            meta["calls"] += 1
            meta["input_tokens"] += r.input_tokens
            meta["output_tokens"] += r.output_tokens
            meta["latency_ms"] += r.latency_ms
            meta["cost_usd"] += self.client.cost(r)

            if r.guardrail_blocked:
                return Refuse("This request was blocked by the content safety guardrail.", [], reason="guardrail",
                              source="ai", meta=meta)
            if r.tool_name == "cannot_answer":
                reason = (r.tool_input or {}).get("reason", "not_an_approved_kpi")
                return Refuse(REFUSE_TEXT.get(reason, REFUSE_TEXT["not_an_approved_kpi"]),
                              self.det._suggestions(question), reason="ai_" + reason, source="ai", meta=meta)
            if r.tool_name == "ask_clarification":
                inp = r.tool_input or {}
                opts = [Option(self.det._name(m), {"question": question, "force_metric": m})
                        for m in inp.get("options", []) if m in self.model.metrics and self.model.metrics[m].approved]
                if len(opts) >= 2:
                    return Clarify(str(inp.get("question") or "Which KPI do you mean?")[:200], opts,
                                   reason="ambiguous", source="ai", meta=meta)
                break
            if r.tool_name != "run_query":
                break

            spec = self._to_spec(r.tool_input or {})
            if force_metric:
                spec["metric"] = force_metric
            errors, notes = self._validate(spec, anchor)
            if not errors:
                understood = [("kpi", self.det._name(spec["metric"]))] + [("breakdown", d) for d in spec["dimensions"]]
                return Interpreted(spec, understood, prev is not None, notes, source="ai", meta=meta)
            if attempt == 0:   # one corrective retry with the validator's error codes
                messages += [
                    {"role": "assistant", "content": r.raw_message.get("content", [])},
                    {"role": "user", "content": [{"toolResult": {
                        "toolUseId": r.tool_use_id, "status": "error",
                        "content": [{"text": "Rejected by the query validator: " + "; ".join(errors)
                                     + ". Call a tool again using only listed ids and values."}]}}]},
                ]
        return Clarify("I couldn't map that to an approved query. Try rephrasing, or pick a suggestion.",
                       self.det._suggestions(question), reason="ai_unmapped", source="ai", meta=meta)
