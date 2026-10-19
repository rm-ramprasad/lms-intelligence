"""Deterministic natural-language interpreter over bi_service (Milestone 3).

No LLM is used. Questions are matched against the governed catalog (semantic/glossary.yaml,
value_aliases.yaml, dimension_values.yaml, language.yaml) and turned into a QuerySpec, a
clarification, or a refusal. Depends on bi_service; nothing in bi_service depends on this package.
"""
from .interpreter import Clarify, DeterministicInterpreter, Interpreted, Option, Refuse

__all__ = ["Clarify", "DeterministicInterpreter", "Interpreted", "Option", "Refuse"]
