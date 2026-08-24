from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.assemblers.claim_evaluation import assemble_claim_evaluation
from app.assemblers.errors import AssemblyError
from app.domain.evidence_requirement import (
    EVIDENCE_REQUIREMENT_REGISTRY,
    CoverageLevel,
    EvidenceCategory,
    EvidenceRole,
)
from app.orchestration.drafts import EvidenceIntentDraft, EvidenceRequirementDraft
from app.orchestration.evidence_intent import validate_grounded_intent
from app.orchestration.evidence_planning import (
    RequirementStatus,
    plan_baseline_queries,
    plan_hybrid_claim,
)
from app.schemas.frozen import CitationRef, Claim, ClaimEvaluationDraft, SourceTrace

NOW = datetime(2026, 8, 24, tzinfo=UTC)


def uid(n: int) -> str:
    return f"01ARZ3NDEKTSV4RRFFQ69G{n:04d}"


def claim(text: str) -> Claim:
    return Claim(
        claim_id=uid(1),
        slot_id=4,
        user_text_span=text,
        span_offset=(0, len(text)),
        normalized_proposition=text,
        verifiable=True,
        origin=SourceTrace.SURVEY,
        created_at=NOW,
    )


def intent(category: EvidenceCategory, **kwargs) -> EvidenceIntentDraft:
    return EvidenceIntentDraft(
        requirements=[EvidenceRequirementDraft(category=category, **kwargs)]
    )


def test_registry_has_exactly_the_fixed_18_categories_and_supported_endpoints_only():
    assert len(EvidenceCategory) == 18
    assert set(EVIDENCE_REQUIREMENT_REGISTRY) == set(EvidenceCategory)
    allowed = {
        "dart": {"financial_statement", "financial_indicator", "disclosure_list"},
        "kiwoom": {"current_quote", "daily_price_history", "investor_flow"},
        "naver": {"news_search"},
    }
    for entry in EVIDENCE_REQUIREMENT_REGISTRY.values():
        for source in entry.sources:
            assert source.endpoint in allowed[source.provider]


def test_source_limited_registry_entries_do_not_claim_primary_authority():
    for category in (
        EvidenceCategory.VALUATION,
        EvidenceCategory.TRADING_VOLUME_LIQUIDITY,
        EvidenceCategory.INDUSTRY_CONDITION,
        EvidenceCategory.DEMAND_SUPPLY,
        EvidenceCategory.COMPETITIVE_POSITION,
        EvidenceCategory.MACRO_REGULATORY_ENVIRONMENT,
    ):
        entry = EVIDENCE_REQUIREMENT_REGISTRY[category]
        assert entry.coverage is CoverageLevel.SOURCE_LIMITED
        assert all(source.role is not EvidenceRole.PRIMARY for source in entry.sources)


def test_evidence_intent_draft_is_bounded_and_mlapi_array_schema_uses_items():
    schema = EvidenceIntentDraft.model_json_schema()
    requirements = schema["properties"]["requirements"]
    assert requirements["maxItems"] == 3
    assert "items" in requirements
    assert "prefixItems" not in requirements
    with pytest.raises(ValidationError):
        EvidenceIntentDraft(
            requirements=[
                EvidenceRequirementDraft(category=EvidenceCategory.NEWS_EVENT)
                for _ in range(4)
            ]
        )


@pytest.mark.parametrize(
    ("text", "proposal"),
    [
        (
            "영업이익이 증가했다",
            {"category": EvidenceCategory.FINANCIAL_PERFORMANCE, "business_year": "2025"},
        ),
        (
            "HBM 경쟁력이 좋다",
            {
                "category": EvidenceCategory.COMPETITIVE_POSITION,
                "comparison_target": "SK하이닉스",
            },
        ),
        (
            "규제가 악재다",
            {
                "category": EvidenceCategory.MACRO_REGULATORY_ENVIRONMENT,
                "jurisdiction": "미국",
            },
        ),
        (
            "영업이익을 확인한다",
            {
                "category": EvidenceCategory.FINANCIAL_PERFORMANCE,
                "direction": "증가",
            },
        ),
    ],
)
def test_ungrounded_or_forbidden_llm_facts_are_rejected(text, proposal):
    with pytest.raises((ValidationError, ValueError)):
        draft = EvidenceIntentDraft(requirements=[EvidenceRequirementDraft(**proposal)])
        validate_grounded_intent(draft, text)


def test_explicit_comparison_target_is_preserved():
    draft = intent(
        EvidenceCategory.COMPETITIVE_POSITION,
        topic_terms=["HBM 경쟁력"],
        comparison_target="SK하이닉스",
    )
    assert validate_grounded_intent(
        draft, "SK하이닉스보다 HBM 경쟁력이 높다"
    ).requirements[0].comparison_target == "SK하이닉스"


def test_baseline_always_plans_dart_kiwoom_and_naver_as_stock_context():
    queries = plan_baseline_queries(
        stock_code="005930",
        stock_name="삼성전자",
        as_of=NOW,
        id_factory=iter([uid(10), uid(11), uid(12)]).__next__,
        clock=lambda: NOW,
    )
    assert {(q.provider, q.endpoint) for q in queries} == {
        ("dart", "disclosure_list"),
        ("kiwoom", "daily_price_history"),
        ("naver", "news_search"),
    }
    assert all(q.scope == "stock" and q.claim_id is None and q.intent == "context" for q in queries)


def test_financial_requirement_plans_primary_and_corroborative_without_inventing_policy_facts():
    result = plan_hybrid_claim(
        claim("2025년 영업이익이 증가했다"),
        intent(EvidenceCategory.FINANCIAL_PERFORMANCE, topic_terms=["영업이익"]),
        stock_code="005930",
        stock_name="삼성전자",
        as_of=NOW,
        id_factory=iter([uid(20), uid(21)]).__next__,
        clock=lambda: NOW,
    )
    primary = next(item for item in result.requirements if item.role is EvidenceRole.PRIMARY)
    assert primary.status is RequirementStatus.READY
    assert primary.query.provider == "dart"
    assert primary.query.params["bsns_year"] == "2025"
    assert primary.query.params["reprt_code"] == "11011"
    assert primary.query.params["fs_div"] == "CFS"
    assert any(item.role is EvidenceRole.CORROBORATIVE for item in result.requirements)


def test_missing_financial_year_has_no_primary_query_even_when_corroborative_is_ready():
    result = plan_hybrid_claim(
        claim("영업이익이 증가했다"),
        intent(EvidenceCategory.FINANCIAL_PERFORMANCE, topic_terms=["영업이익"]),
        stock_code="005930",
        stock_name="삼성전자",
        as_of=NOW,
        id_factory=iter([uid(30)]).__next__,
        clock=lambda: NOW,
    )
    primary = next(item for item in result.requirements if item.role is EvidenceRole.PRIMARY)
    assert primary.status is RequirementStatus.MISSING_USER_FACT
    assert primary.missing == ("bsns_year",)
    assert primary.query is None
    assert result.has_executable_primary is False


def test_demand_supply_is_source_limited_and_only_corroborative_query_is_created():
    result = plan_hybrid_claim(
        claim("HBM 공급이 수요를 못 따라간다"),
        intent(EvidenceCategory.DEMAND_SUPPLY, topic_terms=["HBM 공급", "수요"]),
        stock_code="005930",
        stock_name="삼성전자",
        as_of=NOW,
        id_factory=iter([uid(40)]).__next__,
        clock=lambda: NOW,
    )
    assert result.coverage is CoverageLevel.SOURCE_LIMITED
    assert result.has_executable_primary is False
    assert {q.intent for q in result.queries} == {"context"}


def test_corroborative_only_evidence_cannot_assemble_support_verdict():
    evidence_id = uid(50)
    proposal = ClaimEvaluationDraft(
        citations=[CitationRef(evidence_id=evidence_id, span="기사 근거")],
        support_evidence_ids=[evidence_id],
        oppose_evidence_ids=[],
        unknown_evidence_ids=[],
        verdict="support",
        missing_dimensions=[],
        uncertainty_codes=[],
    )
    with pytest.raises(AssemblyError):
        assemble_claim_evaluation(
            proposal,
            uid(1),
            [evidence_id],
            [],
            uid(51),
            NOW,
            primary_evidence_ids=set(),
        )


def test_primary_support_evidence_can_assemble_support_verdict():
    evidence_id = uid(52)
    proposal = ClaimEvaluationDraft(
        citations=[CitationRef(evidence_id=evidence_id, span="공식 근거")],
        support_evidence_ids=[evidence_id],
        oppose_evidence_ids=[],
        unknown_evidence_ids=[],
        verdict="support",
        missing_dimensions=[],
        uncertainty_codes=[],
    )
    result = assemble_claim_evaluation(
        proposal,
        uid(1),
        [evidence_id],
        [],
        uid(53),
        NOW,
        primary_evidence_ids={evidence_id},
    )
    assert result.verdict == "support"


def test_0126Z0_financial_claim_keeps_DART_primary_and_NAVER_corroborative():
    result = plan_hybrid_claim(
        claim("2025년 영업이익이 증가했다"),
        intent(EvidenceCategory.FINANCIAL_PERFORMANCE, topic_terms=["영업이익"]),
        stock_code="0126Z0",
        stock_name="삼성에피스홀딩스",
        as_of=NOW,
        id_factory=iter([uid(60), uid(61)]).__next__,
        clock=lambda: NOW,
    )
    assert {(q.provider, q.endpoint) for q in result.queries} == {
        ("dart", "financial_statement"),
        ("naver", "news_search"),
    }


def test_0126Z0_news_claim_can_plan_NAVER_primary():
    result = plan_hybrid_claim(
        claim("최근 뉴스가 나왔다"),
        intent(EvidenceCategory.NEWS_EVENT, topic_terms=["최근 뉴스"]),
        stock_code="0126Z0",
        stock_name="삼성에피스홀딩스",
        as_of=NOW,
        id_factory=iter([uid(62), uid(63)]).__next__,
        clock=lambda: NOW,
    )
    primary = next(item for item in result.requirements if item.role is EvidenceRole.PRIMARY)
    assert primary.status is RequirementStatus.READY
    assert (primary.query.provider, primary.query.endpoint) == ("naver", "news_search")


def test_0126Z0_price_claim_marks_Kiwoom_primary_unavailable_without_query():
    result = plan_hybrid_claim(
        claim("최근 주가가 많이 올랐다"),
        intent(EvidenceCategory.PRICE_MOVEMENT, topic_terms=["최근 주가"]),
        stock_code="0126Z0",
        stock_name="삼성에피스홀딩스",
        as_of=NOW,
        id_factory=iter([uid(64)]).__next__,
        clock=lambda: NOW,
    )
    primary = next(item for item in result.requirements if item.role is EvidenceRole.PRIMARY)
    assert primary.status is RequirementStatus.SOURCE_UNAVAILABLE
    assert primary.query is None
    assert {(q.provider, q.endpoint) for q in result.queries} == {
        ("naver", "news_search")
    }


def test_0126Z0_baseline_omits_only_unverified_Kiwoom_capability():
    queries = plan_baseline_queries(
        stock_code="0126Z0",
        stock_name="삼성에피스홀딩스",
        as_of=NOW,
        id_factory=iter([uid(65), uid(66)]).__next__,
        clock=lambda: NOW,
    )
    assert {(q.provider, q.endpoint) for q in queries} == {
        ("dart", "disclosure_list"),
        ("naver", "news_search"),
    }
