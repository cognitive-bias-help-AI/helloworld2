"""Hybrid Evidence Planner v1 taxonomy and provider authority registry."""

from dataclasses import dataclass
from enum import StrEnum


class EvidenceCategory(StrEnum):
    FINANCIAL_PERFORMANCE = "FINANCIAL_PERFORMANCE"
    PROFITABILITY = "PROFITABILITY"
    FINANCIAL_STABILITY = "FINANCIAL_STABILITY"
    FINANCIAL_GROWTH = "FINANCIAL_GROWTH"
    OPERATING_EFFICIENCY = "OPERATING_EFFICIENCY"
    VALUATION = "VALUATION"
    DISCLOSURE_EVENT = "DISCLOSURE_EVENT"
    BUSINESS_STRATEGY = "BUSINESS_STRATEGY"
    CAPITAL_SHAREHOLDER_ACTION = "CAPITAL_SHAREHOLDER_ACTION"
    COMPANY_GUIDANCE = "COMPANY_GUIDANCE"
    PRICE_MOVEMENT = "PRICE_MOVEMENT"
    TRADING_VOLUME_LIQUIDITY = "TRADING_VOLUME_LIQUIDITY"
    INVESTOR_FLOW = "INVESTOR_FLOW"
    NEWS_EVENT = "NEWS_EVENT"
    INDUSTRY_CONDITION = "INDUSTRY_CONDITION"
    DEMAND_SUPPLY = "DEMAND_SUPPLY"
    COMPETITIVE_POSITION = "COMPETITIVE_POSITION"
    MACRO_REGULATORY_ENVIRONMENT = "MACRO_REGULATORY_ENVIRONMENT"


class EvidenceRole(StrEnum):
    PRIMARY = "PRIMARY"
    CORROBORATIVE = "CORROBORATIVE"
    CONTEXT = "CONTEXT"
    UNAVAILABLE = "UNAVAILABLE"


class CoverageLevel(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    SOURCE_LIMITED = "SOURCE_LIMITED"


@dataclass(frozen=True)
class EvidenceSourceRule:
    provider: str
    endpoint: str
    role: EvidenceRole
    indicator_family: str | None = None


@dataclass(frozen=True)
class EvidenceRequirementRule:
    sources: tuple[EvidenceSourceRule, ...]
    coverage: CoverageLevel


def _source(provider: str, endpoint: str, role: EvidenceRole, family: str | None = None):
    return EvidenceSourceRule(provider, endpoint, role, family)


_DART_NEWS = (
    _source("dart", "disclosure_list", EvidenceRole.PRIMARY),
    _source("naver", "news_search", EvidenceRole.CORROBORATIVE),
)
_NEWS_ONLY = (_source("naver", "news_search", EvidenceRole.CORROBORATIVE),)

EVIDENCE_REQUIREMENT_REGISTRY = {
    EvidenceCategory.FINANCIAL_PERFORMANCE: EvidenceRequirementRule((_source("dart", "financial_statement", EvidenceRole.PRIMARY), *_NEWS_ONLY), CoverageLevel.FULL),
    EvidenceCategory.PROFITABILITY: EvidenceRequirementRule((_source("dart", "financial_indicator", EvidenceRole.PRIMARY, "profitability"), *_NEWS_ONLY), CoverageLevel.FULL),
    EvidenceCategory.FINANCIAL_STABILITY: EvidenceRequirementRule((_source("dart", "financial_indicator", EvidenceRole.PRIMARY, "stability"), *_NEWS_ONLY), CoverageLevel.FULL),
    EvidenceCategory.FINANCIAL_GROWTH: EvidenceRequirementRule((_source("dart", "financial_indicator", EvidenceRole.PRIMARY, "growth"), *_NEWS_ONLY), CoverageLevel.PARTIAL),
    EvidenceCategory.OPERATING_EFFICIENCY: EvidenceRequirementRule((_source("dart", "financial_indicator", EvidenceRole.PRIMARY, "activity"), *_NEWS_ONLY), CoverageLevel.FULL),
    EvidenceCategory.VALUATION: EvidenceRequirementRule(_NEWS_ONLY, CoverageLevel.SOURCE_LIMITED),
    EvidenceCategory.DISCLOSURE_EVENT: EvidenceRequirementRule(_DART_NEWS, CoverageLevel.PARTIAL),
    EvidenceCategory.BUSINESS_STRATEGY: EvidenceRequirementRule(_NEWS_ONLY, CoverageLevel.SOURCE_LIMITED),
    EvidenceCategory.CAPITAL_SHAREHOLDER_ACTION: EvidenceRequirementRule(_DART_NEWS, CoverageLevel.PARTIAL),
    EvidenceCategory.COMPANY_GUIDANCE: EvidenceRequirementRule(_NEWS_ONLY, CoverageLevel.SOURCE_LIMITED),
    EvidenceCategory.PRICE_MOVEMENT: EvidenceRequirementRule((_source("kiwoom", "daily_price_history", EvidenceRole.PRIMARY), *_NEWS_ONLY), CoverageLevel.FULL),
    EvidenceCategory.TRADING_VOLUME_LIQUIDITY: EvidenceRequirementRule(_NEWS_ONLY, CoverageLevel.SOURCE_LIMITED),
    EvidenceCategory.INVESTOR_FLOW: EvidenceRequirementRule((_source("kiwoom", "investor_flow", EvidenceRole.PRIMARY), *_NEWS_ONLY), CoverageLevel.FULL),
    EvidenceCategory.NEWS_EVENT: EvidenceRequirementRule((_source("naver", "news_search", EvidenceRole.PRIMARY), _source("dart", "disclosure_list", EvidenceRole.CORROBORATIVE)), CoverageLevel.FULL),
    EvidenceCategory.INDUSTRY_CONDITION: EvidenceRequirementRule(_NEWS_ONLY, CoverageLevel.SOURCE_LIMITED),
    EvidenceCategory.DEMAND_SUPPLY: EvidenceRequirementRule(_NEWS_ONLY, CoverageLevel.SOURCE_LIMITED),
    EvidenceCategory.COMPETITIVE_POSITION: EvidenceRequirementRule(_NEWS_ONLY, CoverageLevel.SOURCE_LIMITED),
    EvidenceCategory.MACRO_REGULATORY_ENVIRONMENT: EvidenceRequirementRule(_NEWS_ONLY, CoverageLevel.SOURCE_LIMITED),
}


__all__ = ["CoverageLevel", "EVIDENCE_REQUIREMENT_REGISTRY", "EvidenceCategory", "EvidenceRequirementRule", "EvidenceRole", "EvidenceSourceRule"]
