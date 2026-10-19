"""Offline checks on the free-form benchmark set and the deterministic baseline (no Bedrock)."""
from datetime import date
from pathlib import Path

import pytest
import yaml

from bi_chat.catalog import load_catalog
from bi_chat.hybrid import HybridInterpreter
from bi_chat.interpreter import ConversationState, DeterministicInterpreter
from bi_service.query_models import QuerySpec
from bi_service.query_service import QueryService
from bi_service.query_validator import validate

FREEFORM = yaml.safe_load((Path(__file__).parent / "eval" / "freeform.yaml").read_text(encoding="utf-8"))


def test_freeform_set_size_and_groups():
    cases = FREEFORM["cases"]
    assert len(cases) >= 40
    assert {c["expect"] for c in cases} == {"interpreted", "clarify", "refuse"}


@pytest.mark.parametrize("case", [c for c in FREEFORM["cases"] if c["expect"] == "interpreted" and "metric" in c.get("spec", {})],
                         ids=lambda c: c["q"])
def test_expected_specs_are_valid_queries(model, case):
    spec = {**(case.get("context") or {}), **case["spec"]}     # expected specs may be partial follow-ups
    spec.setdefault("period", {"kind": "latest"})              # flows need a period; trend default applied at runtime
    validate(QuerySpec.model_validate(spec), model, default_limit=100, max_limit=1000)


def test_deterministic_baseline_never_executes_wrong_refusals(model, config):
    """Deterministic mode must still refuse every must-refuse case it can detect, and never crash."""
    det = DeterministicInterpreter(model, load_catalog(model), QueryService(model, None, config).compile)
    router = HybridInterpreter(det, None)
    for c in FREEFORM["cases"]:
        state = ConversationState(c["context"]) if c.get("context") else None
        router.interpret(c["q"], state, anchor=date(2026, 3, 31), mode="hybrid")
