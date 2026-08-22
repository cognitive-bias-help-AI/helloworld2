"""EvidenceDraft를 canonical Evidence로 승격하는 결정론적 경계."""

from datetime import datetime
from hashlib import sha256

from app.schemas.frozen import (
    PROVIDER_SOURCE_TYPE,
    Evidence,
    EvidenceDraft,
    EvidenceQueryLink,
    ProviderCall,
    Query,
    ReasonCode,
)
from app.store.protocols import EvidenceStore


class ContractViolation(ValueError):
    reason_code = ReasonCode.CONTRACT_VIOLATION


def content_sha256(draft: EvidenceDraft) -> str:
    normalized_span = " ".join(draft.raw_span.split())
    return sha256(f"{normalized_span}|{draft.source_ref}".encode()).hexdigest()


def _evidence_id(run_id: str, digest: str) -> str:
    return "01" + sha256(f"{run_id}|{digest}".encode()).hexdigest().upper()[:24]


async def assemble_evidence(
    drafts: list[EvidenceDraft],
    q: Query,
    call: ProviderCall,
    as_of: datetime,
    run_id: str,
    fetched_at: datetime,
    store: EvidenceStore,
) -> tuple[list[Evidence], int]:
    if as_of.utcoffset() is None or fetched_at.utcoffset() is None:
        raise ContractViolation("as_of and fetched_at must be timezone-aware")
    if (
        call.run_id != run_id
        or call.query_id != q.query_id
        or call.provider != q.provider
        or call.endpoint != q.endpoint
    ):
        raise ContractViolation("ProviderCall lineage mismatch")
    expected = PROVIDER_SOURCE_TYPE[q.provider]
    if any(draft.source_type != expected for draft in drafts):
        raise ContractViolation("EvidenceDraft source_type mismatch")

    grouped: dict[str, EvidenceDraft] = {}
    batch_duplicates = 0
    for draft in drafts:
        digest = content_sha256(draft)
        previous = grouped.get(digest)
        if previous is not None:
            if previous != draft:
                raise ContractViolation("same hash has conflicting EvidenceDraft payloads")
            batch_duplicates += 1
        else:
            grouped[digest] = draft

    existing = await store.find_by_sha256(run_id, list(grouped))
    new_evidence: list[Evidence] = []
    ids_by_hash = dict(existing)
    for digest, draft in grouped.items():
        if digest in existing:
            continue
        evidence = Evidence(
            evidence_id=_evidence_id(run_id, digest),
            **draft.model_dump(),
            fetched_at=fetched_at,
            content_sha256=digest,
            provider_request_id=call.provider_request_id,
            as_of=as_of,
        )
        new_evidence.append(evidence)
        ids_by_hash[digest] = evidence.evidence_id
    await store.put_evidence_batch(
        run_id,
        new_evidence,
        [
            EvidenceQueryLink(evidence_id=ids_by_hash[digest], query_id=q.query_id)
            for digest in grouped
        ],
    )
    return new_evidence, batch_duplicates + len(existing)
