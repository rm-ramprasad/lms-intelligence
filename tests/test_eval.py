"""Interpretation evaluation: the eval set and golden paraphrases must pass 100%, deterministically."""
from datetime import date
from pathlib import Path

import pytest
import yaml

from bi_chat.catalog import load_catalog
from bi_chat.interpreter import Clarify, ConversationState, DeterministicInterpreter, Interpreted, Refuse
from bi_service.query_models import QuerySpec
from bi_service.query_service import QueryService
from bi_service.query_validator import validate

EVAL = yaml.safe_load((Path(__file__).parent / "eval" / "questions.yaml").read_text(encoding="utf-8"))
ANCHOR = date.fromisoformat(EVAL["anchor"])
KINDS = {"interpreted": Interpreted, "clarify": Clarify, "refuse": Refuse}


@pytest.fixture(scope="module")
def interpreter(model, config):
    svc = QueryService(model, None, config)
    return DeterministicInterpreter(model, load_catalog(model), svc.compile)


def _norm_filters(fs):
    out = set()
    for f in fs:
        v = f["value"]
        out.add((f["dimension"], f["op"], tuple(v) if isinstance(v, list) else v))
    return out


@pytest.mark.parametrize("case", EVAL["cases"], ids=lambda c: c["q"])
def test_eval_case(interpreter, case):
    state = ConversationState(case["context"]) if case.get("context") else None
    r = interpreter.interpret(case["q"], state, anchor=ANCHOR)
    assert isinstance(r, KINDS[case["expect"]]), (case["q"], r)
    for key, expected in (case.get("spec") or {}).items():
        got = r.spec.get(key, [] if key in ("dimensions", "filters") else None)
        if key == "filters":
            assert _norm_filters(got) == _norm_filters(expected), case["q"]
        else:
            assert got == expected, (case["q"], key, got)


def test_interpretation_is_deterministic(interpreter):
    for case in EVAL["cases"]:
        a = interpreter.interpret(case["q"], None, anchor=ANCHOR)
        b = interpreter.interpret(case["q"], None, anchor=ANCHOR)
        assert type(a) is type(b) and getattr(a, "spec", None) == getattr(b, "spec", None)


def _resolved(spec, model):
    vq = validate(QuerySpec.model_validate(spec), model, default_limit=100, max_limit=1000)
    return (vq.metric.id, tuple(d.id for d in vq.dimensions),
            frozenset((f.dimension.id, f.op, f.values) for f in vq.filters),
            vq.period.start, vq.period.end)


def golden_cases():
    from bi_service.semantic_loader import load_semantic_model
    return [(ex.id, p) for ex in load_semantic_model().examples.values() for p in ex.paraphrases]


@pytest.mark.parametrize("example_id, phrasing", golden_cases())
def test_golden_paraphrases_match_example_spec(interpreter, model, example_id, phrasing):
    r = interpreter.interpret(phrasing, None, anchor=ANCHOR)
    assert isinstance(r, Interpreted), (phrasing, r)
    expected = model.examples[example_id].spec
    assert _resolved(r.spec, model) == _resolved(expected, model), phrasing


def test_each_golden_example_has_three_paraphrases(model):
    assert all(len(ex.paraphrases) >= 3 for ex in model.examples.values())
