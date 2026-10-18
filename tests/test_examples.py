"""Golden examples: offline compile checks, plus a live comparison against hand-written SQL."""
import math

import pytest

from bi_service.athena_executor import AthenaExecutor
from bi_service.config import ServiceConfig
from bi_service.query_service import QueryService
from bi_service.semantic_loader import load_semantic_model


def test_at_least_five_examples_compile(model, compile_only):
    assert len(model.examples) >= 5
    for ex in model.examples.values():
        vq, _ = compile_only(ex.spec)
        assert vq.metric.approved, ex.id


def _close(a, b):
    if a is None or b is None:
        return a is b
    if isinstance(a, (int, float)) or hasattr(a, "as_tuple"):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-6)
    return str(a) == str(b)


@pytest.mark.live
@pytest.mark.parametrize("example_id", [e for e in load_semantic_model().examples])
def test_live_service_matches_reference_sql(example_id):
    svc = QueryService.from_defaults()
    ex = svc.model.examples[example_id]
    result = svc.run(ex.spec)
    assert result.status == "success", result.errors
    ref = svc.executor.execute(ex.reference_sql, max_rows=1001)
    assert ref.state == "SUCCEEDED", ref.error_message
    keep = [i for i, c in enumerate(result.columns) if c.role != "component"]   # dimensions + value
    got = sorted(([r[i] for i in keep] for r in result.rows), key=str)
    exp = sorted(ref.rows, key=str)
    assert len(got) == len(exp)
    for g, e in zip(got, exp):
        assert all(_close(x, y) for x, y in zip(g, e)), (example_id, g, e)
