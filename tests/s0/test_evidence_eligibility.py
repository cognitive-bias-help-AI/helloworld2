from __future__ import annotations

import copy
from dataclasses import replace

import httpx
import pytest

import app.orchestration.graph as graph_module
import app.orchestration.nodes.s0 as nodes_module
from app.contexts.views import IntegrationView
from app.domain.intake import ResponseState
from app.domain.slot_resolution import CurrentSlotProjection, CurrentSlotStatus
from app.gateway.adapters.naver import NaverAdapter
from app.gateway.admission import ProviderAdmissionController
from app.gateway.evidence_gateway import GatewayResult
from app.orchestration.drafts import (
    FindingDraft,
    GuardVerdictDraft,
    RenderDraft,
    RenderedSlotDraft,
    ViolationDraft,
)
from app.orchestration.nodes.s0 import make_nodes
from app.schemas.frozen import (
    CitationRef,
    Claim,
    ClaimEvaluationDraft,
    ClaimEvidenceDraft,
    ClaimStanceDraft,
    Evidence,
    EvidenceQueryLink,
    NodeStatus,
    ProviderCall,
    Query,
    ReasonCode,
    SourceTrace,
    Usage,
    Violation,
)
from tests.s0.runtime_fixtures import NOW, FlowGateway, deps, initial_state


def uid(n: int) -> str:
    return f"01ARZ3NDEKTSV4RRFFQ69G{n:04d}"


def claim(
    n: int,
    *,
    verifiable: bool,
    slot_id: int = 1,
    proposition: str | None = None,
) -> Claim:
    text = proposition or f"claim-{n}"
    return Claim(
        claim_id=uid(9000 + n),
        slot_id=slot_id,
        user_text_span=text,
        span_offset=(0, len(text)),
        normalized_proposition=text,
        verifiable=verifiable,
        origin=SourceTrace.SURVEY,
        created_at=NOW,
    )


def query(n: int, claim_id: str, *, provider: str = "dart") -> Query:
    return Query(
        query_id=uid(8000 + n),
        scope="claim",
        claim_id=claim_id,
        intent="verify",
        provider=provider,
        endpoint={"dart": "disclosure", "kiwoom": "quote", "naver": "search"}[
            provider
        ],
        params={"stock_code": "005930"},
        created_at=NOW,
    )


def evidence(n: int, *, normalized_value=None, source_type="dart", raw_span=None) -> Evidence:
    return Evidence(
        evidence_id=uid(7000 + n),
        source_type=source_type,
        source_ref=f"ref-{n}",
        fetched_at=NOW,
        raw_span=raw_span or f"evidence-{n}",
        span_scope="structured_field",
        content_sha256=f"{n:064x}",
        provider_request_id=uid(6000 + n),
        as_of=NOW,
        normalized_value=normalized_value,
    )


async def seed_claims(runtime_deps, claims: list[Claim]) -> dict:
    await runtime_deps.review_store.put_claims("run-s0", claims)
    return initial_state() | {
        "stock": {"code": "005930", "name": "삼성전자"},
        "claim_ids": [item.claim_id for item in claims],
    }


async def seed_queries_and_evidence(runtime_deps, pairs):
    queries = [item[1] for item in pairs]
    await runtime_deps.evidence_store.put_queries("run-s0", queries)
    evidence_pairs = [(item[1], item[2]) for item in pairs if item[2] is not None]
    calls = [
        ProviderCall(
            provider_request_id=item.provider_request_id,
            run_id="run-s0",
            provider=query_item.provider,
            endpoint=query_item.endpoint,
            query_id=query_item.query_id,
            latency_ms=0,
            idempotency_key=f"{index:064x}",
            created_at=NOW,
        )
        for index, (query_item, item) in enumerate(evidence_pairs, 1)
    ]
    evidences = [
        item.model_copy(
            update={
                "source_type": {"dart": "dart", "kiwoom": "quote", "naver": "news"}[
                    query_item.provider
                ]
            }
        )
        for query_item, item in evidence_pairs
    ]
    if evidences:
        await runtime_deps.evidence_store.put_provider_calls("run-s0", calls)
        await runtime_deps.evidence_store.put_evidence_batch(
            "run-s0",
            evidences,
            [
                EvidenceQueryLink(evidence_id=item[2].evidence_id, query_id=item[1].query_id)
                for item in pairs
                if item[2] is not None
            ],
        )
    return [item.query_id for item in queries]


@pytest.mark.asyncio
async def test_n5_filters_canonical_non_verifiable_claims_before_query_construction():
    runtime_deps = deps()
    a = claim(1, verifiable=True, proposition="유상증자 공시가 발표됐다")
    b = claim(2, verifiable=False, proposition="NON_VERIFIABLE_SECRET")
    c = claim(3, verifiable=True, proposition="신규사업 공시가 발표됐다")
    state = await seed_claims(runtime_deps, [a, b, c])
    before = copy.deepcopy(await runtime_deps.review_store.get_claims(state["claim_ids"]))

    patch = await make_nodes(runtime_deps)["n5"](state)
    queries = await runtime_deps.evidence_store.get_queries(patch["query_ids"])

    claim_queries = [item for item in queries if item.scope == "claim"]
    assert [item.claim_id for item in claim_queries] == [a.claim_id, a.claim_id, c.claim_id, c.claim_id]
    assert all(
        item.provider == "dart"
        and item.endpoint == "disclosure_list"
        and item.params == {
            "stock_code": "005930",
            "bgn_de": "20260215",
            "end_de": "20260814",
            "sort": "date",
            "sort_mth": "desc",
            "page_no": 1,
            "page_count": 20,
        }
        for item in claim_queries
        if item.provider == "dart"
    )
    assert "NON_VERIFIABLE_SECRET" not in str([item.model_dump() for item in queries])
    assert await runtime_deps.review_store.get_claims(state["claim_ids"]) == before
    assert patch["node_results"] == ["n5:ok"]
    assert [node for node, _ in runtime_deps.model_gateway.calls] == ["n5"] * 2


@pytest.mark.asyncio
async def test_n5_same_slot_verifiable_claims는_각각_독립_Query를_만든다():
    runtime_deps = deps()
    demand = claim(1, verifiable=True, slot_id=4, proposition="HBM 수요 증가 뉴스")
    supply = claim(2, verifiable=True, slot_id=4, proposition="HBM 공급 부족 뉴스")
    state = await seed_claims(runtime_deps, [demand, supply])

    patch = await make_nodes(runtime_deps)["n5"](state)
    queries = await runtime_deps.evidence_store.get_queries(patch["query_ids"])

    claim_queries = [item for item in queries if item.scope == "claim"]
    assert {item.claim_id for item in claim_queries} == {demand.claim_id, supply.claim_id}
    assert len({item.query_id for item in claim_queries}) == 4


@pytest.mark.asyncio
async def test_n5_claim_dependent_text_slot은_NAVER_Query로_계획한다():
    runtime_deps = deps()
    item = claim(1, verifiable=True, slot_id=4, proposition="HBM 공급 확대 뉴스")
    state = await seed_claims(runtime_deps, [item])

    patch = await make_nodes(runtime_deps)["n5"](state)
    queries = await runtime_deps.evidence_store.get_queries(patch["query_ids"])

    claim_queries = [item for item in queries if item.scope == "claim"]
    assert len(claim_queries) == 2
    assert {item.provider for item in claim_queries} == {"dart", "naver"}
    primary_news = next(
        query for query in claim_queries
        if query.provider == "naver" and query.intent == "verify"
    )
    assert primary_news.endpoint == "news_search"
    assert primary_news.params == {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "query": "삼성전자",
        "display": 30,
        "sort": "date",
    }


@pytest.mark.asyncio
async def test_n5_NAVER_Query는_Gateway에서_canonical_Evidence로_연결된다():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "items": [{
                    "title": "삼성전자, HBM 공급 확대",
                    "description": "삼성전자(005930)가 신규 공급 계획을 발표했다.",
                    "link": "https://n.news.naver.com/mnews/article/001/999",
                    "originallink": "https://example.com/news/999",
                    "pubDate": "Fri, 21 Aug 2026 09:00:00 +0900",
                }]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        adapter = NaverAdapter("test-placeholder", "test-placeholder", client=client)
        runtime_deps = replace(
            deps(),
            adapters={"naver": adapter},
            provider_admission=ProviderAdmissionController({"naver": 3}),
        )
        item = claim(1, verifiable=True, slot_id=4, proposition="HBM 공급 확대 뉴스")
        state = await seed_claims(runtime_deps, [item])
        n5_patch = await make_nodes(runtime_deps)["n5"](state)
        state["query_ids"] = n5_patch["query_ids"]

        n6_patch = await make_nodes(runtime_deps)["n6"](state)
        evidence_ids = await runtime_deps.evidence_store.evidence_ids_for_queries(
            state["query_ids"]
        )

        assert n6_patch["node_results"] == ["n6:partial"]
        assert n6_patch["counters"] == {"external_calls": 2}
        assert len(evidence_ids) == 1
        stored = await runtime_deps.evidence_store.get_many(evidence_ids)
        assert stored[0].source_type == "news"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_n5_all_false_returns_empty_query_ids_without_provider_candidate():
    runtime_deps = deps()
    claims = [claim(1, verifiable=False), claim(2, verifiable=False)]
    state = await seed_claims(runtime_deps, claims)

    patch = await make_nodes(runtime_deps)["n5"](state)

    queries = await runtime_deps.evidence_store.get_queries(patch["query_ids"])
    assert len(queries) == 3
    assert all(item.scope == "stock" for item in queries)
    assert patch["node_results"] == ["n5:ok"]
    assert len(runtime_deps.evidence_store._queries) == 3


@pytest.mark.asyncio
async def test_n5_unknown_evidence_need는_provider를_추측하지_않는다():
    runtime_deps = deps()
    item = claim(1, verifiable=True, proposition="HBM 전망이 좋다")
    state = await seed_claims(runtime_deps, [item])

    patch = await make_nodes(runtime_deps)["n5"](state)

    queries = await runtime_deps.evidence_store.get_queries(patch["query_ids"])
    assert len(queries) == 3
    assert all(item.scope == "stock" for item in queries)
    assert patch["node_results"] == ["n5:missing"]
    assert len(runtime_deps.evidence_store._queries) == 3


@pytest.mark.asyncio
async def test_n5_price_financial_mixed_claim의_planning_status와_safe_diagnostics(
    monkeypatch, capsys
):
    monkeypatch.setenv("REVIEW_DEBUG_LOGS", "1")
    cases = [
        ([claim(31, verifiable=True, proposition="주가가 상승했다")], "n5:ok", 5),
        ([claim(32, verifiable=True, proposition="영업이익이 증가했다")], "n5:missing", 4),
        (
            [
                claim(33, verifiable=True, proposition="주가가 상승했다"),
                claim(34, verifiable=True, proposition="영업이익이 증가했다"),
            ],
            "n5:partial",
                6,
        ),
    ]
    for claims, node_result, query_count in cases:
        runtime_deps = deps()
        state = await seed_claims(runtime_deps, claims)
        patch = await make_nodes(runtime_deps)["n5"](state)
        assert patch["node_results"] == [node_result]
        assert len(patch["query_ids"]) == query_count

    stderr = capsys.readouterr().err
    assert "CLAIM_PLAN" in stderr
    assert 'evidence_need="MARKET_PRICE"' in stderr
    assert 'evidence_need="FINANCIAL_STATEMENT"' in stderr
    assert 'missing_parameters=["bsns_year"]' in stderr
    assert "영업이익이 증가했다" not in stderr


@pytest.mark.asyncio
async def test_n6_zero_queries는_gateway를_호출하지_않고_missing이다(monkeypatch, capsys):
    monkeypatch.setenv("REVIEW_DEBUG_LOGS", "1")

    async def forbidden(**kwargs):
        raise AssertionError("collect_evidence must not be called")

    monkeypatch.setattr(nodes_module, "collect_evidence", forbidden)
    runtime_deps = deps()
    state = initial_state() | {"query_ids": []}

    patch = await make_nodes(runtime_deps)["n6"](state)

    assert patch == {
        "collections": {},
        "node_results": ["n6:missing"],
        "counters": {"external_calls": 0},
    }
    assert "COLLECTION_SKIP" in capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([NodeStatus.OK.value], "n6:ok"),
        ([NodeStatus.MISSING.value], "n6:missing"),
        ([NodeStatus.OK.value, NodeStatus.MISSING.value], "n6:partial"),
    ],
)
async def test_n6_nonempty_collection_status는_empty_set_vacuous_success를_쓰지_않는다(
    monkeypatch, statuses, expected
):
    async def fake_collect(**kwargs):
        return GatewayResult(
            collections={f"provider-{i}": {"status": status} for i, status in enumerate(statuses)},
            external_calls=len(statuses),
            provider_calls=(),
            failures=(),
        )

    monkeypatch.setattr(nodes_module, "collect_evidence", fake_collect)
    runtime_deps = deps()
    item = claim(40, verifiable=True)
    state = initial_state() | {"query_ids": [query(40, item.claim_id).query_id]}
    await runtime_deps.evidence_store.put_queries("run-s0", [query(40, item.claim_id)])

    patch = await make_nodes(runtime_deps)["n6"](state)

    assert patch["node_results"] == [expected]


@pytest.mark.asyncio
async def test_n8_unverifiable_fallback은_runtime_ULID_factory로_canonical_evaluation을_만든다():
    from app.runtime.ids import generate_ulid

    runtime_deps = replace(deps(), id_factory=generate_ulid)
    item = claim(50, verifiable=True, proposition="영업이익이 증가했다")
    state = await seed_claims(runtime_deps, [item])

    patch = await make_nodes(runtime_deps)["n8"](state)
    stored = await runtime_deps.review_store.get_claim_evaluations(
        patch["claim_evaluation_ids"]
    )

    assert len(stored) == 1
    assert stored[0].claim_id == item.claim_id


@pytest.mark.asyncio
async def test_n10은_wire_ViolationDraft를_frozen_Violation으로_변환한다():
    class GuardGateway(FlowGateway):
        async def invoke(self, slot, prompt_version, input_view, output_schema):
            if output_schema is GuardVerdictDraft:
                return GuardVerdictDraft(
                    violations=[
                        ViolationDraft(
                            slot_no=1,
                            rule_id="R1",
                            kind="pattern",
                            matched="사세요",
                            span_offset=[0, 3],
                        )
                    ]
                ), Usage(
                    model_slot=slot,
                    prompt_tokens=0,
                    output_tokens=0,
                    ctx_chars=0,
                )
            return await super().invoke(slot, prompt_version, input_view, output_schema)

    runtime_deps = deps(gateway=GuardGateway())
    runtime_deps.render_candidates.put(
        "run-s0",
        RenderDraft(slots=[RenderedSlotDraft(slot_no=1, text="사세요", citations=[])]),
    )

    patch = await make_nodes(runtime_deps)["n10"](initial_state())

    assert patch["node_results"] == ["n10:rewrite"]
    feedback = runtime_deps.render_candidates.get("run-s0").guard_feedback
    assert len(feedback) == 1
    assert isinstance(feedback[0], Violation)
    assert feedback[0].span_offset == (0, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["missing", "duplicate"])
async def test_n5_reference_corruption_blocks_without_query_write(corruption):
    runtime_deps = deps()
    item = claim(1, verifiable=True)
    state = await seed_claims(runtime_deps, [item])
    if corruption == "missing":
        state["claim_ids"] = [uid(9999)]
    else:
        state["claim_ids"] = [item.claim_id, item.claim_id]

    patch = await make_nodes(runtime_deps)["n5"](state)

    assert patch == {"node_results": ["n5:block:contract_violation"]}
    assert runtime_deps.evidence_store._queries == {}


@pytest.mark.asyncio
async def test_n7_calls_llm_only_for_evidenced_claims_and_preserves_query_lineage():
    runtime_deps = deps()
    a = claim(1, verifiable=True)
    b = claim(2, verifiable=False)
    c = claim(3, verifiable=True)
    state = await seed_claims(runtime_deps, [a, b, c])
    qa, qc = query(1, a.claim_id), query(3, c.claim_id)
    ea, ec = evidence(1), evidence(3)
    state["query_ids"] = await seed_queries_and_evidence(
        runtime_deps, [(a, qa, ea), (c, qc, ec)]
    )

    patch = await make_nodes(runtime_deps)["n7"](state)

    calls = [view for node, view in runtime_deps.model_gateway.calls if node == "n7"]
    assert [view.claim.claim_id for view in calls] == [a.claim_id, c.claim_id]
    assert patch == {"node_results": ["n7:ok"], "counters": {"llm_calls": 2}}
    assert (await runtime_deps.review_store.get_claim_evidence("run-s0", a.claim_id))[0].query_id == qa.query_id
    assert await runtime_deps.review_store.get_claim_evidence("run-s0", b.claim_id) == []
    assert (await runtime_deps.review_store.get_claim_evidence("run-s0", c.claim_id))[0].query_id == qc.query_id


@pytest.mark.asyncio
async def test_structured_decrease_for_increase_claim_reaches_n7_oppose_and_n8_contradicted():
    runtime_deps = deps()
    item = claim(1, verifiable=True, proposition="회사 A 영업이익이 증가했다")
    state = await seed_claims(runtime_deps, [item])
    q = query(1, item.claim_id)
    structured = evidence(
        1,
        raw_span="영업이익 당기 80 / 전기 100",
        normalized_value={
            "kind": "financial_statement",
            "account_name": "영업이익",
            "comparison_available": True,
            "current_value": 80,
            "prior_value": 100,
            "change_direction": "decrease",
            "comparison_basis": "ANNUAL",
        },
    )
    state["query_ids"] = await seed_queries_and_evidence(
        runtime_deps, [(item, q, structured)]
    )

    n7_patch = await make_nodes(runtime_deps)["n7"](state)
    links = await runtime_deps.review_store.get_claim_evidence("run-s0", item.claim_id)
    assert links[0].stance == "oppose"
    assert links[0].stance_source == "rule"

    n8_patch = await make_nodes(runtime_deps)["n8"](state | n7_patch)
    evaluations = await runtime_deps.review_store.get_claim_evaluations(
        n8_patch["claim_evaluation_ids"]
    )
    assert evaluations[0].oppose_evidence_ids == [structured.evidence_id]
    assert evaluations[0].verdict == "contradicted"


@pytest.mark.asyncio
async def test_structured_increase_for_increase_claim_is_rule_support_not_contradicted():
    runtime_deps = deps()
    item = claim(1, verifiable=True, proposition="회사 B 영업이익이 증가했다")
    state = await seed_claims(runtime_deps, [item])
    structured = evidence(
        1,
        normalized_value={
            "kind": "financial_statement",
            "account_name": "영업이익",
            "comparison_available": True,
            "current_value": 120,
            "prior_value": 100,
            "change_direction": "increase",
        },
    )
    state["query_ids"] = await seed_queries_and_evidence(
        runtime_deps, [(item, query(1, item.claim_id), structured)]
    )

    n7_patch = await make_nodes(runtime_deps)["n7"](state)
    links = await runtime_deps.review_store.get_claim_evidence("run-s0", item.claim_id)
    assert (links[0].stance, links[0].stance_source) == ("support", "rule")

    n8_patch = await make_nodes(runtime_deps)["n8"](state | n7_patch)
    evaluation = (await runtime_deps.review_store.get_claim_evaluations(
        n8_patch["claim_evaluation_ids"]
    ))[0]
    assert evaluation.verdict != "contradicted"


def test_structured_direction_rule_is_fail_closed_for_uncomparable_or_unrelated_evidence():
    increasing_claim = claim(1, verifiable=True, proposition="회사 A 영업이익이 증가했다")
    negated_claim = claim(2, verifiable=True, proposition="회사 A 영업이익이 증가하지 않았다")
    unavailable = evidence(
        1,
        normalized_value={
            "kind": "financial_statement",
            "account_name": "영업이익",
            "comparison_available": False,
            "change_direction": None,
        },
    )
    competitor = evidence(
        3,
        source_type="news",
        normalized_value={"kind": "news"},
        raw_span="회사 B가 시장 우위를 유지한다",
    )
    supporting = evidence(
        4,
        normalized_value={
            "kind": "financial_statement",
            "account_name": "영업이익",
            "comparison_available": True,
            "change_direction": "increase",
        },
    )

    assert nodes_module._structured_change_stance(increasing_claim, unavailable) is None
    assert nodes_module._structured_change_stance(increasing_claim, competitor) is None
    assert nodes_module._structured_change_stance(increasing_claim, supporting) == "support"
    assert nodes_module._structured_change_stance(negated_claim, supporting) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "providers", [("dart", "kiwoom"), ("dart", "kiwoom", "naver")]
)
async def test_n7_one_claim_accepts_multiple_provider_queries_with_distinct_evidence(
    providers,
):
    runtime_deps = deps()
    item = claim(1, verifiable=True)
    state = await seed_claims(runtime_deps, [item])
    pairs = [
        (item, query(index, item.claim_id, provider=provider), evidence(index))
        for index, provider in enumerate(providers, 1)
    ]
    state["query_ids"] = await seed_queries_and_evidence(runtime_deps, pairs)

    patch = await make_nodes(runtime_deps)["n7"](state)

    packet = next(view for node, view in runtime_deps.model_gateway.calls if node == "n7")
    assert {value.evidence_id for value in packet.evidence} == {
        pair[2].evidence_id for pair in pairs
    }
    stored = await runtime_deps.review_store.get_claim_evidence(
        "run-s0", item.claim_id
    )
    assert {value.evidence_id: value.query_id for value in stored} == {
        pair[2].evidence_id: pair[1].query_id for pair in pairs
    }
    assert patch == {"node_results": ["n7:ok"], "counters": {"llm_calls": 1}}


@pytest.mark.asyncio
async def test_n7_same_evidence_linked_to_multiple_claim_queries_uses_none_lineage():
    runtime_deps = deps()
    item = claim(1, verifiable=True)
    state = await seed_claims(runtime_deps, [item])
    dart = query(1, item.claim_id, provider="dart")
    kiwoom = query(2, item.claim_id, provider="kiwoom")
    shared = evidence(1)
    await runtime_deps.evidence_store.put_queries("run-s0", [dart, kiwoom])
    await runtime_deps.evidence_store.put_provider_calls(
        "run-s0",
        [
            ProviderCall(
                provider_request_id=shared.provider_request_id,
                run_id="run-s0",
                provider=dart.provider,
                endpoint=dart.endpoint,
                query_id=dart.query_id,
                latency_ms=0,
                idempotency_key="1" * 64,
                created_at=NOW,
            )
        ],
    )
    await runtime_deps.evidence_store.put_evidence_batch(
        "run-s0",
        [shared],
        [
            EvidenceQueryLink(evidence_id=shared.evidence_id, query_id=dart.query_id),
            EvidenceQueryLink(evidence_id=shared.evidence_id, query_id=kiwoom.query_id),
        ],
    )
    state["query_ids"] = [dart.query_id, kiwoom.query_id]

    patch = await make_nodes(runtime_deps)["n7"](state)

    stored = await runtime_deps.review_store.get_claim_evidence(
        "run-s0", item.claim_id
    )
    assert len(stored) == 1
    assert stored[0].evidence_id == shared.evidence_id
    assert stored[0].query_id is None
    assert patch == {"node_results": ["n7:ok"], "counters": {"llm_calls": 1}}


@pytest.mark.asyncio
async def test_n7_fails_closed_when_claim_evidence_comes_from_query_outside_state():
    runtime_deps = deps()
    item = claim(1, verifiable=True)
    state = await seed_claims(runtime_deps, [item])
    included = query(1, item.claim_id, provider="dart")
    omitted = query(2, item.claim_id, provider="kiwoom")
    extra = evidence(2)
    await runtime_deps.evidence_store.put_queries("run-s0", [included, omitted])
    await runtime_deps.evidence_store.put_provider_calls(
        "run-s0",
        [
            ProviderCall(
                provider_request_id=extra.provider_request_id,
                run_id="run-s0",
                provider=omitted.provider,
                endpoint=omitted.endpoint,
                query_id=omitted.query_id,
                latency_ms=0,
                idempotency_key="2" * 64,
                created_at=NOW,
            )
        ],
    )
    await runtime_deps.evidence_store.put_evidence_batch(
        "run-s0",
        [extra.model_copy(update={"source_type": "quote"})],
        [EvidenceQueryLink(evidence_id=extra.evidence_id, query_id=omitted.query_id)],
    )
    state["query_ids"] = [included.query_id]

    with pytest.raises(RuntimeError, match=ReasonCode.CONTRACT_VIOLATION.value):
        await make_nodes(runtime_deps)["n7"](state)


@pytest.mark.asyncio
async def test_n7_skips_non_verifiable_and_no_evidence_without_counter_patch():
    runtime_deps = deps()
    a = claim(1, verifiable=False)
    b = claim(2, verifiable=True)
    state = await seed_claims(runtime_deps, [a, b])
    qb = query(2, b.claim_id)
    state["query_ids"] = await seed_queries_and_evidence(runtime_deps, [(b, qb, None)])

    patch = await make_nodes(runtime_deps)["n7"](state)

    assert patch == {"node_results": ["n7:ok"]}
    assert runtime_deps.model_gateway.calls == []
    assert await runtime_deps.review_store.get_claim_evidence("run-s0", a.claim_id) == []
    assert await runtime_deps.review_store.get_claim_evidence("run-s0", b.claim_id) == []


class CounterStanceGateway(FlowGateway):
    def __init__(self, stance: str):
        super().__init__()
        self.stance = stance

    async def invoke(self, slot, prompt_version, input_view, output_schema):
        if output_schema is ClaimStanceDraft:
            self.calls.append(("n7", input_view))
            return ClaimStanceDraft(
                stances=[
                    ClaimEvidenceDraft(evidence_id=item.evidence_id, stance=self.stance)
                    for item in input_view.evidence
                ]
            ), Usage(model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1)
        if output_schema is ClaimEvaluationDraft:
            self.calls.append(("n8", input_view))
            evidence_ids = [item.evidence_id for item in input_view.evidence]
            buckets = {
                "support_evidence_ids": evidence_ids if self.stance == "support" else [],
                "oppose_evidence_ids": evidence_ids if self.stance == "oppose" else [],
                "neutral_evidence_ids": evidence_ids if self.stance == "neutral" else [],
                "unknown_evidence_ids": evidence_ids if self.stance == "unknown" else [],
            }
            return ClaimEvaluationDraft(
                citations=[
                    CitationRef(evidence_id=evidence_ids[0], span=input_view.evidence[0].raw_span)
                ],
                verdict={"support": "support", "oppose": "contradicted"}.get(
                    self.stance, "unverifiable"
                ),
                missing_dimensions=[],
                uncertainty_codes=[],
                **buckets,
            ), Usage(model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1)
        return await super().invoke(slot, prompt_version, input_view, output_schema)


class JudgmentRenderGateway(CounterStanceGateway):
    async def invoke(self, slot, prompt_version, input_view, output_schema):
        if output_schema is RenderDraft:
            self.calls.append(("n11", input_view))
            return RenderDraft(
                slots=[
                    RenderedSlotDraft(
                        slot_no=item.slot_no,
                        text=item.text,
                        citations=item.citations,
                    )
                    for item in input_view.slots
                ]
            ), Usage(model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1)
        return await super().invoke(slot, prompt_version, input_view, output_schema)


class OpposeWithoutDraftCitationGateway(CounterStanceGateway):
    async def invoke(self, slot, prompt_version, input_view, output_schema):
        if output_schema is ClaimEvaluationDraft:
            self.calls.append(("n8", input_view))
            evidence_ids = [item.evidence_id for item in input_view.evidence]
            return ClaimEvaluationDraft(
                citations=[],
                support_evidence_ids=[],
                oppose_evidence_ids=evidence_ids,
                neutral_evidence_ids=[],
                unknown_evidence_ids=[],
                verdict="contradicted",
                missing_dimensions=[],
                uncertainty_codes=[],
            ), Usage(model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1)
        if output_schema is FindingDraft:
            self.calls.append(("n9", input_view))
            return FindingDraft(
                slot_id=7,
                kind="unverified",
                citations=[],
                claim_evaluation_id=input_view.evaluations[0].claim_evaluation_id,
            ), Usage(model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1)
        return await super().invoke(slot, prompt_version, input_view, output_schema)


@pytest.mark.asyncio
@pytest.mark.parametrize("stance, expected_count", [("oppose", 1), ("support", 0)])
async def test_counter_query_intent_does_not_force_stance_and_n9_uses_runtime_lineage(
    stance, expected_count
):
    runtime_deps = deps(gateway=CounterStanceGateway(stance))
    item = claim(11, verifiable=True, slot_id=7, proposition="HBM 전망의 반대 근거")
    state = await seed_claims(runtime_deps, [item])
    counter = query(11, item.claim_id, provider="naver").model_copy(
        update={
            "intent": "counter",
            "endpoint": "news_search",
            "params": {"query": "삼성전자 HBM", "stock_code": "005930"},
        }
    )
    proof = evidence(11).model_copy(update={"source_type": "news"})
    state["query_ids"] = await seed_queries_and_evidence(runtime_deps, [(item, counter, proof)])

    n7_patch = await make_nodes(runtime_deps)["n7"](state)
    stored_stance = await runtime_deps.review_store.get_claim_evidence("run-s0", item.claim_id)
    assert stored_stance[0].stance == stance
    state["node_results"] += n7_patch["node_results"]

    n8_patch = await make_nodes(runtime_deps)["n8"](state)
    state["claim_evaluation_ids"] = n8_patch["claim_evaluation_ids"]
    evaluation = (
        await runtime_deps.review_store.get_claim_evaluations(state["claim_evaluation_ids"])
    )[0]
    assert evaluation.oppose_evidence_ids == ([proof.evidence_id] if stance == "oppose" else [])

    n9_patch = await make_nodes(runtime_deps)["n9"](state)
    assert n9_patch["oppose"] == {
        "status": "verified",
        "count": expected_count,
        "queries": ["삼성전자 HBM"],
        "reason": None,
    }


@pytest.mark.asyncio
async def test_n9_counter_review_derives_citation_from_canonical_evidence():
    runtime_deps = deps(gateway=OpposeWithoutDraftCitationGateway("oppose"))
    item = claim(12, verifiable=True, slot_id=7, proposition="HBM 반대 뉴스")
    state = await seed_claims(runtime_deps, [item])
    counter = query(12, item.claim_id, provider="naver").model_copy(
        update={"intent": "counter", "params": {"query": "005930"}}
    )
    proof = evidence(12).model_copy(update={"source_type": "news"})
    state["query_ids"] = await seed_queries_and_evidence(
        runtime_deps, [(item, counter, proof)]
    )
    await make_nodes(runtime_deps)["n7"](state)
    n8_patch = await make_nodes(runtime_deps)["n8"](state)
    state["claim_evaluation_ids"] = n8_patch["claim_evaluation_ids"]

    n9_patch = await make_nodes(runtime_deps)["n9"](state)

    findings = await runtime_deps.review_store.get_findings(n9_patch["finding_ids"])
    mismatch = next(finding for finding in findings if finding.kind == "mismatch")
    assert mismatch.citations == [
        CitationRef(evidence_id=proof.evidence_id, span=proof.raw_span)
    ]


@pytest.mark.asyncio
async def test_counter_news_network_free_vertical_reaches_verified_oppose_block():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "items": [
                    {
                        "title": "HBM 수요 둔화 우려",
                        "description": "삼성전자 HBM 수요가 둔화할 수 있다는 전망이다.",
                        "link": "https://n.news.naver.com/mnews/article/001/777",
                        "originallink": "https://example.com/news/777",
                        "pubDate": "Sat, 22 Aug 2026 09:00:00 +0900",
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        runtime_deps = replace(
            deps(gateway=JudgmentRenderGateway("oppose")),
            adapters={"naver": NaverAdapter("id", "secret", client=client)},
            provider_admission=ProviderAdmissionController({"naver": 3}),
        )
        item = claim(21, verifiable=True, slot_id=7, proposition="HBM 수요 둔화 뉴스")
        state = await seed_claims(runtime_deps, [item])
        state["input_id"] = await runtime_deps.review_store.put_input(
            "run-s0",
            {
                "schema_version": "hybrid_intake/v1",
                "semantic_projection_version": "semantic_projection/v1",
                "masked_intake": {
                    "mode": "HYBRID",
                    "target": None,
                    "structured": [],
                    "free_text": [],
                },
                "masked_input": "",
                "masked_security_input": "",
            },
        )

        n5_patch = await make_nodes(runtime_deps)["n5"](state)
        state["query_ids"] = n5_patch["query_ids"]
        planned = await runtime_deps.evidence_store.get_queries(state["query_ids"])
        assert any(item.scope == "claim" and item.intent == "counter" for item in planned)

        n6_patch = await make_nodes(runtime_deps)["n6"](state)
        assert n6_patch["node_results"] == ["n6:partial"]
        assert (
            len(await runtime_deps.evidence_store.evidence_ids_for_queries(state["query_ids"])) == 1
        )

        await make_nodes(runtime_deps)["n7"](state)
        n8_patch = await make_nodes(runtime_deps)["n8"](state)
        state["claim_evaluation_ids"] = n8_patch["claim_evaluation_ids"]
        evaluation = (await runtime_deps.review_store.get_claim_evaluations(
            state["claim_evaluation_ids"]
        ))[0]
        n9_patch = await make_nodes(runtime_deps)["n9"](state)

        assert n9_patch["oppose"]["status"] == "verified"
        assert n9_patch["oppose"]["count"] == 1
        assert n9_patch["oppose"]["queries"] == ["삼성전자"]
        integration_view = next(
            view for node, view in runtime_deps.model_gateway.calls if node == "n9"
        )
        assert {(item.slot_id, item.status) for item in integration_view.missing_slots} == {
            (7, "absent"),
            (8, "absent"),
        }
        findings = await runtime_deps.review_store.get_findings(n9_patch["finding_ids"])
        assert {(finding.slot_id, finding.kind) for finding in findings} >= {
            (7, "mismatch"),
            (8, "missing"),
        }
        counter_finding = next(item for item in findings if item.kind == "mismatch")
        assert counter_finding.citations[0].evidence_id in evaluation.oppose_evidence_ids
        change_finding = next(item for item in findings if item.slot_id == 8)
        assert change_finding.citations == []

        state["finding_ids"] = n9_patch["finding_ids"]
        state["oppose"] = n9_patch["oppose"]
        state["node_results"] += n9_patch["node_results"]
        generate = await make_nodes(runtime_deps)["n11"](state)
        assert generate["node_results"] == ["n11:generate"]
        render_view = next(view for node, view in runtime_deps.model_gateway.calls if node == "n11")
        by_slot = {slot.slot_no: slot for slot in render_view.slots}
        assert "반대되는 근거" in by_slot[7].text
        assert by_slot[7].citations
        assert "다시 검토할 조건" in by_slot[8].text
        assert by_slot[8].citations == []
        assert sum(slot.slot_no == 8 for slot in render_view.slots) == 1

        guard = await make_nodes(runtime_deps)["n10"](state | generate)
        assert guard["node_results"] == ["n10:pass"]
        publish = await make_nodes(runtime_deps)["n11"](state | generate | guard)
        assert publish["node_results"] == ["n11:publish"]
        assert publish["report_id"]
        report = await runtime_deps.review_store.get_report(publish["report_id"])
        assert report is not None
        assert any(slot["citations"] for slot in report["rendered_slots"] if slot["slot_no"] == 7)
        assert all(not slot["citations"] for slot in report["rendered_slots"] if slot["slot_no"] == 8)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, expected",
    [
        (CurrentSlotStatus.CONFLICT, "서로 다른 내용"),
        (CurrentSlotStatus.AMBIGUOUS, "의미가 명확하지 않아"),
    ],
)
async def test_n11_preserves_slot8_conflict_and_ambiguity_as_ephemeral_review(
    monkeypatch, status, expected
):
    runtime_deps = deps()
    state = initial_state()
    projection = CurrentSlotProjection(
        slot_id=8,
        status=status,
        issue_ids=("issue-8",),
        response_state=ResponseState.UNKNOWN,
    )

    async def load_projection(*args, **kwargs):
        return (projection,)

    monkeypatch.setattr(nodes_module, "load_current_slot_projections", load_projection)

    patch = await make_nodes(runtime_deps)["n11"](state)

    assert patch["node_results"] == ["n11:generate"]
    render_view = next(view for node, view in runtime_deps.model_gateway.calls if node == "n11")
    assert len(render_view.slots) == 1
    assert expected in render_view.slots[0].text
    assert render_view.slots[0].citations == []
    assert runtime_deps.review_store._findings == {}


class EmptySafeGateway(FlowGateway):
    async def invoke(self, slot, prompt_version, input_view, output_schema):
        if output_schema is ClaimEvaluationDraft and not input_view.evidence:
            self.calls.append(("n8", input_view))
            return ClaimEvaluationDraft(
                citations=[],
                support_evidence_ids=[],
                oppose_evidence_ids=[],
                unknown_evidence_ids=[],
                verdict="unverifiable",
                missing_dimensions=[],
                uncertainty_codes=[],
            ), Usage(model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1)
        if output_schema is FindingDraft and (
            not input_view.evaluations
            or all(not item.citations for item in input_view.evaluations)
        ):
            self.calls.append(("n9", input_view))
            evaluation_id = (
                None
                if not input_view.evaluations
                else input_view.evaluations[0].claim_evaluation_id
            )
            return FindingDraft(
                slot_id=2,
                kind="missing" if evaluation_id is None else "unverified",
                citations=[],
                claim_evaluation_id=evaluation_id,
            ), Usage(model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1)
        return await super().invoke(slot, prompt_version, input_view, output_schema)


@pytest.mark.asyncio
async def test_n8_mixed_claims_skip_non_verifiable_and_rule_fallback_no_evidence():
    runtime_deps = deps(gateway=EmptySafeGateway())
    a = claim(1, verifiable=True)
    b = claim(2, verifiable=False)
    c = claim(3, verifiable=True, slot_id=3)
    state = await seed_claims(runtime_deps, [a, b, c])
    qa, qc, ea = query(1, a.claim_id), query(3, c.claim_id), evidence(1)
    state["query_ids"] = await seed_queries_and_evidence(
        runtime_deps, [(a, qa, ea), (c, qc, None)]
    )
    n7_patch = await make_nodes(runtime_deps)["n7"](state)

    patch = await make_nodes(runtime_deps)["n8"](state | n7_patch)
    evaluations = await runtime_deps.review_store.get_claim_evaluations(
        patch["claim_evaluation_ids"]
    )

    assert [item.claim_id for item in evaluations] == [a.claim_id, c.claim_id]
    assert evaluations[0].verdict == "support"
    assert evaluations[1].verdict == "unverifiable"
    assert evaluations[1].unknown_evidence_ids == []
    assert evaluations[1].uncertainty_codes == [ReasonCode.COVERAGE_TRUNCATED]
    assert [node for node, _ in runtime_deps.model_gateway.calls].count("n8") == 1
    assert patch["counters"] == {"llm_calls": 1}
    assert patch["node_results"] == ["n8:partial"]


@pytest.mark.asyncio
async def test_n7_n8_same_slot_claims는_claim_id별_lineage를_분리한다():
    runtime_deps = deps()
    demand = claim(1, verifiable=True, slot_id=4, proposition="HBM 수요 증가")
    supply = claim(2, verifiable=True, slot_id=4, proposition="HBM 공급 부족")
    state = await seed_claims(runtime_deps, [demand, supply])
    demand_query = query(1, demand.claim_id)
    supply_query = query(2, supply.claim_id)
    demand_evidence = evidence(1)
    supply_evidence = evidence(2)
    state["query_ids"] = await seed_queries_and_evidence(
        runtime_deps,
        [
            (demand, demand_query, demand_evidence),
            (supply, supply_query, supply_evidence),
        ],
    )

    n7_patch = await make_nodes(runtime_deps)["n7"](state)
    n8_patch = await make_nodes(runtime_deps)["n8"](state | n7_patch)
    evaluations = await runtime_deps.review_store.get_claim_evaluations(
        n8_patch["claim_evaluation_ids"]
    )

    demand_links = await runtime_deps.review_store.get_claim_evidence(
        "run-s0", demand.claim_id
    )
    supply_links = await runtime_deps.review_store.get_claim_evidence(
        "run-s0", supply.claim_id
    )
    assert [item.evidence_id for item in demand_links] == [demand_evidence.evidence_id]
    assert [item.evidence_id for item in supply_links] == [supply_evidence.evidence_id]
    assert [item.claim_id for item in evaluations] == [demand.claim_id, supply.claim_id]
    assert len({item.claim_evaluation_id for item in evaluations}) == 2


class AlwaysIncompleteEvaluation(FlowGateway):
    async def invoke(self, slot, prompt_version, input_view, output_schema):
        if output_schema is ClaimEvaluationDraft:
            self.calls.append(("n8", input_view))
            return ClaimEvaluationDraft(
                citations=[],
                support_evidence_ids=[],
                oppose_evidence_ids=[],
                unknown_evidence_ids=[],
                verdict="unverifiable",
                missing_dimensions=[],
                uncertainty_codes=[],
            ), Usage(model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1)
        return await super().invoke(slot, prompt_version, input_view, output_schema)


@pytest.mark.asyncio
async def test_n8_counter_matches_all_retry_invocations_across_claims():
    gateway = AlwaysIncompleteEvaluation()
    runtime_deps = deps(gateway=gateway)
    a, c = claim(1, verifiable=True), claim(3, verifiable=True)
    state = await seed_claims(runtime_deps, [a, c])
    qa, qc, ea, ec = query(1, a.claim_id), query(3, c.claim_id), evidence(1), evidence(3)
    state["query_ids"] = await seed_queries_and_evidence(
        runtime_deps, [(a, qa, ea), (c, qc, ec)]
    )
    n7_patch = await make_nodes(runtime_deps)["n7"](state)

    patch = await make_nodes(runtime_deps)["n8"](state | n7_patch)

    assert [node for node, _ in gateway.calls].count("n8") == 4
    assert patch["counters"] == {"llm_calls": 4}
    assert patch["node_results"] == ["n8:partial"]


@pytest.mark.asyncio
async def test_n9_mixed_uses_llm_only_for_evidence_backed_and_builds_no_evidence_finding():
    runtime_deps = deps(gateway=EmptySafeGateway())
    a = claim(1, verifiable=True)
    b = claim(2, verifiable=False)
    c = claim(3, verifiable=True, slot_id=3)
    state = await seed_claims(runtime_deps, [a, b, c])
    qa, qc, ea = query(1, a.claim_id), query(3, c.claim_id), evidence(1)
    state["query_ids"] = await seed_queries_and_evidence(
        runtime_deps, [(a, qa, ea), (c, qc, None)]
    )
    n7_patch = await make_nodes(runtime_deps)["n7"](state)
    n8_patch = await make_nodes(runtime_deps)["n8"](state | n7_patch)

    patch = await make_nodes(runtime_deps)["n9"](state | n7_patch | n8_patch)
    findings = await runtime_deps.review_store.get_findings(patch["finding_ids"])

    n9_views = [view for node, view in runtime_deps.model_gateway.calls if node == "n9"]
    assert len(n9_views) == 1 and isinstance(n9_views[0], IntegrationView)
    assert [item.claim_id for item in n9_views[0].evaluations] == [a.claim_id]
    deterministic = next(item for item in findings if item.slot_id == 3)
    c_evaluation = next(
        item
        for item in await runtime_deps.review_store.get_claim_evaluations(
            n8_patch["claim_evaluation_ids"]
        )
        if item.claim_id == c.claim_id
    )
    assert (deterministic.kind, deterministic.citations) == ("unverified", [])
    assert deterministic.claim_evaluation_id == c_evaluation.claim_evaluation_id
    assert patch["node_results"] == ["n9:ok"]
    assert patch["counters"] == {"llm_calls": 1}


class RetryThenValidFindingGateway(FlowGateway):
    def __init__(self):
        super().__init__()
        self.n9_attempts = 0

    async def invoke(self, slot, prompt_version, input_view, output_schema):
        if output_schema is FindingDraft:
            self.calls.append(("n9", input_view))
            self.n9_attempts += 1
            evaluation = input_view.evaluations[0]
            if self.n9_attempts == 1:
                return FindingDraft(
                    slot_id=1,
                    kind="mismatch",
                    citations=[],
                    claim_evaluation_id=evaluation.claim_evaluation_id,
                ), Usage(model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1)
            return FindingDraft(
                slot_id=1,
                kind="unverified",
                citations=[evaluation.citations[0]],
                claim_evaluation_id=evaluation.claim_evaluation_id,
            ), Usage(model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1)
        return await super().invoke(slot, prompt_version, input_view, output_schema)


@pytest.mark.asyncio
async def test_n9_counter는_assembly_retry의_실제_LLM_invocation을_센다():
    gateway = RetryThenValidFindingGateway()
    runtime_deps = deps(gateway=gateway)
    item = claim(1, verifiable=True)
    state = await seed_claims(runtime_deps, [item])
    planned = query(1, item.claim_id)
    proof = evidence(1)
    state["query_ids"] = await seed_queries_and_evidence(
        runtime_deps, [(item, planned, proof)]
    )
    n7_patch = await make_nodes(runtime_deps)["n7"](state)
    n8_patch = await make_nodes(runtime_deps)["n8"](state | n7_patch)

    patch = await make_nodes(runtime_deps)["n9"](state | n7_patch | n8_patch)

    assert gateway.n9_attempts == 2
    assert patch["counters"] == {"llm_calls": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["non_verifiable_only", "no_evidence_only"])
async def test_n9_zero_evidence_backed_is_partial_not_blocked(case):
    """근거 0건은 Report 를 없앨 이유가 아니라 Report 에 실을 결과다.

    이전 계약은 여기서 block:evidence_insufficient 를 내고 n12 로 빠졌다.
    그런데 Query 를 하나도 못 만든 경우에는 partial 로 보고서가 나왔기 때문에
    "검색할 게 없으면 보고서가 나오고, 검색했는데 0건이면 안 나온다" 가 됐다.
    뉴스 0건은 정상 상황이므로 두 경우 모두 partial 로 통일한다.

    block 은 계약 위반과 안전 차단에만 남는다.
    """
    runtime_deps = deps(gateway=EmptySafeGateway())
    item = claim(1, verifiable=case == "no_evidence_only", slot_id=2)
    state = await seed_claims(runtime_deps, [item])
    if case == "no_evidence_only":
        q = query(1, item.claim_id)
        state["query_ids"] = await seed_queries_and_evidence(runtime_deps, [(item, q, None)])
    n8_patch = await make_nodes(runtime_deps)["n8"](state)

    patch = await make_nodes(runtime_deps)["n9"](state | n8_patch)

    findings = await runtime_deps.review_store.get_findings(patch["finding_ids"])
    assert patch["node_results"] == ["n9:partial"]
    assert not any(":block:" in item for item in patch["node_results"]), (
        "block 이 남으면 그래프가 n12 로 빠져 보고서가 나오지 않는다"
    )
    assert "counters" not in patch
    assert [node for node, _ in runtime_deps.model_gateway.calls if node == "n9"] == []
    if case == "no_evidence_only":
        assert len(findings) == 2
        assert (findings[0].slot_id, findings[0].kind, findings[0].citations) == (
            2,
            "unverified",
            [],
        )
        assert (findings[1].slot_id, findings[1].kind, findings[1].citations) == (
            8,
            "missing",
            [],
        )
    else:
        assert [(finding.slot_id, finding.kind, finding.citations) for finding in findings] == [
            (8, "missing", [])
        ]


def fake_graph_nodes(block_at: str):
    from langgraph.types import Command

    nodes = {}

    for name in graph_module.VERTICES:
        async def node(state, runtime=None, *, current=name):
            if current == "intake_review":
                return Command(
                    update={"claim_ids": [uid(9001)], "node_results": ["intake_review:ok"]},
                    goto="n5",
                )
            if current == block_at:
                reason = "contract_violation" if current == "n5" else "evidence_insufficient"
                return {"node_results": [f"{current}:block:{reason}"]}
            return {"node_results": [f"{current}:ok"]}

        nodes[name] = node
    return nodes


@pytest.mark.asyncio
@pytest.mark.parametrize(("block_at", "not_reached"), [("n5", "n6"), ("n9", "n11")])
async def test_graph_routes_n5_and_n9_blocks_directly_to_n12(monkeypatch, block_at, not_reached):
    monkeypatch.setattr(graph_module, "make_nodes", lambda deps: fake_graph_nodes(block_at))

    result = await graph_module.build_graph(object()).ainvoke(initial_state())

    assert f"{block_at}:block:" in "|".join(result["node_results"])
    assert f"{not_reached}:ok" not in result["node_results"]
    assert result["node_results"][-1] == "n12:ok"
