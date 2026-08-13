import asyncio
from datetime import UTC, datetime

import pytest

from app.schemas.frozen import Evidence, EvidenceQueryLink, Query
from app.store.memory_evidence_store import MemoryEvidenceStore

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def U(n):
    return f"01K5ZTQ9X7WPCVN2M4H8JRAB{n}D"


def query(n=1, claim=None):
    return Query(
        query_id=U(n),
        scope="claim" if claim else "stock",
        claim_id=claim,
        intent="verify",
        provider="dart",
        endpoint="/dart",
        params={},
        created_at=NOW,
    )


def evidence(n=1, digest=None):
    return Evidence(
        evidence_id=U(n),
        source_type="dart",
        source_ref=f"ref-{n}",
        fetched_at=NOW,
        raw_span=f"span-{n}",
        span_scope="structured_field",
        content_sha256=digest or f"{n:064x}",
        provider_request_id=U(9),
        as_of=NOW,
    )


def run(coro):
    return asyncio.run(coro)


def test_query와_evidence의_idempotency_conflict_run_scope를_검증한다():
    store = MemoryEvidenceStore()
    q = query()
    e = evidence()
    assert run(store.put_queries("r1", [q])) == [q.query_id]
    assert run(store.put_queries("r1", [q])) == [q.query_id]
    with pytest.raises(ValueError):
        run(store.put_queries("r2", [q]))
    assert run(store.put_many("r1", [e])) == [e.evidence_id]
    assert run(store.put_many("r1", [e])) == [e.evidence_id]
    with pytest.raises(ValueError):
        run(store.put_many("r2", [e]))
    with pytest.raises(ValueError):
        run(store.put_many("r1", [evidence(2, e.content_sha256)]))


def test_explicit_lookup은_요청순서와_missing_KeyError를_보존한다():
    store = MemoryEvidenceStore()
    qs = [query(1), query(2)]
    es = [evidence(1), evidence(2)]
    run(store.put_queries("r", qs))
    run(store.put_many("r", es))
    assert run(store.get_queries([qs[1].query_id, qs[0].query_id])) == [qs[1], qs[0]]
    assert run(store.get_many([es[1].evidence_id, es[0].evidence_id])) == [es[1], es[0]]
    with pytest.raises(KeyError):
        run(store.get_many([U(8)]))


def test_hash는_run별이고_link는_멱등이며_관계조회는_sort한다():
    store = MemoryEvidenceStore()
    claim = U(7)
    qs = [query(1, claim), query(2, claim)]
    es = [evidence(2), evidence(1)]
    run(store.put_queries("r", qs))
    run(store.put_many("r", es))
    assert run(store.find_by_sha256("other", [es[0].content_sha256])) == {}
    pairs = [
        EvidenceQueryLink(evidence_id=es[0].evidence_id, query_id=qs[0].query_id),
        EvidenceQueryLink(evidence_id=es[1].evidence_id, query_id=qs[1].query_id),
    ]
    run(store.link(pairs + pairs))
    assert len(store._links) == 2
    expected = sorted(e.evidence_id for e in es)
    assert run(store.evidence_ids_for_claim(claim)) == expected
    assert run(store.evidence_ids_for_queries([qs[1].query_id, qs[0].query_id])) == expected
    with pytest.raises(ValueError):
        run(store.link([EvidenceQueryLink(evidence_id=U(8), query_id=qs[0].query_id)]))
