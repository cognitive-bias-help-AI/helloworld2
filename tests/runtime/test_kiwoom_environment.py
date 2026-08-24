import httpx
import pytest

from app.runtime.local import _adapters
from providers.kiwoom.core import Environment


def set_kiwoom(monkeypatch, environment, *, mock=True, production=True):
    monkeypatch.setenv("KIWOOM_ENV", environment)
    for name in (
        "KIWOOM_MOCK_APP_KEY", "KIWOOM_MOCK_APP_SECRET",
        "KIWOOM_PROD_APP_KEY", "KIWOOM_PROD_APP_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    if mock:
        monkeypatch.setenv("KIWOOM_MOCK_APP_KEY", "mock-key")
        monkeypatch.setenv("KIWOOM_MOCK_APP_SECRET", "mock-secret")
    if production:
        monkeypatch.setenv("KIWOOM_PROD_APP_KEY", "prod-key")
        monkeypatch.setenv("KIWOOM_PROD_APP_SECRET", "prod-secret")


@pytest.mark.parametrize(
    ("app_env", "kiwoom_env", "expected"),
    [("development", "mock", Environment.MOCK), ("production", "mock", Environment.MOCK),
     ("development", "production", Environment.PRODUCTION)],
)
def test_kiwoom_environment_is_explicit_and_independent(monkeypatch, app_env, kiwoom_env, expected):
    monkeypatch.setenv("APP_ENV", app_env)
    set_kiwoom(monkeypatch, kiwoom_env)
    adapters, _, _ = _adapters(httpx.AsyncClient(), corp_cache="missing.json")

    assert adapters["kiwoom"]._environment is expected


def test_kiwoom_debug_diagnostic_names_environment_without_credentials(monkeypatch, capsys):
    monkeypatch.setenv("REVIEW_DEBUG_LOGS", "1")
    set_kiwoom(monkeypatch, "mock")
    _adapters(httpx.AsyncClient(), corp_cache="missing.json")

    diagnostic = capsys.readouterr().err
    assert 'environment="mock"' in diagnostic
    assert "mock-secret" not in diagnostic


def test_invalid_kiwoom_environment_fails_closed(monkeypatch):
    set_kiwoom(monkeypatch, "prodution")
    with pytest.raises(RuntimeError, match="KIWOOM_ENV"):
        _adapters(httpx.AsyncClient(), corp_cache="missing.json")


@pytest.mark.parametrize("environment,missing", [("mock", "KIWOOM_MOCK_APP_KEY"), ("production", "KIWOOM_PROD_APP_KEY")])
def test_selected_kiwoom_environment_requires_its_own_credentials(monkeypatch, environment, missing):
    set_kiwoom(monkeypatch, environment, mock=False, production=False)
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(RuntimeError, match=missing):
        _adapters(httpx.AsyncClient(), corp_cache="missing.json")
