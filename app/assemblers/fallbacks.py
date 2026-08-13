from datetime import datetime

from app.schemas.frozen import (
    ClaimEvaluation,
    ClaimEvidence,
    NumericCheck,
    ReasonCode,
)


def assemble_unknown_claim_evidence_fallback(
    claim_id: str,
    packet_evidence_ids: list[str],
    query_id_by_evidence: dict[str, str],
) -> list[ClaimEvidence]:
    return [
        ClaimEvidence(
            claim_id=claim_id,
            evidence_id=evidence_id,
            stance="unknown",
            stance_source="rule",
            confidence=None,
            query_id=query_id_by_evidence[evidence_id],
        )
        for evidence_id in sorted(packet_evidence_ids)
    ]


def assemble_unverifiable_evaluation_fallback(
    *,
    claim_id: str,
    packet_evidence_ids: list[str],
    numeric_checks: list[NumericCheck],
    claim_evaluation_id: str,
    created_at: datetime,
) -> ClaimEvaluation:
    return ClaimEvaluation(
        claim_evaluation_id=claim_evaluation_id,
        claim_id=claim_id,
        citations=[],
        support_evidence_ids=[],
        oppose_evidence_ids=[],
        neutral_evidence_ids=[],
        unknown_evidence_ids=sorted(packet_evidence_ids),
        numeric_checks=sorted(numeric_checks, key=lambda item: (item.evidence_id, item.metric)),
        verdict="unverifiable",
        missing_dimensions=[],
        uncertainty_codes=[ReasonCode.COVERAGE_TRUNCATED],
        created_at=created_at,
    )


def omit_invalid_findings_fallback() -> list:
    return []
