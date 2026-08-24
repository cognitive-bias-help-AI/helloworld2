from dataclasses import replace

import pytest
from langgraph.types import Command

from app.gateway.adapters.mock import MockAdapter
from app.gateway.admission import ProviderAdmissionController
from app.gateway.evidence_gateway import GatewayContractError
from app.orchestration import graph as graph_module
from app.orchestration.nodes.s0 import make_nodes
from app.schemas.frozen import EvidenceDraft, Query, ReasonCode
from tests.s0.runtime_fixtures import NOW, deps, initial_state


def uid(n: int) -> str:
    return f"01ARZ3NDEKTSV4RRFFQ69G{n:04d}"


def query(n: int, provider: str) -> Query:
    return Query(
        query_id=uid(8000 + n),
        scope="stock",
        claim_id=None,
        intent="context",
        provider=provider,
        endpoint={"dart": "disclosure", "kiwoom": "quote", "naver": "search"}[
            provider
        ],
        params={"stock_code": "005930"},
        created_at=NOW,
    )


async def run_n6(providers, *, adapters=None, external_calls=0):
    runtime_deps = deps()
    configured_adapters = adapters or {
        provider: MockAdapter(provider) for provider in set(providers)
    }
    runtime_deps = replace(
        runtime_deps,
        adapters=configured_adapters,
        provider_admission=ProviderAdmissionController(
            {
                provider: adapter.max_concurrency
                for provider, adapter in configured_adapters.items()
            }
        ),
    )
    queries = [query(index, provider) for index, provider in enumerate(providers, 1)]
    query_ids = await runtime_deps.evidence_store.put_queries("run-s0", queries)
    state = initial_state() | {"query_ids": query_ids}
    state["counters"] = {**state["counters"], "external_calls": external_calls}
    patch = await make_nodes(runtime_deps)["n6"](state)
    return patch


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "source"),
    [("dart", "dart"), ("kiwoom", "quote"), ("naver", "news")],
)
async def test_n6_single_provider_collection_uses_provider_key(provider, source):
    patch = await run_n6([provider])

    assert set(patch["collections"]) == {provider}
    assert patch["collections"][provider] == {
        "source": source,
        "status": "OK",
        "reason_code": None,
        "items_fetched": 1,
        "items_adopted": 1,
        "items_deduped": 0,
        "queries_run": 1,
    }
    assert patch["counters"] == {"external_calls": 1}


@pytest.mark.asyncio
async def test_n6_mixed_providers_aggregate_independently():
    patch = await run_n6(["dart", "dart", "kiwoom", "naver"])

    assert set(patch["collections"]) == {"dart", "kiwoom", "naver"}
    assert patch["collections"]["dart"]["source"] == "dart"
    assert patch["collections"]["dart"]["items_fetched"] == 2
    assert patch["collections"]["dart"]["items_adopted"] == 1
    assert patch["collections"]["dart"]["items_deduped"] == 1
    assert patch["collections"]["dart"]["queries_run"] == 2
    assert patch["collections"]["kiwoom"]["source"] == "quote"
    assert patch["collections"]["kiwoom"]["items_adopted"] == 1
    assert patch["collections"]["naver"]["source"] == "news"
    assert patch["collections"]["naver"]["items_adopted"] == 1
    assert patch["counters"] == {"external_calls": 4}


@pytest.mark.asyncio
async def test_n6_missing_adapter_fails_closed():
    patch = await run_n6(["kiwoom"], adapters={"dart": MockAdapter("dart")})
    assert patch["node_results"] == ["n6:missing"]
    assert patch["collections"]["kiwoom"]["status"] == "MISSING"
    assert patch["collections"]["kiwoom"]["queries_run"] == 0
    assert patch["counters"] == {"external_calls": 0}


@pytest.mark.asyncio
async def test_n6_adapter_name_mismatch_fails_closed():
    with pytest.raises(RuntimeError, match=ReasonCode.CONTRACT_VIOLATION.value):
        await run_n6(["kiwoom"], adapters={"kiwoom": MockAdapter("dart")})


class CountingAdapter(MockAdapter):
    def __init__(self, provider):
        super().__init__(provider)
        self.call_count = 0

    async def acall(self, request):
        self.call_count += 1
        return await super().acall(request)


def graph_nodes_with_actual_n6(runtime_deps, visited):
    actual_n6 = make_nodes(runtime_deps)["n6"]

    def make_fake_node(name):
        async def fake_node(state, runtime=None):
            visited.append(name)
            patch = {"node_results": [f"{name}:ok"]}
            if name == "n11":
                patch["report_id"] = uid(9900)
            return patch

        return fake_node

    async def intake_review(state, runtime=None):
        visited.append("intake_review")
        return Command(update={"node_results": ["intake_review:ok"]}, goto="n5")

    async def n6(state, runtime=None):
        visited.append("n6")
        return await actual_n6(state)

    nodes = {name: make_fake_node(name) for name in graph_module.VERTICES}
    nodes["intake_review"] = intake_review
    nodes["n6"] = n6
    return nodes


@pytest.mark.asyncio
async def test_n6_budget_preflight_blocks_without_call_or_counter_patch():
    adapter = CountingAdapter("dart")
    patch = await run_n6(["dart"], adapters={"dart": adapter}, external_calls=25)

    assert patch == {"node_results": ["n6:block:budget_exceeded"]}
    assert adapter.call_count == 0


@pytest.mark.asyncio
async def test_graph_n6_budget_block_routes_directly_to_n12(monkeypatch):
    runtime_deps = deps()
    adapter = CountingAdapter("dart")
    runtime_deps = replace(runtime_deps, adapters={"dart": adapter})
    item = query(1, "dart")
    query_ids = await runtime_deps.evidence_store.put_queries("run-s0", [item])
    visited = []
    monkeypatch.setattr(
        graph_module,
        "make_nodes",
        lambda ignored: graph_nodes_with_actual_n6(runtime_deps, visited),
    )
    state = initial_state() | {
        "query_ids": query_ids,
        "counters": {"external_calls": 25},
    }

    result = await graph_module.build_graph(runtime_deps).ainvoke(state)

    assert visited == ["n0", "n1", "n2", "intake_review", "n5", "n6", "n12"]
    assert adapter.call_count == 0
    assert await runtime_deps.evidence_store.evidence_ids_for_queries(query_ids) == []
    assert "n6:block:budget_exceeded" in result["node_results"]
    assert result["counters"]["external_calls"] == 25


@pytest.mark.asyncio
async def test_graph_n6_success_preserves_n7_n8_n9_path(monkeypatch):
    runtime_deps = deps()
    adapter = CountingAdapter("dart")
    runtime_deps = replace(runtime_deps, adapters={"dart": adapter})
    item = query(1, "dart")
    query_ids = await runtime_deps.evidence_store.put_queries("run-s0", [item])
    visited = []
    monkeypatch.setattr(
        graph_module,
        "make_nodes",
        lambda ignored: graph_nodes_with_actual_n6(runtime_deps, visited),
    )
    state = initial_state() | {"query_ids": query_ids}

    result = await graph_module.build_graph(runtime_deps).ainvoke(state)

    assert visited[:9] == [
        "n0",
        "n1",
        "n2",
        "intake_review",
        "n5",
        "n6",
        "n7",
        "n8",
        "n9",
    ]
    assert adapter.call_count == 1
    assert result["counters"]["external_calls"] == 1


class WrongSourceAdapter(MockAdapter):
    def parse_response(self, raw: dict, q: Query) -> list[EvidenceDraft]:
        draft = super().parse_response(raw, q)[0]
        return [draft.model_copy(update={"source_type": "news"})]


@pytest.mark.asyncio
async def test_n6_source_type_mismatch_fails_at_gateway_contract_boundary():
    with pytest.raises(GatewayContractError, match=ReasonCode.CONTRACT_VIOLATION.value):
        await run_n6(["dart"], adapters={"dart": WrongSourceAdapter("dart")})
