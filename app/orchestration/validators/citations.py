from app.schemas.frozen import CitationRef, Evidence, ReasonCode


class CitationContractViolation(ValueError):
    reason_code = ReasonCode.CONTRACT_VIOLATION


def validate_citations(citations: list[CitationRef], evidence_by_id: dict[str, Evidence]) -> None:
    for citation in citations:
        evidence = evidence_by_id.get(citation.evidence_id)
        if evidence is None:
            raise CitationContractViolation("citation references unknown evidence")
        if citation.span not in evidence.raw_span:
            raise CitationContractViolation("citation span is not an exact Evidence.raw_span substring")
