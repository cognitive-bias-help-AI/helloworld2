"""P0/S0 검증을 위한 비운영 메모리 ReviewStore."""

import json
from copy import deepcopy
from hashlib import sha256

from app.domain.ask_history import AskRecord, expected_ask_id
from app.domain.resume_source import ResumeSemanticSource, expected_resume_source_id
from app.domain.slot_context import (
    SlotValueObservation,
    expected_observation_id,
    observation_content_sha256,
)
from app.schemas.frozen import Claim, ClaimEvaluation, ClaimEvidence, Finding


def _body_id(kind: str, run_id: str) -> str:
    return "01" + sha256(f"{kind}|{run_id}".encode()).hexdigest().upper()[:24]


def _canonical_body(body: dict) -> str:
    return json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class MemoryReviewStore:
    def __init__(self) -> None:
        self._inputs: dict[str, dict] = {}
        self._input_runs: dict[str, str] = {}
        self._input_bodies: dict[str, str] = {}
        self._claims: dict[str, Claim] = {}
        self._claim_runs: dict[str, str] = {}
        self._claim_evidence: dict[tuple[str, str, str], ClaimEvidence] = {}
        self._evaluations: dict[str, ClaimEvaluation] = {}
        self._evaluation_runs: dict[str, str] = {}
        self._current_evaluations: dict[tuple[str, str], str] = {}
        self._findings: dict[str, Finding] = {}
        self._finding_runs: dict[str, str] = {}
        self._reports: dict[str, dict] = {}
        self._report_runs: dict[str, str] = {}
        self._slot_observations: dict[str, SlotValueObservation] = {}
        self._slot_observation_runs: dict[str, str] = {}
        self._slot_observation_hashes: dict[tuple[str, str], str] = {}
        self._slot_observation_order: dict[str, list[str]] = {}
        self._resume_sources: dict[str, ResumeSemanticSource] = {}
        self._resume_source_runs: dict[str, str] = {}
        self._resume_source_order: dict[str, list[str]] = {}
        self._ask_records: dict[str, AskRecord] = {}
        self._ask_record_runs: dict[str, str] = {}
        self._ask_record_order: dict[str, list[str]] = {}

    async def put_input(self, run_id: str, body: dict) -> str:
        item_id = _body_id("input", run_id)
        canonical = _canonical_body(body)
        old = self._input_bodies.get(item_id)
        if old is not None and (
            old != canonical or self._input_runs[item_id] != run_id
        ):
            raise ValueError("input run/payload conflict")
        if old is not None:
            return item_id
        self._inputs[item_id] = deepcopy(body)
        self._input_runs[item_id] = run_id
        self._input_bodies[item_id] = canonical
        return item_id

    async def get_input(self, input_id: str) -> dict:
        return deepcopy(self._inputs[input_id])

    async def put_claims(self, run_id: str, items: list[Claim]) -> list[str]:
        for item in items:
            old = self._claims.get(item.claim_id)
            if old is not None and (old != item or self._claim_runs[item.claim_id] != run_id):
                raise ValueError("claim_id ownership/payload conflict")
        for item in items:
            self._claims[item.claim_id], self._claim_runs[item.claim_id] = item, run_id
        return [item.claim_id for item in items]

    async def get_claims(self, claim_ids: list[str]) -> list[Claim]:
        return [self._claims[item_id] for item_id in claim_ids]

    async def put_claim_evidence(self, run_id: str, items: list[ClaimEvidence]) -> list[str]:
        for item in items:
            self._claim_evidence[(run_id, item.claim_id, item.evidence_id)] = item
        return [item.key for item in items]

    async def get_claim_evidence(self, run_id: str, claim_id: str) -> list[ClaimEvidence]:
        return [
            item
            for (owner, cid, _), item in sorted(self._claim_evidence.items())
            if owner == run_id and cid == claim_id
        ]

    async def put_claim_evaluations(self, run_id: str, items: list[ClaimEvaluation]) -> list[str]:
        for item in items:
            key = (run_id, item.claim_id)
            previous = self._current_evaluations.get(key)
            if previous is not None:
                self._evaluations.pop(previous, None)
                self._evaluation_runs.pop(previous, None)
            self._evaluations[item.claim_evaluation_id] = item
            self._evaluation_runs[item.claim_evaluation_id] = run_id
            self._current_evaluations[key] = item.claim_evaluation_id
        return [item.claim_evaluation_id for item in items]

    async def get_claim_evaluations(self, ids: list[str]) -> list[ClaimEvaluation]:
        return [self._evaluations[item_id] for item_id in ids if item_id in self._evaluations]

    async def put_findings(self, run_id: str, items: list[Finding]) -> list[str]:
        for item in items:
            old = self._findings.get(item.finding_id)
            if old is not None and (old != item or self._finding_runs[item.finding_id] != run_id):
                raise ValueError("finding_id ownership/payload conflict")
            self._findings[item.finding_id], self._finding_runs[item.finding_id] = item, run_id
        return [item.finding_id for item in items]

    async def get_findings(self, ids: list[str]) -> list[Finding]:
        return [self._findings[item_id] for item_id in ids]

    async def put_report(self, run_id: str, body: dict) -> str:
        item_id = _body_id("report", run_id)
        self._reports[item_id] = dict(body)
        self._report_runs[item_id] = run_id
        return item_id

    async def get_report(self, report_id: str) -> dict | None:
        body = self._reports.get(report_id)
        return None if body is None else dict(body)

    async def put_slot_observations(
        self, run_id: str, items: list[SlotValueObservation]
    ) -> list[str]:
        batch_by_id: dict[str, SlotValueObservation] = {}
        batch_by_hash: dict[str, str] = {}
        digests: dict[str, str] = {}
        for item in items:
            old = self._slot_observations.get(item.observation_id)
            if old is not None and (
                old != item
                or self._slot_observation_runs[item.observation_id] != run_id
            ):
                raise ValueError("observation_id ownership/payload conflict")
            previous = batch_by_id.get(item.observation_id)
            if previous is not None and previous != item:
                raise ValueError("observation_id ownership/payload conflict")

            digest = observation_content_sha256(item)
            if item.observation_id != expected_observation_id(run_id, item):
                raise ValueError("slot observation content hash/ID conflict")
            indexed = self._slot_observation_hashes.get((run_id, digest))
            if indexed is not None and indexed != item.observation_id:
                raise ValueError("slot observation content hash/ID conflict")
            batch_indexed = batch_by_hash.get(digest)
            if batch_indexed is not None and batch_indexed != item.observation_id:
                raise ValueError("slot observation content hash/ID conflict")
            batch_by_id[item.observation_id] = item
            batch_by_hash[digest] = item.observation_id
            digests[item.observation_id] = digest

        order = self._slot_observation_order.setdefault(run_id, [])
        for observation_id, item in batch_by_id.items():
            if observation_id in self._slot_observations:
                continue
            self._slot_observations[observation_id] = item
            self._slot_observation_runs[observation_id] = run_id
            self._slot_observation_hashes[(run_id, digests[observation_id])] = (
                observation_id
            )
            order.append(observation_id)
        return [item.observation_id for item in items]

    async def get_slot_observations(
        self, run_id: str
    ) -> list[SlotValueObservation]:
        return [
            self._slot_observations[item_id]
            for item_id in self._slot_observation_order.get(run_id, [])
        ]

    async def put_semantic_batch(
        self,
        run_id: str,
        observations: list[SlotValueObservation],
        claims: list[Claim],
    ) -> tuple[list[str], list[str]]:
        """Persist one semantic assembly result only after both sides validate."""

        observations_by_id: dict[str, SlotValueObservation] = {}
        observation_digests: dict[str, str] = {}
        observation_hashes: dict[str, str] = {}
        for item in observations:
            old = self._slot_observations.get(item.observation_id)
            if old is not None and (
                old != item
                or self._slot_observation_runs[item.observation_id] != run_id
            ):
                raise ValueError("observation_id ownership/payload conflict")
            previous = observations_by_id.get(item.observation_id)
            if previous is not None and previous != item:
                raise ValueError("observation_id ownership/payload conflict")

            digest = observation_content_sha256(item)
            if item.observation_id != expected_observation_id(run_id, item):
                raise ValueError("slot observation content hash/ID conflict")
            indexed = self._slot_observation_hashes.get((run_id, digest))
            if indexed is not None and indexed != item.observation_id:
                raise ValueError("slot observation content hash/ID conflict")
            batch_indexed = observation_hashes.get(digest)
            if batch_indexed is not None and batch_indexed != item.observation_id:
                raise ValueError("slot observation content hash/ID conflict")
            observations_by_id[item.observation_id] = item
            observation_digests[item.observation_id] = digest
            observation_hashes[digest] = item.observation_id

        claims_by_id: dict[str, Claim] = {}
        for item in claims:
            old = self._claims.get(item.claim_id)
            if old is not None and (
                old != item or self._claim_runs[item.claim_id] != run_id
            ):
                raise ValueError("claim_id ownership/payload conflict")
            previous = claims_by_id.get(item.claim_id)
            if previous is not None and previous != item:
                raise ValueError("claim_id ownership/payload conflict")
            claims_by_id[item.claim_id] = item

        observation_order = self._slot_observation_order.setdefault(run_id, [])
        for observation_id, item in observations_by_id.items():
            if observation_id in self._slot_observations:
                continue
            self._slot_observations[observation_id] = item
            self._slot_observation_runs[observation_id] = run_id
            self._slot_observation_hashes[(run_id, observation_digests[observation_id])] = (
                observation_id
            )
            observation_order.append(observation_id)
        for claim_id, item in claims_by_id.items():
            if claim_id in self._claims:
                continue
            self._claims[claim_id] = item
            self._claim_runs[claim_id] = run_id

        return (
            [item.observation_id for item in observations],
            [item.claim_id for item in claims],
        )

    async def put_resume_sources(
        self, run_id: str, items: list[ResumeSemanticSource]
    ) -> list[str]:
        batch: dict[str, ResumeSemanticSource] = {}
        for item in items:
            if item.source_id != expected_resume_source_id(run_id, item.resume_key):
                raise ValueError("resume source ownership/payload conflict")
            old = self._resume_sources.get(item.source_id)
            if old is not None and (
                old != item or self._resume_source_runs[item.source_id] != run_id
            ):
                raise ValueError("resume source ownership/payload conflict")
            previous = batch.get(item.source_id)
            if previous is not None and previous != item:
                raise ValueError("resume source ownership/payload conflict")
            batch[item.source_id] = item

        order = self._resume_source_order.setdefault(run_id, [])
        for source_id, item in batch.items():
            if source_id in self._resume_sources:
                continue
            self._resume_sources[source_id] = item
            self._resume_source_runs[source_id] = run_id
            order.append(source_id)
        return [item.source_id for item in items]

    async def get_resume_sources(self, run_id: str) -> list[ResumeSemanticSource]:
        return [
            self._resume_sources[item_id]
            for item_id in self._resume_source_order.get(run_id, [])
        ]

    async def put_ask_records(
        self, run_id: str, items: list[AskRecord]
    ) -> list[str]:
        batch: dict[str, AskRecord] = {}
        for item in items:
            if item.ask_id != expected_ask_id(run_id, item.ask_key):
                raise ValueError("ask record ownership/payload conflict")
            old = self._ask_records.get(item.ask_id)
            if old is not None and (
                old != item or self._ask_record_runs[item.ask_id] != run_id
            ):
                raise ValueError("ask record ownership/payload conflict")
            previous = batch.get(item.ask_id)
            if previous is not None and previous != item:
                raise ValueError("ask record ownership/payload conflict")
            batch[item.ask_id] = item

        order = self._ask_record_order.setdefault(run_id, [])
        for ask_id, item in batch.items():
            if ask_id in self._ask_records:
                continue
            self._ask_records[ask_id] = item
            self._ask_record_runs[ask_id] = run_id
            order.append(ask_id)
        return [item.ask_id for item in items]

    async def get_ask_records(self, run_id: str) -> list[AskRecord]:
        return [
            self._ask_records[item_id]
            for item_id in self._ask_record_order.get(run_id, [])
        ]
