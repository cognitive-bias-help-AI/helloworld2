"""n8 ClaimEvaluationDraft assembler."""

from datetime import datetime

from app.assemblers.errors import AssemblyError
from app.schemas.frozen import ClaimEvaluation, ClaimEvaluationDraft, NumericCheck


def assemble_claim_evaluation(
    draft: ClaimEvaluationDraft,
    claim_id: str,
    packet_evidence_ids: list[str],
    numeric_checks: list[NumericCheck],
    claim_evaluation_id: str,
    created_at: datetime,
    *,
    primary_evidence_ids: set[str] | None = None,
) -> ClaimEvaluation:
    if len(packet_evidence_ids) != len(set(packet_evidence_ids)):
        raise AssemblyError("duplicate_reference", retryable=False)
    packet = set(packet_evidence_ids)
    buckets = [
        *draft.support_evidence_ids, *draft.oppose_evidence_ids,
        *draft.neutral_evidence_ids, *draft.unknown_evidence_ids,
    ]
    if len(buckets) != len(set(buckets)):
        raise AssemblyError("duplicate_reference", retryable=True)
    if set(buckets) - packet:
        raise AssemblyError("unknown_reference", retryable=True)
    if set(buckets) != packet:
        raise AssemblyError("coverage_mismatch", retryable=True)
    if any(citation.evidence_id not in set(buckets) for citation in draft.citations):
        raise AssemblyError("unknown_reference", retryable=True)
    if any(check.evidence_id not in packet for check in numeric_checks):
        raise AssemblyError("unknown_reference", retryable=False)
    if primary_evidence_ids is not None:
        if not primary_evidence_ids <= packet:
            raise AssemblyError("unknown_reference", retryable=False)
        if draft.verdict in {"support", "partial_support"} and not (
            set(draft.support_evidence_ids) & primary_evidence_ids
        ):
            raise AssemblyError("coverage_mismatch", retryable=True)
        if draft.verdict == "contradicted" and not (
            set(draft.oppose_evidence_ids) & primary_evidence_ids
        ):
            raise AssemblyError("coverage_mismatch", retryable=True)
    citations = sorted(draft.citations, key=lambda item: (item.evidence_id, item.span))
    checks = sorted(
        numeric_checks,
        key=lambda item: (item.evidence_id, item.metric, item.period or "", item.claimed),
    )
    return ClaimEvaluation(
        claim_evaluation_id=claim_evaluation_id,
        claim_id=claim_id,
        citations=citations,
        support_evidence_ids=sorted(draft.support_evidence_ids),
        oppose_evidence_ids=sorted(draft.oppose_evidence_ids),
        neutral_evidence_ids=sorted(draft.neutral_evidence_ids),
        unknown_evidence_ids=sorted(draft.unknown_evidence_ids),
        numeric_checks=checks,
        verdict=draft.verdict,
        missing_dimensions=sorted(draft.missing_dimensions),
        uncertainty_codes=sorted(draft.uncertainty_codes, key=lambda item: item.value),
        created_at=created_at,
    )
