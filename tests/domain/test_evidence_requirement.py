from app.domain.evidence_requirement import (
    EVIDENCE_REQUIREMENT_REGISTRY,
    CoverageLevel,
    EvidenceCategory,
    EvidenceRole,
)


def test_business_strategy_metadata_only_disclosure_is_not_primary():
    rule = EVIDENCE_REQUIREMENT_REGISTRY[EvidenceCategory.BUSINESS_STRATEGY]
    assert rule.coverage is CoverageLevel.SOURCE_LIMITED
    assert all(source.role is not EvidenceRole.PRIMARY for source in rule.sources)


def test_company_guidance_metadata_only_disclosure_is_not_primary():
    rule = EVIDENCE_REQUIREMENT_REGISTRY[EvidenceCategory.COMPANY_GUIDANCE]
    assert rule.coverage is CoverageLevel.SOURCE_LIMITED
    assert all(source.role is not EvidenceRole.PRIMARY for source in rule.sources)


def test_capital_shareholder_action_retains_title_level_disclosure_primary():
    rule = EVIDENCE_REQUIREMENT_REGISTRY[EvidenceCategory.CAPITAL_SHAREHOLDER_ACTION]
    assert any(
        source.provider == "dart"
        and source.endpoint == "disclosure_list"
        and source.role is EvidenceRole.PRIMARY
        for source in rule.sources
    )
