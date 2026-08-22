import asyncio
import os
from datetime import timedelta, timezone

import asyncpg
import pytest

from app.schemas.frozen import EvidenceQueryLink
from app.store.errors import StoreConflictError, StoreLineageError, StorePersistenceError
from app.store.sql_evidence_store import SqlEvidenceStore
from tests.store.evidence_store_contract import evidence, link, provider_call, query, uid

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_s1_query_round_trip(sql_store):
    item = query().model_copy(
        update={"created_at": query().created_at.astimezone(timezone(timedelta(hours=9)))}
    )
    await sql_store.put_queries("run-sql", [item])
    assert await sql_store.get_queries([item.query_id, item.query_id]) == [item, item]


@pytest.mark.asyncio
async def test_json_key_order_is_semantic_and_missing_query_is_key_error(sql_store):
    item = query().model_copy(update={"params": {"a": 1, "b": {"x": 2, "y": 3}}})
    reordered = item.model_copy(
        update={"params": {"b": {"y": 3, "x": 2}, "a": 1}}
    )
    await sql_store.put_queries("run-sql", [item])
    await sql_store.put_queries("run-sql", [reordered])
    assert await sql_store.get_queries([item.query_id]) == [item]
    with pytest.raises(KeyError):
        await sql_store.get_queries([uid(9999)])


@pytest.mark.asyncio
async def test_s2_s3_query_replay_conflict_and_batch_atomicity(sql_store):
    item = query()
    assert await sql_store.put_queries("run-sql", [item, item]) == [item.query_id] * 2
    conflict = item.model_copy(update={"endpoint": "changed"})
    new_item = query(2)
    with pytest.raises(StoreConflictError):
        await sql_store.put_queries("run-sql", [new_item, conflict])
    with pytest.raises(KeyError):
        await sql_store.get_queries([new_item.query_id])


@pytest.mark.asyncio
async def test_s4_s8_provider_call_round_trip_replay_and_retry_identity(sql_store):
    item = query()
    first, second = provider_call(1, item), provider_call(2, item)
    await sql_store.put_queries("run-sql", [item])
    await sql_store.put_provider_calls("run-sql", [second, first, first])
    assert await sql_store.get_provider_calls([first.provider_request_id] * 2) == [first] * 2
    assert await sql_store.provider_calls_for_query(item.query_id) == [first, second]
    assert first.idempotency_key == second.idempotency_key


@pytest.mark.asyncio
async def test_s6_provider_call_conflict_and_batch_atomicity(sql_store):
    item = query()
    existing = provider_call(1, item)
    new_call = provider_call(2, item)
    await sql_store.put_queries("run-sql", [item])
    await sql_store.put_provider_calls("run-sql", [existing])
    with pytest.raises(StoreConflictError):
        await sql_store.put_provider_calls(
            "run-sql", [new_call, existing.model_copy(update={"latency_ms": 999})]
        )
    with pytest.raises(KeyError):
        await sql_store.get_provider_calls([new_call.provider_request_id])


@pytest.mark.asyncio
async def test_s9_s12_evidence_round_trip_hash_scope_and_conflict(sql_store):
    q1, q2 = query(1), query(2)
    c1 = provider_call(1, q1)
    c2 = provider_call(2, q2, run_id="other")
    e1 = evidence(1, c1, digest="f" * 64)
    e2 = evidence(2, c2, digest="f" * 64)
    await sql_store.put_queries("run-sql", [q1])
    await sql_store.put_queries("other", [q2])
    await sql_store.put_provider_calls("run-sql", [c1])
    await sql_store.put_provider_calls("other", [c2])
    await sql_store.put_many("run-sql", [e1])
    await sql_store.put_many("other", [e2])
    assert await sql_store.get_many([e1.evidence_id]) == [e1]
    assert await sql_store.find_by_sha256("run-sql", ["f" * 64, "0" * 64]) == {
        "f" * 64: e1.evidence_id
    }
    with pytest.raises(StoreConflictError):
        await sql_store.put_many("run-sql", [e1.model_copy(update={"raw_span": "changed"})])


@pytest.mark.asyncio
async def test_s13_s14_multiple_queries_link_to_one_evidence(sql_store):
    q1, q2 = query(1), query(2)
    call = provider_call(1, q1)
    item = evidence(1, call)
    await sql_store.put_queries("run-sql", [q1, q2])
    await sql_store.put_provider_calls("run-sql", [call])
    links = [link(item, q1), link(item, q2)]
    await sql_store.put_evidence_batch("run-sql", [item], links + links)
    assert await sql_store.evidence_ids_for_queries([q1.query_id]) == [item.evidence_id]
    assert await sql_store.evidence_ids_for_queries([q2.query_id]) == [item.evidence_id]


@pytest.mark.asyncio
async def test_s15_s17_dangling_and_mismatched_lineage(sql_store):
    item = query()
    call = provider_call(1, item)
    with pytest.raises(StoreLineageError):
        await sql_store.put_provider_calls("run-sql", [call])
    await sql_store.put_queries("run-sql", [item])
    with pytest.raises(StoreLineageError):
        await sql_store.put_provider_calls(
            "run-sql", [call.model_copy(update={"provider": "kiwoom"})]
        )
    with pytest.raises(StoreLineageError):
        await sql_store.put_many("run-sql", [evidence(1, call)])


@pytest.mark.asyncio
async def test_s18_concurrent_exact_evidence_adoption(sql_store, postgres_pool):
    item_query = query()
    call = provider_call(1, item_query)
    item = evidence(1, call)
    await sql_store.put_queries("run-sql", [item_query])
    await sql_store.put_provider_calls("run-sql", [call])
    await asyncio.gather(
        sql_store.put_evidence_batch("run-sql", [item], [link(item, item_query)]),
        SqlEvidenceStore(postgres_pool).put_evidence_batch(
            "run-sql", [item], [link(item, item_query)]
        ),
    )
    async with postgres_pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM evidence") == 1


@pytest.mark.asyncio
async def test_concurrent_conflicting_evidence_hash_race(sql_store, postgres_pool):
    item_query = query()
    call = provider_call(1, item_query)
    first = evidence(1, call, digest="e" * 64)
    second = evidence(2, call, digest="e" * 64)
    await sql_store.put_queries("run-sql", [item_query])
    await sql_store.put_provider_calls("run-sql", [call])
    outcomes = await asyncio.gather(
        sql_store.put_evidence_batch("run-sql", [first], [link(first, item_query)]),
        SqlEvidenceStore(postgres_pool).put_evidence_batch(
            "run-sql", [second], [link(second, item_query)]
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(outcome, StoreConflictError) for outcome in outcomes) == 1
    async with postgres_pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM evidence") == 1


@pytest.mark.asyncio
async def test_s19_s20_concurrent_provider_call_replay_and_conflict(sql_store, postgres_pool):
    item = query()
    call = provider_call(1, item)
    await sql_store.put_queries("run-sql", [item])
    await asyncio.gather(
        sql_store.put_provider_calls("run-sql", [call]),
        SqlEvidenceStore(postgres_pool).put_provider_calls("run-sql", [call]),
    )
    conflicting = call.model_copy(update={"latency_ms": 999})
    outcomes = await asyncio.gather(
        sql_store.put_provider_calls("run-sql", [call]),
        SqlEvidenceStore(postgres_pool).put_provider_calls("run-sql", [conflicting]),
        return_exceptions=True,
    )
    assert sum(isinstance(outcome, StoreConflictError) for outcome in outcomes) == 1
    assert await sql_store.get_provider_calls([call.provider_request_id]) == [call]


@pytest.mark.asyncio
async def test_s21_s22_atomic_rollback_keeps_provider_call(sql_store):
    item_query = query()
    call = provider_call(1, item_query)
    item = evidence(1, call)
    await sql_store.put_queries("run-sql", [item_query])
    await sql_store.put_provider_calls("run-sql", [call])
    invalid = EvidenceQueryLink(evidence_id=item.evidence_id, query_id=uid(9999))
    with pytest.raises(StoreLineageError):
        await sql_store.put_evidence_batch("run-sql", [item], [invalid])
    with pytest.raises(KeyError):
        await sql_store.get_many([item.evidence_id])
    assert await sql_store.get_provider_calls([call.provider_request_id]) == [call]


@pytest.mark.asyncio
async def test_s23_new_pool_and_store_read_committed_records(sql_store):
    item = query()
    await sql_store.put_queries("run-sql", [item])
    dsn = os.environ["TEST_POSTGRES_DSN"]
    new_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    try:
        assert await SqlEvidenceStore(new_pool).get_queries([item.query_id]) == [item]
    finally:
        await new_pool.close()


@pytest.mark.asyncio
async def test_physical_pk_fk_unique_and_composite_ownership(postgres_pool, sql_store):
    item = query()
    call = provider_call(1, item)
    canonical = evidence(1, call)
    await sql_store.put_queries("run-sql", [item])
    await sql_store.put_provider_calls("run-sql", [call])
    await sql_store.put_evidence_batch("run-sql", [canonical], [link(canonical, item)])
    async with postgres_pool.acquire() as connection:
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                "INSERT INTO acquisition_queries SELECT * FROM acquisition_queries"
            )
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute("INSERT INTO provider_calls SELECT * FROM provider_calls")
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                "INSERT INTO evidence_query_links SELECT * FROM evidence_query_links"
            )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO evidence_query_links (evidence_id, query_id)
                VALUES ($1,$2)
                """,
                uid(9998),
                item.query_id,
            )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO evidence_query_links (evidence_id, query_id)
                VALUES ($1,$2)
                """,
                canonical.evidence_id,
                uid(9997),
            )
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                """
                INSERT INTO evidence
                SELECT $1, run_id, source_type, source_ref, source_url, publisher,
                       published_at, fetched_at, raw_span, span_scope, content_sha256,
                       normalized_value, provider_request_id, as_of
                FROM evidence WHERE evidence_id=$2
                """,
                uid(3999),
                canonical.evidence_id,
            )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO provider_calls
                SELECT $1, run_id, 'kiwoom', endpoint, query_id, http_status,
                       latency_ms, cache_hit, reason_code, idempotency_key, created_at
                FROM provider_calls WHERE provider_request_id=$2
                """,
                uid(2998),
                call.provider_request_id,
            )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO provider_calls
                SELECT $1, 'other-run', provider, endpoint, query_id, http_status,
                       latency_ms, cache_hit, reason_code, idempotency_key, created_at
                FROM provider_calls WHERE provider_request_id=$2
                """,
                uid(2997),
                call.provider_request_id,
            )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO evidence
                SELECT $1, run_id, source_type, source_ref, source_url, publisher,
                       published_at, fetched_at, raw_span, span_scope, $2,
                       normalized_value, $3, as_of
                FROM evidence WHERE evidence_id=$4
                """,
                uid(3998),
                "d" * 64,
                uid(2996),
                canonical.evidence_id,
            )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO evidence
                SELECT $1, 'other-run', source_type, source_ref, source_url, publisher,
                       published_at, fetched_at, raw_span, span_scope, $2,
                       normalized_value, provider_request_id, as_of
                FROM evidence WHERE evidence_id=$3
                """,
                uid(3997),
                "c" * 64,
                canonical.evidence_id,
            )


@pytest.mark.asyncio
async def test_closed_pool_maps_to_store_persistence_error(postgres_pool):
    store = SqlEvidenceStore(postgres_pool)
    await postgres_pool.close()
    with pytest.raises(StorePersistenceError):
        await store.get_queries([uid(1001)])
