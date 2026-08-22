"""Explicit application-scoped construction and resource ownership."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.domain.protocols import StockResolver
from app.gateway.admission import ProviderAdmissionController
from app.gateway.protocols import ProviderAdapter
from app.models.protocols import ModelGateway
from app.orchestration.graph import build_graph
from app.orchestration.reporting import RenderCandidateStore
from app.orchestration.runtime import Clock, IdFactory, RuntimeDeps
from app.store.protocols import ReviewStore
from app.store.sql_evidence_store import SqlEvidenceStore

PoolFactory = Callable[[str], Awaitable[asyncpg.Pool]]
GraphBuilder = Callable[..., Any]


@dataclass(frozen=True)
class ApplicationRuntime:
    """Minimal application-scope surface; Review state remains invocation-scoped."""

    deps: RuntimeDeps
    graph: Any
    pool: asyncpg.Pool


def _provider_capacities(
    adapters: Mapping[str, ProviderAdapter],
) -> dict[str, int]:
    capacities: dict[str, int] = {}
    for provider, adapter in adapters.items():
        if adapter.name != provider:
            raise ValueError(f"adapter ownership mismatch: {provider}")
        capacity = adapter.max_concurrency
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError(f"provider admission capacity must be positive: {provider}")
        capacities[provider] = capacity
    return capacities


@asynccontextmanager
async def compose_application_runtime(
    *,
    postgres_dsn: str,
    review_store: ReviewStore,
    model_gateway: ModelGateway,
    stock_resolver: StockResolver,
    adapters: Mapping[str, ProviderAdapter],
    clock: Clock,
    id_factory: IdFactory,
    render_candidates: RenderCandidateStore | None = None,
    checkpointer: Any = None,
    _pool_factory: PoolFactory = asyncpg.create_pool,
    _graph_builder: GraphBuilder = build_graph,
) -> AsyncIterator[ApplicationRuntime]:
    """Create one reusable runtime and close only its owned PostgreSQL Pool."""

    if not isinstance(postgres_dsn, str) or not postgres_dsn.strip():
        raise ValueError("postgres_dsn must be non-blank")
    adapter_map = dict(adapters)
    admission = ProviderAdmissionController(_provider_capacities(adapter_map))
    pool = await _pool_factory(postgres_dsn)
    try:
        evidence_store = SqlEvidenceStore(pool)
        deps = RuntimeDeps(
            review_store=review_store,
            evidence_store=evidence_store,
            provider_admission=admission,
            model_gateway=model_gateway,
            stock_resolver=stock_resolver,
            adapters=adapter_map,
            clock=clock,
            id_factory=id_factory,
            render_candidates=render_candidates or RenderCandidateStore(),
        )
        graph = _graph_builder(deps, checkpointer=checkpointer)
    except BaseException:
        try:
            await pool.close()
        finally:
            raise

    try:
        yield ApplicationRuntime(deps=deps, graph=graph, pool=pool)
    finally:
        await pool.close()
