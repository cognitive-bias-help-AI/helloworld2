"""n9 FindingDraft assembler."""

from datetime import datetime

from app.assemblers.errors import AssemblyError
from app.orchestration.drafts import FindingDraft
from app.schemas.frozen import ClaimEvaluation, Finding


def _key(draft: FindingDraft):
    citations = tuple(sorted((item.evidence_id, item.span) for item in draft.citations))
    return (draft.slot_id, draft.kind, draft.claim_evaluation_id or "", citations)


def assemble_findings(
    drafts: list[FindingDraft],
    evaluations: list[ClaimEvaluation],
    finding_ids: list[str],
    created_at: datetime,
) -> list[Finding]:
    if len(finding_ids) != len(drafts):
        raise AssemblyError("contract_violation", retryable=False)
    if len(finding_ids) != len(set(finding_ids)):
        raise AssemblyError("duplicate_reference", retryable=False)
    evaluation_ids = [item.claim_evaluation_id for item in evaluations]
    if len(evaluation_ids) != len(set(evaluation_ids)):
        raise AssemblyError("duplicate_reference", retryable=False)
    by_id = {item.claim_evaluation_id: item for item in evaluations}
    keys = [_key(draft) for draft in drafts]
    if len(keys) != len(set(keys)):
        raise AssemblyError("duplicate_reference", retryable=True)
    for draft in drafts:
        if draft.kind == "mismatch" and not draft.citations:
            raise AssemblyError("contract_violation", retryable=True)
        if draft.claim_evaluation_id is None:
            if draft.citations:
                raise AssemblyError("unknown_reference", retryable=True)
            continue
        evaluation = by_id.get(draft.claim_evaluation_id)
        if evaluation is None:
            raise AssemblyError("unknown_reference", retryable=True)
        allowed = {
            *evaluation.support_evidence_ids,
            *evaluation.oppose_evidence_ids,
            *evaluation.neutral_evidence_ids,
            *evaluation.unknown_evidence_ids,
            *(item.evidence_id for item in evaluation.numeric_checks),
        }
        if any(citation.evidence_id not in allowed for citation in draft.citations):
            raise AssemblyError("unknown_reference", retryable=True)
    ordered = sorted(drafts, key=_key)
    return [
        Finding(
            finding_id=finding_id,
            slot_id=draft.slot_id,
            kind=draft.kind,
            citations=sorted(draft.citations, key=lambda item: (item.evidence_id, item.span)),
            claim_evaluation_id=draft.claim_evaluation_id,
            created_at=created_at,
        )
        for draft, finding_id in zip(ordered, finding_ids, strict=True)
    ]
