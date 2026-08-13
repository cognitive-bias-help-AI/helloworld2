"""P0/S0 검증을 위한 비운영 메모리 EvidenceStore."""

from app.schemas.frozen import Evidence, EvidenceQueryLink, Query


class MemoryEvidenceStore:
    def __init__(self) -> None:
        self._queries: dict[str, Query] = {}
        self._query_runs: dict[str, str] = {}
        self._evidence: dict[str, Evidence] = {}
        self._evidence_runs: dict[str, str] = {}
        self._hashes: dict[tuple[str, str], str] = {}
        self._links: set[tuple[str, str]] = set()

    async def put_queries(self, run_id: str, queries: list[Query]) -> list[str]:
        for query in queries:
            old = self._queries.get(query.query_id)
            if old is not None and (old != query or self._query_runs[query.query_id] != run_id):
                raise ValueError("query_id ownership/payload conflict")
        for query in queries:
            self._queries[query.query_id] = query
            self._query_runs[query.query_id] = run_id
        return [query.query_id for query in queries]

    async def get_queries(self, query_ids: list[str]) -> list[Query]:
        return [self._queries[query_id] for query_id in query_ids]

    async def put_many(self, run_id: str, evs: list[Evidence]) -> list[str]:
        for evidence in evs:
            old = self._evidence.get(evidence.evidence_id)
            if old is not None and (
                old != evidence or self._evidence_runs[evidence.evidence_id] != run_id
            ):
                raise ValueError("evidence_id ownership/payload conflict")
            indexed = self._hashes.get((run_id, evidence.content_sha256))
            if indexed is not None and indexed != evidence.evidence_id:
                raise ValueError("(run_id, content_sha256) uniqueness violation")
        for evidence in evs:
            self._evidence[evidence.evidence_id] = evidence
            self._evidence_runs[evidence.evidence_id] = run_id
            self._hashes[(run_id, evidence.content_sha256)] = evidence.evidence_id
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
                raise ValueError("dangling EvidenceQueryLink")
        self._links.update((pair.evidence_id, pair.query_id) for pair in pairs)

    async def evidence_ids_for_claim(self, claim_id: str) -> list[str]:
        query_ids = {q.query_id for q in self._queries.values() if q.claim_id == claim_id}
        return sorted({eid for eid, qid in self._links if qid in query_ids})

    async def evidence_ids_for_queries(self, query_ids: list[str]) -> list[str]:
        requested = set(query_ids)
        return sorted({eid for eid, qid in self._links if qid in requested})
