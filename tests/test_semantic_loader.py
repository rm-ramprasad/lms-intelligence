import shutil

import pytest
import yaml

from bi_service.semantic_loader import SemanticConfigError, load_semantic_model
from conftest import ROOT


def test_model_loads_with_only_governed_view(model):
    assert set(model.views) == {"v_account_summary"}
    assert model.views["v_account_summary"].qualified == "analytics_poc.v_account_summary"


def test_at_least_five_approved_metrics(model):
    assert sum(m.approved for m in model.metrics.values()) >= 5


def test_every_metric_has_required_fields(model):
    for m in model.metrics.values():
        assert m.name and m.definition and m.format and m.validation_sql.strip()
        assert m.behavior in ("snapshot", "period")
        assert "analytics_poc.v_account_summary" in m.validation_sql


def test_snapshot_semi_additive_period_additive(model):
    for m in model.metrics.values():
        if m.measure.aggregation == "ratio":
            assert m.additivity == "non_additive"
        else:
            assert m.additivity == {"snapshot": "semi_additive", "period": "additive"}[m.behavior]


def test_no_identifier_or_pii_dimensions(model):
    view = model.views["v_account_summary"]
    for d in model.dimensions.values():
        assert view.columns[d.column].pii in ("none",)


def test_relationships_all_disabled():
    rels = yaml.safe_load((ROOT / "semantic" / "relationships.yaml").read_text())["relationships"]
    assert rels and all(r["enabled"] is False and r["status"] == "pending_approval" for r in rels)


@pytest.fixture
def semantic_copy(tmp_path):
    d = tmp_path / "semantic"
    shutil.copytree(ROOT / "semantic", d)
    return d


def _mutate(path, fn):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


@pytest.mark.parametrize("mutation, message", [
    (lambda d: d["metrics"][0]["measure"].update(columns=["first_name"]), "not in v_account_summary"),
    (lambda d: d["metrics"][0]["measure"].update(columns=["vertical"]), "quality"),
    (lambda d: d["metrics"][0].update(view="v_dim_customers"), "not governed"),
    (lambda d: d["metrics"][0].update(additivity="additive"), "additivity"),
    (lambda d: d["metrics"][0]["measure"].update(columns=["state_name"]), "non-numeric"),
])
def test_bad_metric_config_rejected(semantic_copy, mutation, message):
    _mutate(semantic_copy / "metrics.yaml", mutation)
    with pytest.raises(SemanticConfigError, match=message):
        load_semantic_model(semantic_copy)


@pytest.mark.parametrize("mutation, message", [
    (lambda d: d["dimensions"].append({"id": "acct", "label": "x", "kind": "attribute", "type": "string",
                                       "view": "v_account_summary", "column": "account_number",
                                       "groupable": True, "filterable": False}), "identifier"),
    (lambda d: d["dimensions"].append({"id": "x", "label": "x", "kind": "attribute", "type": "string",
                                       "view": "v_account_summary", "column": "segment",
                                       "groupable": True, "filterable": False}), "quality"),
])
def test_bad_dimension_config_rejected(semantic_copy, mutation, message):
    _mutate(semantic_copy / "dimensions.yaml", mutation)
    with pytest.raises(SemanticConfigError, match=message):
        load_semantic_model(semantic_copy)


def test_enabled_relationship_rejected(semantic_copy):
    _mutate(semantic_copy / "relationships.yaml", lambda d: d["relationships"][0].update(enabled=True))
    with pytest.raises(SemanticConfigError, match="relationships"):
        load_semantic_model(semantic_copy)
