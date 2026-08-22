from datetime import UTC, datetime

from app.schemas.frozen import Evidence, EvidenceQueryLink, ProviderCall, Query

NOW = datetime(2026, 8, 21, tzinfo=UTC)


def uid(n: int) -> str:
    return f"01ARZ3NDEKTSV4RRFFQ69G{n:04d}"


def query(n: int = 1, *, claim_id: str | None = None, provider: str = "dart") -> Query:
    return Query(
        query_id=uid(1000 + n),
        scope="claim" if claim_id else "stock",
        claim_id=claim_id,
        intent="verify" if claim_id else "context",
        provider=provider,
        endpoint={"dart": "disclosure", "kiwoom": "quote", "naver": "search"}[provider],
        params={"nested": [1, True, None, {"value": 2.5}]},
        created_at=NOW,
    )


def provider_call(n: int, item: Query, *, run_id: str = "run-sql") -> ProviderCall:
    return ProviderCall(
        provider_request_id=uid(2000 + n),
        run_id=run_id,
        provider=item.provider,
        endpoint=item.endpoint,
        query_id=item.query_id,
        latency_ms=n,
        idempotency_key="a" * 64,
        created_at=NOW,
    )


def evidence(
    n: int,
    call: ProviderCall,
    *,
    digest: str | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=uid(3000 + n),
        source_type={"dart": "dart", "kiwoom": "quote", "naver": "news"}[call.provider],
        source_ref=f"ref-{n}",
        fetched_at=NOW,
        raw_span=f"evidence-{n}",
        span_scope="structured_field",
        content_sha256=digest or f"{n:064x}",
        normalized_value={"nested": [1, True, None, {"value": 2.5}]},
        provider_request_id=call.provider_request_id,
        as_of=NOW,
    )


def link(item: Evidence, item_query: Query) -> EvidenceQueryLink:
    return EvidenceQueryLink(evidence_id=item.evidence_id, query_id=item_query.query_id)
