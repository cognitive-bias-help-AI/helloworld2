from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.gateway.adapters.dart import DartAdapter
from app.gateway.adapters.kiwoom import KiwoomAdapter
from app.gateway.adapters.naver import NaverAdapter
from app.store.sql_evidence_store import SqlEvidenceStore

NOW = datetime(2026, 8, 23, tzinfo=UTC)


def valid_environment() -> dict[str, str]:
    return {
        "APP_ENV": "production",
        "TIMEZONE": "Asia/Seoul",
        "DATABASE_URL": "postgresql://runtime-only",
        "DART_API_KEY": "super-secret-dart-key",
        "NAVER_CLIENT_ID": "super-secret-naver-id",
        "NAVER_CLIENT_SECRET": "super-secret-naver-secret",
        "KIWOOM_ENV": "production",
        "KIWOOM_PROD_APP_KEY": "super-secret-kiwoom-key",
        "KIWOOM_PROD_APP_SECRET": "super-secret-kiwoom-secret",
    }


class FakePool:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class PoolFactory:
    def __init__(self) -> None:
        self.dsns: list[str] = []
        self.pools: list[FakePool] = []

    async def __call__(self, dsn: str):
        pool = FakePool()
        self.dsns.append(dsn)
        self.pools.append(pool)
        return pool


class GraphBuilder:
    def __init__(self) -> None:
        self.calls = []
        self.graph = object()

    def __call__(self, deps, *, checkpointer=None):
        self.calls.append((deps, checkpointer))
        return self.graph


class FakeHttpClient:
    def __init__(self) -> None:
        self.close_calls = 0
        self.request_calls = 0

    async def get(self, *args, **kwargs):
        self.request_calls += 1
        raise AssertionError("bootstrap must not perform network I/O")

    async def post(self, *args, **kwargs):
        self.request_calls += 1
        raise AssertionError("bootstrap must not perform network I/O")

    async def aclose(self) -> None:
        self.close_calls += 1


class HttpClientFactory:
    def __init__(self) -> None:
        self.clients: list[FakeHttpClient] = []

    def __call__(self):
        client = FakeHttpClient()
        self.clients.append(client)
        return client


def authorities() -> dict:
    return {
        "review_store": object(),
        "model_gateway": object(),
        "stock_resolver": object(),
        "dart_corp_mapping": {"005930": "00126380"},
        "clock": lambda: NOW,
        "id_factory": lambda: "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    }


def bootstrap_api():
    from app.runtime.production import ProductionSettings, compose_production_runtime

    return ProductionSettings, compose_production_runtime


def test_settings_read_typed_process_environment_and_mask_secrets():
    ProductionSettings, _ = bootstrap_api()
    settings = ProductionSettings.from_environment(valid_environment())

    assert settings.app_env.value == "production"
    assert str(settings.timezone) == "Asia/Seoul"
    assert settings.database_url.get_secret_value() == "postgresql://runtime-only"
    rendered = repr(settings)
    for secret in valid_environment().values():
        if secret.startswith("super-secret") or secret.startswith("postgresql://"):
            assert secret not in rendered


def test_kiwoom_environment_is_not_derived_from_app_environment():
    ProductionSettings, _ = bootstrap_api()
    values = valid_environment() | {
        "APP_ENV": "development",
        "KIWOOM_ENV": "mock",
        "KIWOOM_MOCK_APP_KEY": "super-secret-mock-key",
        "KIWOOM_MOCK_APP_SECRET": "super-secret-mock-secret",
    }
    settings = ProductionSettings.from_environment(values)

    assert settings.app_env.value == "development"
    assert settings.kiwoom_env.value == "mock"


def test_settings_read_from_process_environment(monkeypatch):
    ProductionSettings, _ = bootstrap_api()
    for key, value in valid_environment().items():
        monkeypatch.setenv(key, value)

    settings = ProductionSettings.from_environment()

    assert settings.app_env.value == "production"
    assert settings.database_url.get_secret_value() == "postgresql://runtime-only"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("APP_ENV", "invalid"),
        ("TIMEZONE", "Not/AZone"),
        ("DATABASE_URL", None),
        ("DART_API_KEY", None),
        ("NAVER_CLIENT_ID", None),
        ("NAVER_CLIENT_SECRET", None),
        ("KIWOOM_PROD_APP_KEY", None),
        ("KIWOOM_PROD_APP_SECRET", None),
    ],
)
def test_settings_fail_fast_for_invalid_or_missing_required_values(key, value):
    ProductionSettings, _ = bootstrap_api()
    environment = valid_environment()
    if value is None:
        environment.pop(key)
    else:
        environment[key] = value

    with pytest.raises(ValidationError) as raised:
        ProductionSettings.from_environment(environment)

    error = str(raised.value)
    assert "super-secret" not in error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing",
    ["review_store", "model_gateway", "stock_resolver", "dart_corp_mapping"],
)
async def test_missing_production_authority_fails_before_resource_creation(missing):
    ProductionSettings, compose = bootstrap_api()
    values = authorities()
    values[missing] = None
    http_factory = HttpClientFactory()
    pool_factory = PoolFactory()

    with pytest.raises(ValueError, match=missing):
        async with compose(
            ProductionSettings.from_environment(valid_environment()),
            **values,
            _http_client_factory=http_factory,
            _pool_factory=pool_factory,
            _graph_builder=GraphBuilder(),
        ):
            pytest.fail("invalid composition must not yield")

    assert http_factory.clients == []
    assert pool_factory.pools == []


@pytest.mark.asyncio
async def test_production_runtime_builds_real_adapters_with_one_owned_http_client():
    ProductionSettings, compose = bootstrap_api()
    settings = ProductionSettings.from_environment(valid_environment())
    http_factory = HttpClientFactory()
    pool_factory = PoolFactory()
    graph_builder = GraphBuilder()

    async with compose(
        settings,
        **authorities(),
        _http_client_factory=http_factory,
        _pool_factory=pool_factory,
        _graph_builder=graph_builder,
    ) as runtime:
        assert pool_factory.dsns == ["postgresql://runtime-only"]
        assert isinstance(runtime.deps.evidence_store, SqlEvidenceStore)
        assert runtime.deps.evidence_store.pool is runtime.pool
        assert runtime.graph is graph_builder.graph
        assert set(runtime.deps.adapters) == {"dart", "naver", "kiwoom"}
        assert isinstance(runtime.deps.adapters["dart"], DartAdapter)
        assert isinstance(runtime.deps.adapters["naver"], NaverAdapter)
        assert isinstance(runtime.deps.adapters["kiwoom"], KiwoomAdapter)

        shared_client = http_factory.clients[0]
        assert runtime.deps.adapters["dart"]._client._client is shared_client
        assert runtime.deps.adapters["naver"]._client._client is shared_client
        assert runtime.deps.adapters["kiwoom"]._core._http_client is shared_client
        assert shared_client.request_calls == 0
        assert shared_client.close_calls == 0

    assert len(http_factory.clients) == 1
    assert http_factory.clients[0].close_calls == 1
    assert pool_factory.pools[0].close_calls == 1


@pytest.mark.asyncio
async def test_http_client_closes_once_when_existing_composition_startup_fails():
    ProductionSettings, compose = bootstrap_api()
    http_factory = HttpClientFactory()

    async def failing_pool_factory(dsn):
        del dsn
        raise LookupError("pool startup failed")

    with pytest.raises(LookupError, match="pool startup failed"):
        async with compose(
            ProductionSettings.from_environment(valid_environment()),
            **authorities(),
            _http_client_factory=http_factory,
            _pool_factory=failing_pool_factory,
            _graph_builder=GraphBuilder(),
        ):
            pytest.fail("failed composition must not yield")

    assert http_factory.clients[0].close_calls == 1
    assert http_factory.clients[0].request_calls == 0


@pytest.mark.asyncio
async def test_production_runtime_compiles_existing_graph_without_network_calls():
    ProductionSettings, compose = bootstrap_api()
    http_factory = HttpClientFactory()
    pool_factory = PoolFactory()

    async with compose(
        ProductionSettings.from_environment(valid_environment()),
        **authorities(),
        _http_client_factory=http_factory,
        _pool_factory=pool_factory,
    ) as runtime:
        assert callable(runtime.graph.ainvoke)
        assert http_factory.clients[0].request_calls == 0

    assert http_factory.clients[0].close_calls == 1
