"""Deterministic grounding boundary for LLM EvidenceIntentDraft output."""

from app.orchestration.drafts import EvidenceIntentDraft


def _grounded(value: str | None, claim_text: str) -> bool:
    return value is None or value.casefold() in claim_text.casefold()


def validate_grounded_intent(draft: EvidenceIntentDraft, claim_text: str) -> EvidenceIntentDraft:
    for requirement in draft.requirements:
        for field in ("direction", "actor", "comparison_target", "temporal_expression"):
            if not _grounded(getattr(requirement, field), claim_text):
                raise ValueError(f"ungrounded evidence intent field: {field}")
        if any(not _grounded(term, claim_text) for term in requirement.topic_terms):
            raise ValueError("ungrounded evidence intent field: topic_terms")
    return draft


__all__ = ["validate_grounded_intent"]
