"""Durable asyncpg implementation of the acquisition Store contract."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from app.schemas.frozen import (
    PROVIDER_SOURCE_TYPE,
    Evidence,
    EvidenceQueryLink,
    ProviderCall,
    Query,
)
from app.store.errors import (
    StoreConflictError,
    StoreError,
    StoreLineageError,
    StorePersistenceError,
)
from app.store.json_value import validate_json_native


def _json_encode(value: object) -> str:
    validate_json_native(value, path="acquisition JSON")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_decode(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


def _query_from_row(row) -> Query:
    return Query(
        query_id=row["query_id"],
        scope=row["scope"],
        claim_id=row["claim_id"],
        intent=row["intent"],
        provider=row["provider"],
        endpoint=row["endpoint"],
        params=_json_decode(row["params"]),
        created_at=row["created_at"],
    )


def _provider_call_from_row(row) -> ProviderCall:
    return ProviderCall(
        provider_request_id=row["provider_request_id"],
        run_id=row["run_id"],
        provider=row["provider"],
        endpoint=row["endpoint"],
        query_id=row["query_id"],
        http_status=row["http_status"],
        latency_ms=row["latency_ms"],
        cache_hit=row["cache_hit"],
        reason_code=row["reason_code"],
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
    )


def _evidence_from_row(row) -> Evidence:
    return Evidence(
        evidence_id=row["evidence_id"],
        source_type=row["source_type"],
        source_ref=row["source_ref"],
        source_url=row["source_url"],
        publisher=row["publisher"],
        published_at=row["published_at"],
        fetched_at=row["fetched_at"],
        raw_span=row["raw_span"],
        span_scope=row["span_scope"],
        content_sha256=row["content_sha256"],
        normalized_value=_json_decode(row["normalized_value"]),
        provider_request_id=row["provider_request_id"],
        as_of=row["as_of"],
    )


class SqlEvidenceStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @property
    def pool(self) -> asyncpg.Pool:
        return self._pool

    @asynccontextmanager
    async def _connection(self):
        try:
            async with self._pool.acquire() as connection:
                yield connection
        except asyncio.CancelledError:
            raise
        except StoreError:
            raise
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
            raise StorePersistenceError("PostgreSQL persistence operation failed") from exc

    async def put_queries(self, run_id: str, queries: list[Query]) -> list[str]:
        batch: dict[str, Query] = {}
        for query in queries:
            validate_json_native(query.params, path="Query.params")
            previous = batch.get(query.query_id)
            if previous is not None and previous != query:
                raise StoreConflictError("query_id ownership/payload conflict")
            batch[query.query_id] = query
        async with self._connection() as connection, connection.transaction():
            for query in batch.values():
                inserted = await connection.fetchval(
                    """
                    INSERT INTO acquisition_queries
                        (query_id, run_id, scope, claim_id, intent, provider, endpoint,
                         params, created_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9)
                    ON CONFLICT DO NOTHING RETURNING query_id
                    """,
                    query.query_id,
                    run_id,
                    query.scope,
                    query.claim_id,
                    query.intent,
                    query.provider,
                    query.endpoint,
                    _json_encode(query.params),
                    query.created_at,
                )
                if inserted is None:
                    row = await connection.fetchrow(
                        "SELECT * FROM acquisition_queries WHERE query_id=$1",
                        query.query_id,
                    )
                    if row is None or row["run_id"] != run_id or _query_from_row(row) != query:
                        raise StoreConflictError("query_id ownership/payload conflict")
        return [query.query_id for query in queries]

    async def get_queries(self, query_ids: list[str]) -> list[Query]:
        rows = await self._rows_by_ids(
            "acquisition_queries", "query_id", query_ids, _query_from_row
        )
        return rows

    async def put_provider_calls(
        self, run_id: str, calls: list[ProviderCall]
    ) -> list[str]:
        batch: dict[str, ProviderCall] = {}
        for call in calls:
            previous = batch.get(call.provider_request_id)
            if previous is not None and previous != call:
                raise StoreConflictError("provider_request_id ownership/payload conflict")
            batch[call.provider_request_id] = call
        async with self._connection() as connection, connection.transaction():
            for call in batch.values():
                query_row = await connection.fetchrow(
                    "SELECT * FROM acquisition_queries WHERE query_id=$1", call.query_id
                )
                if query_row is None:
                    raise StoreLineageError("dangling ProviderCall query")
                query = _query_from_row(query_row)
                if (
                    call.run_id != run_id
                    or query_row["run_id"] != run_id
                    or call.provider != query.provider
                    or call.endpoint != query.endpoint
                ):
                    raise StoreLineageError("ProviderCall Query ownership/payload conflict")
                inserted = await connection.fetchval(
                    """
                    INSERT INTO provider_calls
                        (provider_request_id, run_id, provider, endpoint, query_id,
                         http_status, latency_ms, cache_hit, reason_code,
                         idempotency_key, created_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                    ON CONFLICT DO NOTHING RETURNING provider_request_id
                    """,
                    call.provider_request_id,
                    run_id,
                    call.provider,
                    call.endpoint,
                    call.query_id,
                    call.http_status,
                    call.latency_ms,
                    call.cache_hit,
                    call.reason_code.value if call.reason_code else None,
                    call.idempotency_key,
                    call.created_at,
                )
                if inserted is None:
                    row = await connection.fetchrow(
                        "SELECT * FROM provider_calls WHERE provider_request_id=$1",
                        call.provider_request_id,
                    )
                    if row is None or _provider_call_from_row(row) != call:
                        raise StoreConflictError(
                            "provider_request_id ownership/payload conflict"
                        )
        return [call.provider_request_id for call in calls]

    async def get_provider_calls(
        self, provider_request_ids: list[str]
    ) -> list[ProviderCall]:
        return await self._rows_by_ids(
            "provider_calls",
            "provider_request_id",
            provider_request_ids,
            _provider_call_from_row,
        )

    async def provider_calls_for_query(self, query_id: str) -> list[ProviderCall]:
        async with self._connection() as connection:
            rows = await connection.fetch(
                """
                SELECT * FROM provider_calls WHERE query_id=$1
                ORDER BY created_at, provider_request_id
                """,
                query_id,
            )
        return [_provider_call_from_row(row) for row in rows]

    async def put_many(self, run_id: str, evs: list[Evidence]) -> list[str]:
        async with self._connection() as connection, connection.transaction():
            await self._put_evidence(connection, run_id, evs)
        return [item.evidence_id for item in evs]

    async def get_many(self, ids: list[str]) -> list[Evidence]:
        return await self._rows_by_ids("evidence", "evidence_id", ids, _evidence_from_row)

    async def find_by_sha256(self, run_id: str, hashes: list[str]) -> dict[str, str]:
        if not hashes:
            return {}
        async with self._connection() as connection:
            rows = await connection.fetch(
                """
                SELECT content_sha256, evidence_id FROM evidence
                WHERE run_id=$1 AND content_sha256=ANY($2::text[])
                """,
                run_id,
                hashes,
            )
        return {row["content_sha256"]: row["evidence_id"] for row in rows}

    async def link(self, pairs: list[EvidenceQueryLink]) -> None:
        async with self._connection() as connection, connection.transaction():
            await self._put_links(connection, pairs)

    async def put_evidence_batch(
        self,
        run_id: str,
        evidence: list[Evidence],
        links: list[EvidenceQueryLink],
    ) -> list[str]:
        async with self._connection() as connection, connection.transaction():
            await self._put_evidence(connection, run_id, evidence)
            linked_ids = await self._put_links(connection, links, run_id=run_id)
            if any(item.evidence_id not in linked_ids for item in evidence):
                raise StoreLineageError("incoming Evidence requires Query lineage")
        return [item.evidence_id for item in evidence]

    async def evidence_ids_for_claim(self, claim_id: str) -> list[str]:
        async with self._connection() as connection:
            rows = await connection.fetch(
                """
                SELECT DISTINCT l.evidence_id
                FROM evidence_query_links l
                JOIN acquisition_queries q ON q.query_id=l.query_id
                WHERE q.claim_id=$1 ORDER BY l.evidence_id
                """,
                claim_id,
            )
        return [row["evidence_id"] for row in rows]

    async def evidence_ids_for_queries(self, query_ids: list[str]) -> list[str]:
        if not query_ids:
            return []
        async with self._connection() as connection:
            rows = await connection.fetch(
                """
                SELECT DISTINCT evidence_id FROM evidence_query_links
                WHERE query_id=ANY($1::text[]) ORDER BY evidence_id
                """,
                query_ids,
            )
        return [row["evidence_id"] for row in rows]

    async def _put_evidence(self, connection, run_id: str, items: list[Evidence]) -> None:
        batch: dict[str, Evidence] = {}
        hashes: dict[str, str] = {}
        for item in items:
            validate_json_native(item.normalized_value, path="Evidence.normalized_value")
            previous = batch.get(item.evidence_id)
            if previous is not None and previous != item:
                raise StoreConflictError("evidence_id ownership/payload conflict")
            previous_id = hashes.get(item.content_sha256)
            if previous_id is not None and previous_id != item.evidence_id:
                raise StoreConflictError("(run_id, content_sha256) uniqueness violation")
            batch[item.evidence_id] = item
            hashes[item.content_sha256] = item.evidence_id
        for item in batch.values():
            call_row = await connection.fetchrow(
                "SELECT * FROM provider_calls WHERE provider_request_id=$1",
                item.provider_request_id,
            )
            if call_row is None:
                raise StoreLineageError("dangling Evidence ProviderCall")
            call = _provider_call_from_row(call_row)
            if call.run_id != run_id:
                raise StoreLineageError("Evidence ProviderCall run ownership mismatch")
            if item.source_type != PROVIDER_SOURCE_TYPE[call.provider]:
                raise StoreLineageError("Evidence ProviderCall source lineage mismatch")
            inserted = await connection.fetchval(
                """
                INSERT INTO evidence
                    (evidence_id, run_id, source_type, source_ref, source_url, publisher,
                     published_at, fetched_at, raw_span, span_scope, content_sha256,
                     normalized_value, provider_request_id, as_of)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13,$14)
                ON CONFLICT DO NOTHING RETURNING evidence_id
                """,
                item.evidence_id,
                run_id,
                item.source_type,
                item.source_ref,
                item.source_url,
                item.publisher,
                item.published_at,
                item.fetched_at,
                item.raw_span,
                item.span_scope,
                item.content_sha256,
                _json_encode(item.normalized_value),
                item.provider_request_id,
                item.as_of,
            )
            if inserted is None:
                row = await connection.fetchrow(
                    "SELECT * FROM evidence WHERE evidence_id=$1", item.evidence_id
                )
                if (
                    row is None
                    or row["run_id"] != run_id
                    or _evidence_from_row(row) != item
                ):
                    raise StoreConflictError("evidence identity/hash/payload conflict")

    async def _put_links(
        self, connection, pairs: list[EvidenceQueryLink], *, run_id: str | None = None
    ) -> set[str]:
        linked_ids: set[str] = set()
        for pair in pairs:
            evidence_row = await connection.fetchrow(
                "SELECT run_id FROM evidence WHERE evidence_id=$1", pair.evidence_id
            )
            query_row = await connection.fetchrow(
                "SELECT run_id FROM acquisition_queries WHERE query_id=$1", pair.query_id
            )
            if evidence_row is None:
                raise StoreLineageError("dangling EvidenceQueryLink Evidence")
            if query_row is None:
                raise StoreLineageError("dangling EvidenceQueryLink Query")
            if run_id is not None and (
                evidence_row["run_id"] != run_id or query_row["run_id"] != run_id
            ):
                raise StoreLineageError("EvidenceQueryLink run ownership mismatch")
            await connection.execute(
                """
                INSERT INTO evidence_query_links (evidence_id, query_id)
                VALUES ($1,$2) ON CONFLICT DO NOTHING
                """,
                pair.evidence_id,
                pair.query_id,
            )
            linked_ids.add(pair.evidence_id)
        return linked_ids

    async def _rows_by_ids(self, table: str, column: str, ids: list[str], convert):
        if not ids:
            return []
        allowed = {
            ("acquisition_queries", "query_id"),
            ("provider_calls", "provider_request_id"),
            ("evidence", "evidence_id"),
        }
        if (table, column) not in allowed:
            raise AssertionError("non-allowlisted SQL identifier")
        async with self._connection() as connection:
            rows = await connection.fetch(
                f"SELECT * FROM {table} WHERE {column}=ANY($1::text[])", ids
            )
        by_id: dict[str, Any] = {row[column]: convert(row) for row in rows}
        result = []
        for item_id in ids:
            if item_id not in by_id:
                raise KeyError(item_id)
            result.append(by_id[item_id])
        return result
