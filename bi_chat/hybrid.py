"""Interpreter modes and routing.

  deterministic  catalog rules only (Milestone 3)
  ai             safety pre-filter, then the LLM
  hybrid         catalog rules first; the LLM only when they could not answer AND the question contains
                 words the catalog does not recognise (typos, informal phrasing, unseen synonyms)

The deterministic refusal pre-filter (identifiers, PII words, why/forecast) ALWAYS runs first, so such
questions are never sent to Bedrock. Any Bedrock failure falls back to the deterministic result.
"""
from datetime import date

from .interpreter import Clarify, ConversationState, DeterministicInterpreter, Refuse
from .llm_client import LlmError

MODES = ("deterministic", "ai", "hybrid")


class HybridInterpreter:
    def __init__(self, det: DeterministicInterpreter, llm=None):
        self.det = det
        self.llm = llm   # LlmInterpreter or None (Bedrock disabled / unavailable)

    def interpret(self, question: str, state: ConversationState | None = None, *, anchor: date,
                  mode: str = "hybrid", **force):
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode}")
        det_result = self.det.interpret(question, state, anchor=anchor, **force)
        if mode == "deterministic" or self.llm is None:
            return det_result
        if isinstance(det_result, Refuse) and det_result.reason in ("identifier", "out_of_scope"):
            return det_result                                   # pre-filter: never sent to Bedrock
        if mode == "hybrid":
            # AI only when the question contains words the catalog does not know. If every word was recognised,
            # the deterministic answer/clarification stands (prevents the model guessing a KPI for "by branch").
            if not isinstance(det_result, Clarify):
                return det_result
            leftovers = self.det.matcher.parse(question, anchor).leftovers
            if force.get("ignore"):
                leftovers = [w for w in leftovers if w not in force["ignore"]]
            if not leftovers:
                return det_result
        try:
            return self.llm.interpret(question, state, anchor=anchor, force_metric=force.get("force_metric"))
        except LlmError as e:
            det_result.meta = {**det_result.meta, "ai_fallback": str(e)[:100]}
            return det_result
