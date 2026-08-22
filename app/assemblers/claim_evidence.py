"""n7 ClaimStanceDraft assembler."""

from app.assemblers.errors import AssemblyError
from app.schemas.frozen import ClaimEvidence, ClaimStanceDraft


def assemble_claim_evidence(
    draft: ClaimStanceDraft,
    claim_id: str,
    packet_evidence_ids: list[str],
    query_id_by_evidence: dict[str, str | None],
) -> list[ClaimEvidence]:
    if len(packet_evidence_ids) != len(set(packet_evidence_ids)):
        raise AssemblyError("duplicate_reference", retryable=False)
    packet = set(packet_evidence_ids)
    if set(query_id_by_evidence) != packet:
        raise AssemblyError("contract_violation", retryable=False)
    draft_ids = [item.evidence_id for item in draft.stances]
    if len(draft_ids) != len(set(draft_ids)):
        raise AssemblyError("duplicate_reference", retryable=True)
    unknown = set(draft_ids) - packet
    if unknown:
        raise AssemblyError("unknown_reference", retryable=True)
    if set(draft_ids) != packet:
        raise AssemblyError("coverage_mismatch", retryable=True)
    return [
        ClaimEvidence(
            claim_id=claim_id,
            evidence_id=item.evidence_id,
            stance=item.stance,
            stance_source="llm",
            confidence=item.confidence,
            query_id=query_id_by_evidence[item.evidence_id],
        )
        for item in sorted(draft.stances, key=lambda value: value.evidence_id)
    ]
