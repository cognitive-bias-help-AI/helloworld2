import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.gateway.assemble import ContractViolation, assemble_evidence, content_sha256
from app.schemas.frozen import EvidenceDraft, ProviderCall, Query
from app.store.memory_evidence_store import MemoryEvidenceStore

NOW = datetime(2026, 8, 13, tzinfo=UTC)
FETCHED = NOW + timedelta(seconds=5)
run = asyncio.run


def U(n):
    return f"01K5ZTQ9X7WPCVN2M4H8JRAB{n}D"


def query(n=1):
    return Query(
        query_id=U(n),
        scope="stock",
        intent="context",
        provider="dart",
        endpoint="/dart",
        params={},
        created_at=NOW,
    )


def call(q, run_id="r", **changes):
    data = dict(
        provider_request_id=U(9),
        run_id=run_id,
        provider=q.provider,
        endpoint=q.endpoint,
        query_id=q.query_id,
        latency_ms=1,
        idempotency_key="a" * 64,
        created_at=NOW,
    )
    return ProviderCall(**(data | changes))


def draft(**changes):
    return EvidenceDraft(
        **(
            dict(
                source_type="dart",
                source_ref="ref",
                raw_span=" A\n  B ",
                span_scope="structured_field",
                normalized_value={"v": 1},
            )
            | changes
        )
    )


def assemble(ds, q=None, c=None, run_id="r", store=None):
    q = q or query()
    c = c or call(q, run_id)
    store = store or MemoryEvidenceStore()
    run(store.put_queries(run_id, [q]))
    if (
        c.run_id == run_id
        and c.query_id == q.query_id
        and c.provider == q.provider
        and c.endpoint == q.endpoint
    ):
        run(store.put_provider_calls(run_id, [c]))
    return run(assemble_evidence(ds, q, c, NOW, run_id, FETCHED, store)), store


@pytest.mark.parametrize(
    "change",
    [{"run_id": "wrong"}, {"query_id": U(8)}, {"provider": "naver"}, {"endpoint": "/wrong"}],
)
def test_lineage_mismatch는_거부한다(change):
    q = query()
    with pytest.raises(ContractViolation):
        assemble([draft()], q, call(q, **change))


def test_source_type_mismatch와_naive_datetime을_거부한다():
    q = query()
    store = MemoryEvidenceStore()
    run(store.put_queries("r", [q]))
    with pytest.raises(ContractViolation):
        assemble([draft(source_type="news")], q, store=store)
    with pytest.raises(ContractViolation):
        run(assemble_evidence([], q, call(q), NOW.replace(tzinfo=None), "r", FETCHED, store))


def test_hash는_whitespace만_normalize하고_raw_span은_보존한다():
    assert content_sha256(draft()) == content_sha256(draft(raw_span="A B"))
    (items, dedup), _ = assemble([draft()])
    assert dedup == 0 and items[0].raw_span == " A\n  B "
    assert items[0].fetched_at == FETCHED and items[0].as_of == NOW
    assert items[0].provider_request_id == U(9)


def test_batch_same_payload는_collapse하고_conflict는_순서무관_거부한다():
    item = draft()
    (items, dedup), _ = assemble([item, item])
    assert len(items) == 1 and dedup == 1
    conflict = draft(publisher="different")
    for values in ([item, conflict], [conflict, item]):
        with pytest.raises(ContractViolation):
            assemble(values)


def test_same_run_repeat와_query_link는_멱등이다():
    store = MemoryEvidenceStore()
    q = query()
    run(store.put_queries("r", [q]))
    c = call(q)
    run(store.put_provider_calls("r", [c]))
    first = run(assemble_evidence([draft()], q, c, NOW, "r", FETCHED, store))
    second = run(assemble_evidence([draft()], q, c, NOW, "r", FETCHED, store))
    assert len(first[0]) == 1 and first[1] == 0 and second == ([], 1)
    assert len(store._links) == 1
    assert run(store.evidence_ids_for_queries([q.query_id])) == [first[0][0].evidence_id]


def test_same_content_new_query는기존_evidence에_link하고_new_run은별도채택한다():
    store = MemoryEvidenceStore()
    q1, q2 = query(1), query(2)
    run(store.put_queries("r", [q1, q2]))
    c1, c2 = call(q1), call(q2, provider_request_id=U(8))
    run(store.put_provider_calls("r", [c1, c2]))
    first = run(assemble_evidence([draft()], q1, c1, NOW, "r", FETCHED, store))[0][0]
    assert run(assemble_evidence([draft()], q2, c2, NOW, "r", FETCHED, store)) == ([], 1)
    assert run(store.evidence_ids_for_queries([q1.query_id, q2.query_id])) == [first.evidence_id]
    q3 = query(3)
    run(store.put_queries("other", [q3]))
    c3 = call(q3, "other", provider_request_id=U(7))
    run(store.put_provider_calls("other", [c3]))
    other = run(assemble_evidence([draft()], q3, c3, NOW, "other", FETCHED, store))[
        0
    ]
    assert len(other) == 1 and other[0].evidence_id != first.evidence_id


class AtomicOnlyStore(MemoryEvidenceStore):
    async def put_many(self, run_id, evs):
        raise AssertionError("legacy put_many path used")

    async def link(self, pairs):
        raise AssertionError("legacy link path used")


def test_assembler_uses_atomic_evidence_adoption_operation():
    store = AtomicOnlyStore()
    q = query()
    c = call(q)
    run(store.put_queries("r", [q]))
    run(store.put_provider_calls("r", [c]))

    items, dedup = run(assemble_evidence([draft()], q, c, NOW, "r", FETCHED, store))

    assert len(items) == 1 and dedup == 0
    assert run(store.evidence_ids_for_queries([q.query_id])) == [items[0].evidence_id]
