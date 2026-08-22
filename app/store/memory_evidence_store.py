"""P0/S0 검증을 위한 비운영 메모리 EvidenceStore."""

from app.schemas.frozen import (
    PROVIDER_SOURCE_TYPE,
    Evidence,
    EvidenceQueryLink,
    ProviderCall,
    Query,
)
from app.store.errors import StoreConflictError, StoreLineageError
from app.store.json_value import validate_json_native


class MemoryEvidenceStore:
    def __init__(self) -> None:
        self._queries: dict[str, Query] = {}
        self._query_runs: dict[str, str] = {}
        self._provider_calls: dict[str, ProviderCall] = {}
        self._provider_call_runs: dict[str, str] = {}
        self._evidence: dict[str, Evidence] = {}
        self._evidence_runs: dict[str, str] = {}
        self._hashes: dict[tuple[str, str], str] = {}
        self._links: set[tuple[str, str]] = set()

    async def put_queries(self, run_id: str, queries: list[Query]) -> list[str]:
        for query in queries:
            validate_json_native(query.params, path="Query.params")
            old = self._queries.get(query.query_id)
            if old is not None and (old != query or self._query_runs[query.query_id] != run_id):
                raise StoreConflictError("query_id ownership/payload conflict")
        for query in queries:
            self._queries[query.query_id] = query
            self._query_runs[query.query_id] = run_id
        return [query.query_id for query in queries]

    async def get_queries(self, query_ids: list[str]) -> list[Query]:
        return [self._queries[query_id] for query_id in query_ids]

    async def put_provider_calls(
        self, run_id: str, calls: list[ProviderCall]
    ) -> list[str]:
        batch: dict[str, ProviderCall] = {}
        for call in calls:
            old = self._provider_calls.get(call.provider_request_id)
            if old is not None and (
                old != call or self._provider_call_runs[call.provider_request_id] != run_id
            ):
                raise StoreConflictError("provider_request_id ownership/payload conflict")
            previous = batch.get(call.provider_request_id)
            if previous is not None and previous != call:
                raise StoreConflictError("provider_request_id ownership/payload conflict")
            query = self._queries.get(call.query_id)
            if query is None:
                raise StoreLineageError("dangling ProviderCall query")
            if (
                call.run_id != run_id
                or self._query_runs[call.query_id] != run_id
                or call.provider != query.provider
                or call.endpoint != query.endpoint
            ):
                raise StoreLineageError("ProviderCall Query ownership/payload conflict")
            batch[call.provider_request_id] = call
        for provider_request_id, call in batch.items():
            self._provider_calls[provider_request_id] = call
            self._provider_call_runs[provider_request_id] = run_id
        return [call.provider_request_id for call in calls]

    async def get_provider_calls(
        self, provider_request_ids: list[str]
    ) -> list[ProviderCall]:
        return [self._provider_calls[item_id] for item_id in provider_request_ids]

    async def provider_calls_for_query(self, query_id: str) -> list[ProviderCall]:
        return sorted(
            (call for call in self._provider_calls.values() if call.query_id == query_id),
            key=lambda call: (call.created_at, call.provider_request_id),
        )

    async def put_many(self, run_id: str, evs: list[Evidence]) -> list[str]:
        evidence_state, run_state, hash_state = self._validated_evidence_state(run_id, evs)
        self._evidence = evidence_state
        self._evidence_runs = run_state
        self._hashes = hash_state
        return [evidence.evidence_id for evidence in evs]

    async def get_many(self, ids: list[str]) -> list[Evidence]:
        return [self._evidence[evidence_id] for evidence_id in ids]

    async def find_by_sha256(self, run_id: str, hashes: list[str]) -> dict[str, str]:
        return {
            value: self._hashes[(run_id, value)]
            for value in hashes
            if (run_id, value) in self._hashes
        }

    async def link(self, pairs: list[EvidenceQueryLink]) -> None:
        for pair in pairs:
            if pair.evidence_id not in self._evidence or pair.query_id not in self._queries:
                raise StoreLineageError("dangling EvidenceQueryLink")
        self._links.update((pair.evidence_id, pair.query_id) for pair in pairs)

    async def put_evidence_batch(
        self,
        run_id: str,
        evidence: list[Evidence],
        links: list[EvidenceQueryLink],
    ) -> list[str]:
        evidence_state, run_state, hash_state = self._validated_evidence_state(
            run_id, evidence
        )
        link_state = set(self._links)
        linked_ids: set[str] = set()
        for pair in links:
            if pair.evidence_id not in evidence_state:
                raise StoreLineageError("dangling EvidenceQueryLink Evidence")
            query = self._queries.get(pair.query_id)
            if query is None:
                raise StoreLineageError("dangling EvidenceQueryLink Query")
            if (
                run_state[pair.evidence_id] != run_id
                or self._query_runs[pair.query_id] != run_id
            ):
                raise StoreLineageError("EvidenceQueryLink run ownership mismatch")
            linked_ids.add(pair.evidence_id)
            link_state.add((pair.evidence_id, pair.query_id))
        if any(item.evidence_id not in linked_ids for item in evidence):
            raise StoreLineageError("incoming Evidence requires Query lineage")

        self._evidence = evidence_state
        self._evidence_runs = run_state
        self._hashes = hash_state
        self._links = link_state
        return [item.evidence_id for item in evidence]

    def _validated_evidence_state(
        self, run_id: str, items: list[Evidence]
    ) -> tuple[dict[str, Evidence], dict[str, str], dict[tuple[str, str], str]]:
        evidence_state = dict(self._evidence)
        run_state = dict(self._evidence_runs)
        hash_state = dict(self._hashes)
        for item in items:
            validate_json_native(item.normalized_value, path="Evidence.normalized_value")
            old = evidence_state.get(item.evidence_id)
            if old is not None and (old != item or run_state[item.evidence_id] != run_id):
                raise StoreConflictError("evidence_id ownership/payload conflict")
            indexed = hash_state.get((run_id, item.content_sha256))
            if indexed is not None and indexed != item.evidence_id:
                raise StoreConflictError("(run_id, content_sha256) uniqueness violation")
            call = self._provider_calls.get(item.provider_request_id)
            if call is None:
                raise StoreLineageError("dangling Evidence ProviderCall")
            if self._provider_call_runs[item.provider_request_id] != run_id or call.run_id != run_id:
                raise StoreLineageError("Evidence ProviderCall run ownership mismatch")
            if item.source_type != PROVIDER_SOURCE_TYPE[call.provider]:
                raise StoreLineageError("Evidence ProviderCall source lineage mismatch")
            evidence_state[item.evidence_id] = item
            run_state[item.evidence_id] = run_id
            hash_state[(run_id, item.content_sha256)] = item.evidence_id
        return evidence_state, run_state, hash_state

    async def evidence_ids_for_claim(self, claim_id: str) -> list[str]:
        query_ids = {q.query_id for q in self._queries.values() if q.claim_id == claim_id}
        return sorted({eid for eid, qid in self._links if qid in query_ids})

    async def evidence_ids_for_queries(self, query_ids: list[str]) -> list[str]:
        requested = set(query_ids)
        return sorted({eid for eid, qid in self._links if qid in requested})
