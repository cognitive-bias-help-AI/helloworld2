import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.schemas.frozen import Evidence, EvidenceQueryLink, ProviderCall, Query
from app.store.errors import StoreConflictError, StoreError, StoreLineageError
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


def provider_call(n=9, *, query_id=None, run_id="r1", reason_code=None):
    return ProviderCall(
        provider_request_id=U(n),
        run_id=run_id,
        provider="dart",
        endpoint="/dart",
        query_id=query_id or U(1),
        latency_ms=0,
        reason_code=reason_code,
        idempotency_key="a" * 64,
        created_at=NOW,
    )


def run(coro):
    return asyncio.run(coro)


def test_query와_evidence의_idempotency_conflict_run_scope를_검증한다():
    store = MemoryEvidenceStore()
    q = query()
    e = evidence()
    assert run(store.put_queries("r1", [q])) == [q.query_id]
    assert run(store.put_queries("r1", [q])) == [q.query_id]
    run(store.put_provider_calls("r1", [provider_call(query_id=q.query_id)]))
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
    run(store.put_provider_calls("r", [provider_call(query_id=qs[0].query_id, run_id="r")]))
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
    run(store.put_provider_calls("r", [provider_call(query_id=qs[0].query_id, run_id="r")]))
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


def test_provider_call은_exact_replay와_run_ownership을_fail_closed한다():
    store = MemoryEvidenceStore()
    q = query()
    call = provider_call(query_id=q.query_id)
    run(store.put_queries("r1", [q]))

    assert run(store.put_provider_calls("r1", [call])) == [call.provider_request_id]
    assert run(store.put_provider_calls("r1", [call])) == [call.provider_request_id]
    assert run(store.get_provider_calls([call.provider_request_id])) == [call]

    with pytest.raises(ValueError, match="provider_request_id ownership/payload conflict"):
        run(store.put_provider_calls("r1", [call.model_copy(update={"latency_ms": 1})]))
    with pytest.raises(ValueError, match="provider_request_id ownership/payload conflict"):
        run(store.put_provider_calls("r2", [call]))


def test_same_query의_multiple_attempts와_logical_idempotency_key를_허용한다():
    store = MemoryEvidenceStore()
    q1, q2 = query(1), query(2)
    run(store.put_queries("r1", [q1, q2]))
    calls = [provider_call(n, query_id=q1.query_id) for n in (7, 8, 9)]
    unrelated = provider_call(6, query_id=q2.query_id)

    run(store.put_provider_calls("r1", [calls[2], unrelated, calls[0], calls[1]]))

    assert run(store.provider_calls_for_query(q1.query_id)) == calls
    assert run(store.provider_calls_for_query(q2.query_id)) == [unrelated]
    assert {item.idempotency_key for item in calls} == {"a" * 64}


def test_provider_call은_dangling_query를_거부하고_evidence_lineage를_보장한다():
    store = MemoryEvidenceStore()
    q = query()
    call = provider_call(query_id=q.query_id)
    run(store.put_queries("r1", [q]))

    with pytest.raises(ValueError, match="dangling ProviderCall query"):
        run(store.put_provider_calls("r1", [provider_call(8, query_id=U(8))]))

    run(store.put_provider_calls("r1", [call]))
    assert run(store.put_many("r1", [evidence()])) == [evidence().evidence_id]


def prepared_store(*queries):
    store = MemoryEvidenceStore()
    items = list(queries) or [query()]
    run(store.put_queries("r1", items))
    calls = [provider_call(9 - index, query_id=item.query_id) for index, item in enumerate(items)]
    run(store.put_provider_calls("r1", calls))
    return store, items, calls


def test_atomic_evidence_batch_persists_evidence_and_link_together():
    store, queries, _ = prepared_store(query())
    item = evidence()
    link = EvidenceQueryLink(evidence_id=item.evidence_id, query_id=queries[0].query_id)

    assert run(store.put_evidence_batch("r1", [item], [link])) == [item.evidence_id]
    assert run(store.get_many([item.evidence_id])) == [item]
    assert run(store.evidence_ids_for_queries([queries[0].query_id])) == [item.evidence_id]


def test_atomic_evidence_batch_link_failure_has_no_partial_mutation_and_keeps_call():
    store, _, calls = prepared_store(query())
    item = evidence()
    invalid = EvidenceQueryLink(evidence_id=item.evidence_id, query_id=U(8))

    with pytest.raises(StoreLineageError, match="Query"):
        run(store.put_evidence_batch("r1", [item], [invalid]))

    with pytest.raises(KeyError):
        run(store.get_many([item.evidence_id]))
    assert run(store.get_provider_calls([calls[0].provider_request_id])) == calls
    assert run(store.evidence_ids_for_queries([U(8)])) == []


def test_atomic_evidence_batch_prevalidates_all_evidence_before_mutation():
    store, queries, _ = prepared_store(query())
    valid = evidence(1)
    invalid = evidence(2).model_copy(update={"provider_request_id": U(8)})
    links = [EvidenceQueryLink(evidence_id=valid.evidence_id, query_id=queries[0].query_id)]

    with pytest.raises(StoreLineageError, match="ProviderCall"):
        run(store.put_evidence_batch("r1", [valid, invalid], links))
    with pytest.raises(KeyError):
        run(store.get_many([valid.evidence_id]))


def test_atomic_evidence_batch_rejects_provider_call_run_mismatch():
    store, _, calls = prepared_store(query())
    item = evidence().model_copy(update={"provider_request_id": calls[0].provider_request_id})

    with pytest.raises(StoreLineageError, match="run"):
        run(store.put_evidence_batch("r2", [item], []))


def test_atomic_evidence_batch_exact_replay_and_duplicate_link_are_idempotent():
    store, queries, _ = prepared_store(query())
    item = evidence()
    link = EvidenceQueryLink(evidence_id=item.evidence_id, query_id=queries[0].query_id)

    assert run(store.put_evidence_batch("r1", [item], [link, link])) == [item.evidence_id]
    assert run(store.put_evidence_batch("r1", [item], [link])) == [item.evidence_id]
    assert run(store.evidence_ids_for_queries([queries[0].query_id])) == [item.evidence_id]


def test_atomic_evidence_batch_conflict_has_no_partial_mutation():
    store, queries, _ = prepared_store(query())
    first = evidence(1)
    link = EvidenceQueryLink(evidence_id=first.evidence_id, query_id=queries[0].query_id)
    run(store.put_evidence_batch("r1", [first], [link]))
    second = evidence(2)
    conflict = first.model_copy(update={"raw_span": "changed"})

    with pytest.raises(StoreConflictError):
        run(store.put_evidence_batch("r1", [second, conflict], []))
    with pytest.raises(KeyError):
        run(store.get_many([second.evidence_id]))
    assert run(store.get_many([first.evidence_id])) == [first]


@pytest.mark.parametrize(
    "unsupported",
    [
        Decimal("1.2"),
        NOW,
        date(2026, 8, 13),
        b"bytes",
        bytearray(b"bytes"),
        {"set"},
        (1, 2),
        {1: "value"},
        float("nan"),
        float("inf"),
        float("-inf"),
        object(),
    ],
)
def test_acquisition_json_fields_reject_non_json_native_values(unsupported):
    store = MemoryEvidenceStore()
    invalid_query = query().model_copy(update={"params": {"value": unsupported}})
    with pytest.raises(StoreError, match="JSON-native"):
        run(store.put_queries("r1", [invalid_query]))


def test_json_native_nested_values_and_key_order_are_semantically_equal():
    store = MemoryEvidenceStore()
    first = query().model_copy(
        update={"params": {"a": 1, "b": True, "c": None, "d": [1, "x", {"nested": 2.5}]}}
    )
    reordered = first.model_copy(
        update={"params": {"d": [1, "x", {"nested": 2.5}], "c": None, "b": True, "a": 1}}
    )

    assert run(store.put_queries("r1", [first])) == [first.query_id]
    assert run(store.put_queries("r1", [reordered])) == [first.query_id]
    assert run(store.get_queries([first.query_id])) == [first]


def test_evidence_normalized_value_uses_same_json_native_boundary():
    store, queries, _ = prepared_store(query())
    item = evidence().model_copy(update={"normalized_value": {"value": Decimal("1.2")}})
    link = EvidenceQueryLink(evidence_id=item.evidence_id, query_id=queries[0].query_id)

    with pytest.raises(StoreError, match="JSON-native"):
        run(store.put_evidence_batch("r1", [item], [link]))
    with pytest.raises(KeyError):
        run(store.get_many([item.evidence_id]))


def test_one_evidence_can_atomically_link_to_multiple_queries():
    q1, q2 = query(1), query(2)
    store, queries, _ = prepared_store(q1, q2)
    item = evidence()
    links = [
        EvidenceQueryLink(evidence_id=item.evidence_id, query_id=item_query.query_id)
        for item_query in queries
    ]

    run(store.put_evidence_batch("r1", [item], links))
    assert run(store.evidence_ids_for_queries([q1.query_id])) == [item.evidence_id]
    assert run(store.evidence_ids_for_queries([q2.query_id])) == [item.evidence_id]
