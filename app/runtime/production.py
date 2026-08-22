"""Environment-driven construction of the existing application runtime."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any

import asyncpg
import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.domain.protocols import StockResolver
from app.gateway.adapters.dart import DartAdapter
from app.gateway.adapters.kiwoom import KiwoomAdapter
from app.gateway.adapters.naver import NaverAdapter
from app.models.protocols import ModelGateway
from app.orchestration.graph import build_graph
from app.orchestration.reporting import RenderCandidateStore
from app.orchestration.runtime import Clock, IdFactory
from app.runtime.composition import (
    ApplicationRuntime,
    GraphBuilder,
    PoolFactory,
    compose_application_runtime,
)
from app.store.protocols import ReviewStore
from providers.dart.corp_code import DartCorpCodeResolver
from providers.kiwoom.core import (
    Environment as KiwoomEnvironment,
)
from providers.kiwoom.core import (
    KiwoomAdapter as KiwoomCoreAdapter,
)
from providers.kiwoom.core import (
    KiwoomCredentials,
)


class ApplicationEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class ApplicationTimezone(StrEnum):
    ASIA_SEOUL = "Asia/Seoul"
    UTC = "UTC"


class ProductionSettings(BaseModel):
    """Validated bootstrap inputs read directly from the process environment."""

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
    )

    app_env: ApplicationEnvironment = Field(alias="APP_ENV")
    timezone: ApplicationTimezone = Field(alias="TIMEZONE")
    database_url: SecretStr = Field(alias="DATABASE_URL", min_length=1)
    dart_api_key: SecretStr = Field(alias="DART_API_KEY", min_length=1)
    naver_client_id: SecretStr = Field(alias="NAVER_CLIENT_ID", min_length=1)
    naver_client_secret: SecretStr = Field(alias="NAVER_CLIENT_SECRET", min_length=1)
    kiwoom_app_key: SecretStr = Field(alias="KIWOOM_APP_KEY", min_length=1)
    kiwoom_app_secret: SecretStr = Field(alias="KIWOOM_APP_SECRET", min_length=1)

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> ProductionSettings:
        """Read only declared values; loading .env files is intentionally external."""

        source = os.environ if environ is None else environ
        keys = (
            "APP_ENV",
            "TIMEZONE",
            "DATABASE_URL",
            "DART_API_KEY",
            "NAVER_CLIENT_ID",
            "NAVER_CLIENT_SECRET",
            "KIWOOM_APP_KEY",
            "KIWOOM_APP_SECRET",
        )
        return cls.model_validate({key: source[key] for key in keys if key in source})


HttpClientFactory = Callable[[], httpx.AsyncClient]


def _require_authorities(
    *,
    review_store: ReviewStore | None,
    model_gateway: ModelGateway | None,
    stock_resolver: StockResolver | None,
    dart_corp_mapping: Mapping[str, str] | None,
) -> Mapping[str, str]:
    required = {
        "review_store": review_store,
        "model_gateway": model_gateway,
        "stock_resolver": stock_resolver,
        "dart_corp_mapping": dart_corp_mapping,
    }
    for name, value in required.items():
        if value is None:
            raise ValueError(f"{name} is required for production composition")
    if not dart_corp_mapping:
        raise ValueError("dart_corp_mapping must be non-empty")
    return dart_corp_mapping


@asynccontextmanager
async def compose_production_runtime(
    settings: ProductionSettings,
    *,
    review_store: ReviewStore | None,
    model_gateway: ModelGateway | None,
    stock_resolver: StockResolver | None,
    dart_corp_mapping: Mapping[str, str] | None,
    clock: Clock,
    id_factory: IdFactory,
    render_candidates: RenderCandidateStore | None = None,
    checkpointer: Any = None,
    _http_client_factory: HttpClientFactory = httpx.AsyncClient,
    _pool_factory: PoolFactory = asyncpg.create_pool,
    _graph_builder: GraphBuilder = build_graph,
) -> AsyncIterator[ApplicationRuntime]:
    """Own one shared HTTP client and delegate Pool/Graph ownership downstream."""

    mapping = _require_authorities(
        review_store=review_store,
        model_gateway=model_gateway,
        stock_resolver=stock_resolver,
        dart_corp_mapping=dart_corp_mapping,
    )
    client = _http_client_factory()
    try:
        kiwoom_environment = (
            KiwoomEnvironment.PRODUCTION
            if settings.app_env is ApplicationEnvironment.PRODUCTION
            else KiwoomEnvironment.MOCK
        )
        adapters = {
            "dart": DartAdapter(
                settings.dart_api_key.get_secret_value(),
                DartCorpCodeResolver(mapping),
                client,
            ),
            "naver": NaverAdapter(
                settings.naver_client_id.get_secret_value(),
                settings.naver_client_secret.get_secret_value(),
                client,
            ),
            "kiwoom": KiwoomAdapter(
                KiwoomCoreAdapter(
                    client,
                    KiwoomCredentials(
                        app_key=settings.kiwoom_app_key.get_secret_value(),
                        secret_key=settings.kiwoom_app_secret.get_secret_value(),
                    ),
                ),
                environment=kiwoom_environment,
            ),
        }
        async with compose_application_runtime(
            postgres_dsn=settings.database_url.get_secret_value(),
            review_store=review_store,
            model_gateway=model_gateway,
            stock_resolver=stock_resolver,
            adapters=adapters,
            clock=clock,
            id_factory=id_factory,
            render_candidates=render_candidates,
            checkpointer=checkpointer,
            _pool_factory=_pool_factory,
            _graph_builder=_graph_builder,
        ) as runtime:
            yield runtime
    finally:
        await client.aclose()
