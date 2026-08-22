from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest

from app.gateway.evidence_gateway import collect_evidence
from app.orchestration.reporting import RenderCandidateStore
from app.runtime.composition import compose_application_runtime
from app.schemas.frozen import EvidenceDraft, Query, Request
from app.store.errors import StorePersistenceError
from app.store.memory_review_store import MemoryReviewStore
from tests.store.evidence_store_contract import uid

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]
NOW = datetime(2026, 8, 22, tzinfo=UTC)


class Ids:
    def __init__(self) -> None:
        self.value = 7000

    def __call__(self) -> str:
        self.value += 1
        return uid(self.value)


class GraphBuilder:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, deps, *, checkpointer=None):
        self.calls.append((deps, checkpointer))
        return object()


class BlockingAdapter:
    name = "dart"
    max_concurrency = 1

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def build_request(self, q: Query, as_of: datetime) -> Request:
        return Request(
            provider="dart",
            endpoint=q.endpoint,
            params={"query_id": q.query_id, "as_of": as_of.isoformat()},
        )

    async def acall(self, req: Request) -> dict:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        await self.release.wait()
        self.active -= 1
        return {"query_id": req.params["query_id"]}

    def parse_response(self, raw: dict, q: Query) -> list[EvidenceDraft]:
        return [
            EvidenceDraft(
                source_type="dart",
                source_ref=f"runtime:{raw['query_id']}",
                source_url=None,
                publisher="runtime-test",
                published_at=None,
                raw_span=f"runtime evidence for {q.query_id}",
                span_scope="structured_field",
                normalized_value={"query_id": q.query_id},
            )
        ]

    def classify_error(self, raw: dict):
        raise ValueError("successful test response")

    def rate_limit_hint(self, raw: dict):
        return None


def query(number: int) -> Query:
    return Query(
        query_id=uid(6000 + number),
        scope="stock",
        claim_id=None,
        intent="context",
        provider="dart",
        endpoint="disclosure",
        params={"stock_code": "005930"},
        created_at=NOW,
    )


def runtime_inputs(adapter, graph_builder, ids):
    return {
        "postgres_dsn": os.environ["TEST_POSTGRES_DSN"],
        "review_store": MemoryReviewStore(),
        "model_gateway": object(),
        "stock_resolver": object(),
        "adapters": {"dart": adapter},
        "clock": lambda: NOW,
        "id_factory": ids,
        "render_candidates": RenderCandidateStore(),
        "_graph_builder": graph_builder,
    }


async def test_real_pool_store_shutdown_and_runtime_a_b_durability(postgres_pool):
    del postgres_pool
    adapter = BlockingAdapter()
    item = query(1)
    first_builder = GraphBuilder()

    async with compose_application_runtime(
        **runtime_inputs(adapter, first_builder, Ids())
    ) as first:
        store = first.deps.evidence_store
        pool = first.pool
        await store.put_queries("runtime-a", [item])
        assert await store.get_queries([item.query_id]) == [item]

    with pytest.raises(StorePersistenceError):
        await store.get_queries([item.query_id])

    second_builder = GraphBuilder()
    async with compose_application_runtime(
        **runtime_inputs(adapter, second_builder, Ids())
    ) as second:
        assert second.pool is not pool
        assert await second.deps.evidence_store.get_queries([item.query_id]) == [item]


async def test_two_review_collections_share_one_runtime_admission_limit(postgres_pool):
    del postgres_pool
    adapter = BlockingAdapter()
    ids = Ids()
    q1, q2 = query(1), query(2)

    async with compose_application_runtime(
        **runtime_inputs(adapter, GraphBuilder(), ids)
    ) as runtime:
        await runtime.deps.evidence_store.put_queries("review-a", [q1])
        await runtime.deps.evidence_store.put_queries("review-b", [q2])

        async def review(run_id: str, item: Query):
            return await collect_evidence(
                run_id=run_id,
                as_of=NOW,
                queries=[item],
                adapters=runtime.deps.adapters,
                evidence_store=runtime.deps.evidence_store,
                provider_admission=runtime.deps.provider_admission,
                clock=runtime.deps.clock,
                id_factory=runtime.deps.id_factory,
                current_external_calls=0,
                external_call_limit=2,
            )

        tasks = [
            asyncio.create_task(review("review-a", q1)),
            asyncio.create_task(review("review-b", q2)),
        ]
        await asyncio.wait_for(adapter.started.wait(), timeout=1)
        await asyncio.sleep(0)
        assert adapter.max_active == 1
        adapter.release.set()
        results = await asyncio.gather(*tasks)

    assert adapter.max_active == 1
    assert [result.external_calls for result in results] == [1, 1]
