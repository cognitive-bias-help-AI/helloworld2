from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime

import pytest

from app.gateway.adapters.mock import MockAdapter
from app.orchestration.reporting import RenderCandidateStore
from app.store.memory_review_store import MemoryReviewStore
from app.store.sql_evidence_store import SqlEvidenceStore

NOW = datetime(2026, 8, 22, tzinfo=UTC)


def composition_api():
    from app.runtime.composition import ApplicationRuntime, compose_application_runtime

    return ApplicationRuntime, compose_application_runtime


class FakePool:
    def __init__(self) -> None:
        self.closed = False
        self.close_calls = 0

    async def close(self) -> None:
        self.closed = True
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
    def __init__(self, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.calls = []
        self.graphs = []

    def __call__(self, deps, *, checkpointer=None):
        self.calls.append((deps, checkpointer))
        if self.failure is not None:
            raise self.failure
        graph = object()
        self.graphs.append(graph)
        return graph


class Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"01ARZ3NDEKTSV4RRFFQ69G{self.value:04d}"


def inputs(pool_factory, graph_builder, *, adapters=None, checkpointer=None):
    return {
        "postgres_dsn": "postgresql://test-only",
        "review_store": MemoryReviewStore(),
        "model_gateway": object(),
        "stock_resolver": object(),
        "adapters": adapters or {"dart": MockAdapter("dart")},
        "clock": lambda: NOW,
        "id_factory": Ids(),
        "render_candidates": RenderCandidateStore(),
        "checkpointer": checkpointer,
        "_pool_factory": pool_factory,
        "_graph_builder": graph_builder,
    }


@pytest.mark.asyncio
async def test_c1_c2_c3_c7_one_runtime_constructs_and_reuses_one_dependency_graph():
    ApplicationRuntime, compose = composition_api()
    pool_factory = PoolFactory()
    graph_builder = GraphBuilder()

    async with compose(**inputs(pool_factory, graph_builder)) as runtime:
        assert isinstance(runtime, ApplicationRuntime)
        assert [field.name for field in fields(ApplicationRuntime)] == ["deps", "graph", "pool"]
        assert pool_factory.dsns == ["postgresql://test-only"]
        assert runtime.pool is pool_factory.pools[0]
        assert isinstance(runtime.deps.evidence_store, SqlEvidenceStore)
        assert runtime.deps.evidence_store.pool is runtime.pool
        assert runtime.deps.provider_admission is runtime.deps.provider_admission
        assert runtime.graph is graph_builder.graphs[0]
        first_review = (runtime.deps, runtime.graph)
        second_review = (runtime.deps, runtime.graph)
        assert first_review[0] is second_review[0]
        assert first_review[1] is second_review[1]
        assert len(graph_builder.calls) == 1
        assert len(pool_factory.pools) == 1


@pytest.mark.asyncio
async def test_c4_c9_two_explicit_runtimes_have_isolated_resources():
    _, compose = composition_api()
    pool_factory = PoolFactory()
    first_builder, second_builder = GraphBuilder(), GraphBuilder()

    async with compose(**inputs(pool_factory, first_builder)) as first:
        async with compose(**inputs(pool_factory, second_builder)) as second:
            assert first.pool is not second.pool
            assert first.deps is not second.deps
            assert first.deps.evidence_store is not second.deps.evidence_store
            assert first.deps.provider_admission is not second.deps.provider_admission
            assert first.graph is not second.graph
        assert second.pool.closed
        assert not first.pool.closed


@pytest.mark.asyncio
async def test_c5_normal_shutdown_closes_owned_pool_exactly_once():
    _, compose = composition_api()
    pool_factory = PoolFactory()

    async with compose(**inputs(pool_factory, GraphBuilder())) as runtime:
        pool = runtime.pool
        assert not pool.closed

    assert pool.closed
    assert pool.close_calls == 1


@pytest.mark.asyncio
async def test_c6_startup_failure_closes_pool_and_preserves_original_exception():
    _, compose = composition_api()
    pool_factory = PoolFactory()
    original = LookupError("graph construction failed")

    with pytest.raises(LookupError) as raised:
        async with compose(**inputs(pool_factory, GraphBuilder(original))):
            pytest.fail("a failed composition must not yield a runtime")

    assert raised.value is original
    assert pool_factory.pools[0].closed
    assert pool_factory.pools[0].close_calls == 1


@pytest.mark.asyncio
async def test_caller_owned_checkpointer_is_passed_through_and_not_closed():
    _, compose = composition_api()
    pool_factory = PoolFactory()
    graph_builder = GraphBuilder()

    class Checkpointer:
        close_calls = 0

        async def close(self):
            self.close_calls += 1

    checkpointer = Checkpointer()
    async with compose(
        **inputs(pool_factory, graph_builder, checkpointer=checkpointer)
    ) as runtime:
        assert graph_builder.calls == [(runtime.deps, checkpointer)]

    assert checkpointer.close_calls == 0


@pytest.mark.asyncio
async def test_invalid_adapter_capacity_fails_before_pool_creation():
    _, compose = composition_api()
    pool_factory = PoolFactory()
    adapter = MockAdapter("dart")
    adapter.max_concurrency = 0

    with pytest.raises(ValueError, match="capacity"):
        async with compose(
            **inputs(pool_factory, GraphBuilder(), adapters={"dart": adapter})
        ):
            pytest.fail("invalid composition must not yield")

    assert pool_factory.pools == []
