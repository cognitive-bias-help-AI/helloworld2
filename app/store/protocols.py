"""외부 Evidence와 내부 판단 산출물 저장소 계약."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.ask_history import AskRecord
from app.domain.resume_source import ResumeSemanticSource
from app.domain.slot_context import SlotValueObservation
from app.schemas.frozen import (
    Claim,
    ClaimEvaluation,
    ClaimEvidence,
    Evidence,
    EvidenceQueryLink,
    Finding,
    ProviderCall,
    Query,
)


@runtime_checkable
class EvidenceStore(Protocol):
    async def put_queries(self, run_id: str, queries: list[Query]) -> list[str]: ...

    async def get_queries(self, query_ids: list[str]) -> list[Query]: ...

    async def put_provider_calls(
        self, run_id: str, calls: list[ProviderCall]
    ) -> list[str]: ...

    async def get_provider_calls(
        self, provider_request_ids: list[str]
    ) -> list[ProviderCall]: ...

    async def provider_calls_for_query(self, query_id: str) -> list[ProviderCall]: ...

    async def put_many(self, run_id: str, evs: list[Evidence]) -> list[str]: ...

    async def get_many(self, ids: list[str]) -> list[Evidence]: ...

    async def find_by_sha256(self, run_id: str, hashes: list[str]) -> dict[str, str]: ...

    async def link(self, pairs: list[EvidenceQueryLink]) -> None: ...

    async def put_evidence_batch(
        self,
        run_id: str,
        evidence: list[Evidence],
        links: list[EvidenceQueryLink],
    ) -> list[str]: ...

    async def evidence_ids_for_claim(self, claim_id: str) -> list[str]: ...

    async def evidence_ids_for_queries(self, query_ids: list[str]) -> list[str]: ...


@runtime_checkable
class ReviewStore(Protocol):
    async def put_input(self, run_id: str, body: dict) -> str: ...

    async def get_input(self, input_id: str) -> dict: ...

    async def put_claims(self, run_id: str, items: list[Claim]) -> list[str]: ...

    async def get_claims(self, claim_ids: list[str]) -> list[Claim]: ...

    async def put_claim_evidence(
        self, run_id: str, items: list[ClaimEvidence]
    ) -> list[str]: ...

    async def get_claim_evidence(self, run_id: str, claim_id: str) -> list[ClaimEvidence]: ...

    async def put_claim_evaluations(
        self, run_id: str, items: list[ClaimEvaluation]
    ) -> list[str]: ...

    async def get_claim_evaluations(self, ids: list[str]) -> list[ClaimEvaluation]: ...

    async def put_findings(self, run_id: str, items: list[Finding]) -> list[str]: ...

    async def get_findings(self, ids: list[str]) -> list[Finding]: ...

    async def put_report(self, run_id: str, body: dict) -> str: ...

    async def get_report(self, report_id: str) -> dict | None: ...

    async def put_slot_observations(
        self, run_id: str, items: list[SlotValueObservation]
    ) -> list[str]: ...

    async def get_slot_observations(
        self, run_id: str
    ) -> list[SlotValueObservation]: ...

    async def put_semantic_batch(
        self,
        run_id: str,
        observations: list[SlotValueObservation],
        claims: list[Claim],
    ) -> tuple[list[str], list[str]]: ...

    async def put_resume_sources(
        self, run_id: str, items: list[ResumeSemanticSource]
    ) -> list[str]: ...

    async def get_resume_sources(self, run_id: str) -> list[ResumeSemanticSource]: ...

    async def put_ask_records(
        self, run_id: str, items: list[AskRecord]
    ) -> list[str]: ...

    async def get_ask_records(self, run_id: str) -> list[AskRecord]: ...
