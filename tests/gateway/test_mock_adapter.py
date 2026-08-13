import asyncio
from datetime import UTC, datetime

import pytest

from app.gateway.adapters.mock import MockAdapter
from app.schemas.frozen import PROVIDER_SOURCE_TYPE, EvidenceDraft, Query, ReasonCode

QID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
NOW = datetime(2026, 8, 13, tzinfo=UTC)


def query(provider: str) -> Query:
    return Query(
        query_id=QID,
        scope="stock",
        intent="context",
        provider=provider,
        endpoint=f"/{provider}",
        params={"code": "005930"},
        created_at=NOW,
    )


@pytest.mark.parametrize("provider", ["dart", "naver", "kiwoom"])
def test_mock_adapter는_provider별_draft를_결정적으로_만든다(provider):
    adapter = MockAdapter(provider)
    q = query(provider)
    req = adapter.build_request(q, NOW)
    raw1 = asyncio.run(adapter.acall(req))
    raw2 = asyncio.run(adapter.acall(req))
    drafts1 = adapter.parse_response(raw1, q)
    drafts2 = adapter.parse_response(raw2, q)

    assert raw1 == raw2
    assert drafts1 == drafts2
    assert all(isinstance(item, EvidenceDraft) for item in drafts1)
    assert all(item.source_type == PROVIDER_SOURCE_TYPE[provider] for item in drafts1)
    assert all(
        item.published_at is None or item.published_at.utcoffset() is not None for item in drafts1
    )
    assert all(item.normalized_value for item in drafts1)
    assert (
        drafts1[0].source_url is None
        if provider == "kiwoom"
        else drafts1[0].source_url.startswith("https://")
    )
    assert not {
        "evidence_id",
        "content_sha256",
        "provider_request_id",
        "fetched_at",
        "as_of",
    } & set(EvidenceDraft.model_fields)


def test_mock_adapter는_다른_provider_query를_거부한다():
    with pytest.raises(ValueError):
        MockAdapter("dart").build_request(query("naver"), NOW)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"status": 500}, (ReasonCode.UPSTREAM_5XX, True)),
        ({"status": 429}, (ReasonCode.RATE_LIMIT, True)),
        ({"status": 401}, (ReasonCode.AUTH_FAILED, False)),
        ({"status": 403}, (ReasonCode.AUTH_FAILED, False)),
        ({"timeout": True}, (ReasonCode.UPSTREAM_TIMEOUT, True)),
    ],
)
def test_mock_adapter는_최소_오류를_분류한다(raw, expected):
    assert MockAdapter("naver").classify_error(raw) == expected


def test_mock_adapter는_429에만_결정적_rate_limit_hint를_준다():
    adapter = MockAdapter("naver")
    hint = adapter.rate_limit_hint({"status": 429})
    assert hint.provider == "naver"
    assert hint.retry_after_ms == 1000
    assert adapter.rate_limit_hint({"status": 500}) is None
