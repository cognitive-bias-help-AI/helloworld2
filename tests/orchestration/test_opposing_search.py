from datetime import UTC, datetime

import pytest

from app.orchestration.opposing_search import build_oppose_block, render_query
from app.schemas.frozen import ProviderCall, Query, ReasonCode

NOW = datetime(2026, 8, 22, tzinfo=UTC)


def uid(n: int) -> str:
    return f"01ARZ3NDEKTSV4RRFFQ69G{n:04d}"


def query(n: int, *, provider: str = "naver", params=None) -> Query:
    endpoint = {"naver": "news_search", "dart": "disclosure_list", "kiwoom": "investor_flow"}[
        provider
    ]
    return Query(
        query_id=uid(n),
        scope="claim",
        claim_id=uid(9000 + n),
        intent="counter",
        provider=provider,
        endpoint=endpoint,
        params=params or {"query": "삼성전자 HBM", "stock_code": "005930"},
        created_at=NOW,
    )


def call(n: int, item: Query, reason: ReasonCode | None = None) -> ProviderCall:
    return ProviderCall(
        provider_request_id=uid(1000 + n),
        run_id="run-opposing",
        provider=item.provider,
        endpoint=item.endpoint,
        query_id=item.query_id,
        latency_ms=10,
        reason_code=reason,
        idempotency_key=f"{n:064x}",
        created_at=NOW,
    )


def summarize(queries, calls, links=None, oppose=()):
    return build_oppose_block(
        counter_queries=queries,
        provider_calls_by_query=calls,
        evidence_ids_by_query=links or {},
        oppose_evidence_ids=set(oppose),
    )


def test_no_counter_query_is_not_falsely_verified():
    result = summarize([], {})
    assert result.status == "unverified"
    assert result.count is None
    assert result.reason is ReasonCode.EVIDENCE_INSUFFICIENT
    assert result.queries is None


def test_success_counts_only_opposing_evidence_linked_to_counter_queries():
    item = query(1)
    result = summarize(
        [item],
        {item.query_id: [call(1, item)]},
        {item.query_id: [uid(101), uid(102), uid(103)]},
        [uid(101), uid(102), uid(999)],
    )
    assert result.status == "verified"
    assert result.count == 2
    assert result.reason is None
    assert result.queries == ["삼성전자 HBM"]


def test_success_does_not_turn_support_or_neutral_evidence_into_oppose():
    item = query(2)
    result = summarize(
        [item],
        {item.query_id: [call(2, item)]},
        {item.query_id: [uid(201), uid(202)]},
    )
    assert result.status == "verified"
    assert result.count == 0


def test_provider_no_result_is_completed_search_with_zero_opposing_evidence():
    item = query(3)
    result = summarize([item], {item.query_id: [call(3, item, ReasonCode.NO_RESULT)]})
    assert result.status == "verified"
    assert result.count == 0
    assert result.reason is None


@pytest.mark.parametrize("reason", [ReasonCode.RATE_LIMIT, ReasonCode.UPSTREAM_TIMEOUT])
def test_provider_failure_is_unverified_with_exact_reason(reason):
    item = query(4)
    result = summarize([item], {item.query_id: [call(4, item, reason)]})
    assert result.status == "unverified"
    assert result.count is None
    assert result.reason is reason
    assert result.queries == ["삼성전자 HBM"]


def test_partial_counter_execution_is_unverified():
    first, second = (
        query(5),
        query(6, provider="kiwoom", params={"stock_code": "005930", "date": "20260822"}),
    )
    result = summarize(
        [first, second],
        {
            first.query_id: [call(5, first)],
            second.query_id: [call(6, second, ReasonCode.UPSTREAM_TIMEOUT)],
        },
    )
    assert result.status == "unverified"
    assert result.reason is ReasonCode.UPSTREAM_TIMEOUT
    assert result.queries == ["삼성전자 HBM", "investor_flow:005930:20260822"]


def test_query_renderer_uses_safe_deterministic_values_not_placeholder():
    item = query(
        7,
        provider="dart",
        params={"stock_code": "005930", "api_key": "SECRET", "authorization": "Bearer secret"},
    )
    assert render_query(item) == "disclosure_list:005930"
    assert "SECRET" not in render_query(item)
    assert render_query(item) != "반대 근거 검색"
