"""Ephemeral reconstruction of system opposing-search runtime facts."""

from collections.abc import Mapping, Sequence

from app.schemas.frozen import OpposeBlock, ProviderCall, Query, ReasonCode

_COMPLETED_REASONS = {None, ReasonCode.NO_RESULT}


def render_query(query: Query) -> str:
    """Render a traceable query without credentials or arbitrary parameter dumps."""
    if query.provider == "naver" and isinstance(query.params.get("query"), str):
        value = query.params["query"].strip()
        if value:
            return value

    parts = [query.endpoint]
    for key in ("stock_code", "date", "base_date", "bsns_year", "reprt_code"):
        value = query.params.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            parts.append(str(value).strip())
    return ":".join(parts)


def build_oppose_block(
    *,
    counter_queries: Sequence[Query],
    provider_calls_by_query: Mapping[str, Sequence[ProviderCall]],
    evidence_ids_by_query: Mapping[str, Sequence[str]],
    oppose_evidence_ids: set[str],
) -> OpposeBlock:
    """Build an OpposeBlock solely from persisted counter-search lineage."""
    if not counter_queries:
        return OpposeBlock(
            status="unverified",
            reason=ReasonCode.EVIDENCE_INSUFFICIENT,
        )

    attempted_queries: list[str] = []
    counter_evidence_ids: set[str] = set()
    failure: ReasonCode | None = None
    for query in counter_queries:
        calls = provider_calls_by_query.get(query.query_id, ())
        if not calls:
            failure = failure or ReasonCode.CONTRACT_VIOLATION
            continue
        attempted_queries.append(render_query(query))
        final_call = max(
            calls,
            key=lambda call: (call.created_at, call.provider_request_id),
        )
        if final_call.reason_code not in _COMPLETED_REASONS:
            failure = failure or final_call.reason_code
        counter_evidence_ids.update(evidence_ids_by_query.get(query.query_id, ()))

    if failure is not None:
        return OpposeBlock(
            status="unverified",
            queries=attempted_queries or None,
            reason=failure,
        )

    return OpposeBlock(
        status="verified",
        count=len(counter_evidence_ids & oppose_evidence_ids),
        queries=attempted_queries,
    )
