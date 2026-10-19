from bi_service.config import ServiceConfig, resolve_profile


def test_local_uses_configured_profile(monkeypatch):
    for k in ("BI_AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"):
        monkeypatch.delenv(k, raising=False)
    assert resolve_profile("analytics-query-runtime") == "analytics-query-runtime"
    assert ServiceConfig.load().profile == "analytics-query-runtime"


def test_env_credentials_mean_no_profile(monkeypatch):
    monkeypatch.delenv("BI_AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    assert resolve_profile("analytics-query-runtime") is None


def test_explicit_override(monkeypatch):
    monkeypatch.setenv("BI_AWS_PROFILE", "")
    assert resolve_profile("analytics-query-runtime") is None
    monkeypatch.setenv("BI_AWS_PROFILE", "other")
    assert resolve_profile("analytics-query-runtime") == "other"
