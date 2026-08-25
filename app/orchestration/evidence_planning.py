"""Query Specification — 호출에 필요한 값이 충분한지 판정하고 Query를 만든다.

🔴 B1 — EvidenceNeed(의미)와 분리된 두 번째 단계다.

    evidence_need.py       무슨 종류의 근거가 필요한가
    이 파일                 실제로 호출할 수 있는가

■ 사용자 사실 vs 조회 정책 — 이 구분이 이 파일의 전부다

fail-closed 는 "사용자가 모든 API 파라미터를 직접 말해야 한다" 는 뜻이 아니다.
두 가지를 구분한다.

  ● 사용자 사실 (User fact) — 없으면 **조회하지 않는다**
    틀리면 주장의 의미가 달라지는 값. 사업연도가 대표적이다.
    "2024년 영업이익" 과 "2025년 영업이익" 은 다른 주장이므로 시스템이
    연도를 골라주면 사용자가 하지 않은 주장을 검증하게 된다.

  ● 조회 정책 (Retrieval policy) — 시스템이 정한다
    무엇을 검증할지가 아니라 **어떻게 가져올지**에 관한 값.
    수정주가 여부, 수급 단위, 연결/별도 기본값이 여기에 속한다.
    사용자에게 "수정주가 기준인가요?" 를 매번 되묻는 것은 되묻기 예산만
    쓰고 판단을 바꾸지 못한다.

정책으로 채운 값은 `Query.params` 에 그대로 남으므로 사후 추적이 된다.
"어떤 기준으로 조회했는가" 는 params 를 보면 답할 수 있다.

■ 부족하면 조용히 사라지지 않는다

`missing_parameters()` 가 무엇이 없어서 조회하지 못했는지 이름으로 돌려준다.
n9 는 이것을 보고 "계약 위반(계획했어야 하는데 안 함)" 과 "정당한 미계획"을
구분한다. 이전에는 둘 다 UNKNOWN 이라 구분이 불가능했다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from app.domain.account_concepts import (
    accepted_dart_account_names,
    resolve_account_concepts,
)
from app.domain.evidence_need import (
    INDICATOR_FAMILIES,
    EvidenceNeed,
    classify_evidence_need,
    contains_any,
)
from app.domain.evidence_requirement import (
    EVIDENCE_REQUIREMENT_REGISTRY,
    CoverageLevel,
    EvidenceCategory,
    EvidenceRole,
    EvidenceSourceRule,
)
from app.domain.slots import EvidencePolicy, get_slot_definition
from app.orchestration.drafts import EvidenceIntentDraft
from app.schemas.frozen import Claim, Query
from providers.kiwoom.core import supports_stock_code as kiwoom_supports_stock_code
from providers.naver.query import build_query_params

_YEAR: Final = re.compile(r"(?<!\d)(20\d{2})(?!\d)")

_REPORT_CODES: Final[dict[str, str]] = {
    "사업보고서": "11011",
    "반기보고서": "11012",
    "1분기": "11013",
    "3분기": "11014",
}
_FS_DIV: Final[dict[str, str]] = {"연결": "CFS", "별도": "OFS"}
_MEASURES: Final[dict[str, str]] = {"수량": "quantity", "금액": "amount"}
_UNITS: Final[dict[str, str]] = {
    "주 단위": "shares",
    "천주": "thousand_shares",
    "백만원": "million_krw",
}


def disclosure_query_params(*, stock_code: str, as_of: datetime) -> dict[str, object]:
    """Return the single governed DART disclosure retrieval policy."""
    end_de = as_of.date()
    bgn_de = end_de - timedelta(days=DISCLOSURE_LOOKBACK_DAYS)
    return {
        "stock_code": stock_code,
        "bgn_de": bgn_de.strftime("%Y%m%d"),
        "end_de": end_de.strftime("%Y%m%d"),
        "sort": "date",
        "sort_mth": "desc",
        "page_no": 1,
        "page_count": 20,
    }
_VALID_FLOW_UNITS: Final[frozenset[tuple[str, str]]] = frozenset(
    {("quantity", "shares"), ("quantity", "thousand_shares"), ("amount", "million_krw")}
)
_UNIT_MEASURE: Final[dict[str, str]] = {
    "shares": "quantity",
    "thousand_shares": "quantity",
    "million_krw": "amount",
}
_INDICATOR_FAMILY_BY_TERM: Final[dict[str, str]] = {
    "ROE": "profitability",
    "ROA": "profitability",
    "부채비율": "stability",
    "회전율": "activity",
}

# ── 조회 정책 기본값 ──────────────────────────────────────────────
#
# 값을 여기 모아 두는 이유: "왜 연결 기준으로 조회했나" 를 물었을 때
# 코드 여러 곳을 뒤지지 않고 이 표만 보면 되게 하기 위해서다.
POLICY_REPORT_CODE: Final = "11011"   # 사업보고서 — 연간 확정치가 기준값이다
POLICY_FS_DIV: Final = "CFS"          # 연결 — 그룹 실적의 표준 표시
POLICY_MEASURE: Final = "amount"      # 수급은 금액 기준이 통용된다
POLICY_UNIT: Final = "million_krw"
POLICY_ADJUSTED_PRICE: Final = True   # 수정주가 — 액면분할 전후를 잇는다
DISCLOSURE_LOOKBACK_DAYS: Final = 180  # 분기·반기 공시를 포함하는 최근 맥락 창


@dataclass(frozen=True)
class ParameterResolution:
    """조회 계획과, 그것을 못 세운 이유."""

    planned: tuple[tuple[str, str, dict[str, object]], ...] = ()
    missing: tuple[str, ...] = ()
    policy_applied: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return bool(self.planned) and not self.missing


class RequirementStatus(StrEnum):
    READY = "READY"
    MISSING_USER_FACT = "MISSING_USER_FACT"
    AMBIGUOUS = "AMBIGUOUS"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class PlannedRequirement:
    category: EvidenceCategory
    role: EvidenceRole
    status: RequirementStatus
    provider: str | None = None
    endpoint: str | None = None
    query: Query | None = None
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class HybridClaimPlan:
    requirements: tuple[PlannedRequirement, ...]
    coverage: CoverageLevel

    @property
    def queries(self) -> tuple[Query, ...]:
        return tuple(item.query for item in self.requirements if item.query is not None)

    @property
    def has_executable_primary(self) -> bool:
        return any(
            item.role is EvidenceRole.PRIMARY
            and item.status is RequirementStatus.READY
            and item.query is not None
            for item in self.requirements
        )


def _single(text: str, options: dict[str, str]) -> tuple[str | None, bool]:
    """정확히 하나만 언급됐을 때 그 값을. 둘 이상이면 모호로 표시한다."""
    matched = list(dict.fromkeys(value for token, value in options.items() if token in text))
    if len(matched) == 1:
        return matched[0], False
    return None, len(matched) > 1


def _year(text: str) -> tuple[str | None, bool]:
    values = sorted(set(_YEAR.findall(text)))
    if len(values) == 1:
        return values[0], False
    return None, len(values) > 1


def _trade_kind(text: str) -> tuple[str | None, bool]:
    """'순매수' 는 '매수' 를 포함하므로 먼저 걷어내고 본다."""
    if "순매수" in text:
        return (None, True) if "매도" in text.replace("순매수", "") else ("net_buy", False)
    return _single(text, {"매수": "buy", "매도": "sell"})


def _flow_measure_and_unit(text: str) -> tuple[str | None, str | None, tuple[str, ...]]:
    """수급의 measure/unit. 둘 다 정책값이 있지만 사용자가 말하면 그쪽을 따른다."""
    measure, measure_ambiguous = _single(text, _MEASURES)
    unit, unit_ambiguous = _single(text, _UNITS)
    if measure_ambiguous:
        return None, None, ("measure",)
    if unit_ambiguous:
        return None, None, ("unit",)
    if unit is not None and measure is None:
        measure = _UNIT_MEASURE[unit]   # 단위가 measure 를 결정한다
    if measure is None:
        return POLICY_MEASURE, POLICY_UNIT, ()
    if unit is None:
        unit = POLICY_UNIT if measure == "amount" else "shares"
    if (measure, unit) not in _VALID_FLOW_UNITS:
        return None, None, ("unit",)
    return measure, unit, ()


def _indicator_family(text: str) -> tuple[str | None, bool]:
    explicit, ambiguous = _single(text, INDICATOR_FAMILIES)
    if explicit is not None or ambiguous:
        return explicit, ambiguous
    return _single(text, _INDICATOR_FAMILY_BY_TERM)


def _claim_grounded_topic(stock_name: str, text: str) -> str:
    """Use Claim text when N5 supplied no narrower topic terms."""

    name = stock_name.strip()
    claim_text = text.strip()
    if claim_text.startswith(name):
        claim_text = claim_text[len(name):].lstrip()
        for particle in ("은", "는", "이", "가", "의", "을", "를", "에", "에서"):
            if claim_text.startswith(particle):
                claim_text = claim_text[len(particle):].lstrip()
                break
    return " ".join(part for part in (name, claim_text) if part).strip()


def resolve_parameters(
    need: EvidenceNeed,
    text: str,
    *,
    stock_code: str,
    stock_name: str,
    as_of: datetime,
) -> ParameterResolution:
    """need 와 문장으로부터 Provider 호출 파라미터를 만든다."""

    if need is EvidenceNeed.DISCLOSURE:
        return ParameterResolution(
            planned=((
                "dart",
                "disclosure_list",
                disclosure_query_params(stock_code=stock_code, as_of=as_of),
            ),)
        )

    if need is EvidenceNeed.NEWS:
        return ParameterResolution(
            planned=tuple(
                ("naver", "news_search", params)
                for params in build_query_params(stock_code, stock_name)
            )
        )

    if need is EvidenceNeed.MARKET_PRICE:
        if not kiwoom_supports_stock_code(stock_code):
            return ParameterResolution()
        # 기준일과 수정주가 여부는 둘 다 조회 정책이다.
        return ParameterResolution(
            planned=(
                (
                    "kiwoom",
                    "daily_price_history",
                    {
                        "stock_code": stock_code,
                        "base_date": as_of.strftime("%Y%m%d"),
                        "adjusted_price": "비수정주가" not in text,
                    },
                ),
            ),
            policy_applied=("base_date", "adjusted_price"),
        )

    if need is EvidenceNeed.INVESTOR_FLOW:
        if not kiwoom_supports_stock_code(stock_code):
            return ParameterResolution()
        trade_kind, _ = _trade_kind(text)
        measure, unit, unit_missing = _flow_measure_and_unit(text)
        missing = []
        if trade_kind is None:
            missing.append("trade_kind")   # 매수인지 매도인지는 사용자 사실이다
        missing.extend(unit_missing)
        if missing:
            return ParameterResolution(missing=tuple(missing))
        policy = ["date"]
        if not contains_any(text, tuple(_MEASURES)) and not contains_any(text, tuple(_UNITS)):
            policy.extend(("measure", "unit"))
        return ParameterResolution(
            planned=(
                (
                    "kiwoom",
                    "investor_flow",
                    {
                        "stock_code": stock_code,
                        "date": as_of.strftime("%Y%m%d"),
                        "measure": measure,
                        "trade_kind": trade_kind,
                        "unit": unit,
                    },
                ),
            ),
            policy_applied=tuple(policy),
        )

    if need in {EvidenceNeed.FINANCIAL_STATEMENT, EvidenceNeed.FINANCIAL_INDICATOR}:
        year, _ = _year(text)
        report_code, report_ambiguous = _single(text, _REPORT_CODES)
        missing: list[str] = []
        policy: list[str] = []
        if year is None:
            # 🔴 사업연도는 사용자 사실이다. 시스템이 고르면 사용자가 하지 않은
            #    주장을 검증하게 된다.
            missing.append("bsns_year")
        if report_ambiguous:
            missing.append("reprt_code")
        elif report_code is None:
            report_code = POLICY_REPORT_CODE
            policy.append("reprt_code")

        if need is EvidenceNeed.FINANCIAL_STATEMENT:
            fs_div, fs_ambiguous = _single(text, _FS_DIV)
            concepts = resolve_account_concepts(text)
            accounts = list(accepted_dart_account_names(concepts))
            if fs_ambiguous:
                missing.append("fs_div")
            elif fs_div is None:
                fs_div = POLICY_FS_DIV
                policy.append("fs_div")
            if not accounts:
                missing.append("account_names")
            if missing:
                return ParameterResolution(missing=tuple(missing))
            return ParameterResolution(
                planned=(
                    (
                        "dart",
                        "financial_statement",
                        {
                            "stock_code": stock_code,
                            "bsns_year": year,
                            "reprt_code": report_code,
                            "fs_div": fs_div,
                            "account_names": accounts,
                        },
                    ),
                ),
                policy_applied=tuple(policy),
            )

        family, family_ambiguous = _indicator_family(text)
        if family_ambiguous or family is None:
            missing.append("indicator_family")
        if missing:
            return ParameterResolution(missing=tuple(missing))
        return ParameterResolution(
            planned=(
                (
                    "dart",
                    "financial_indicator",
                    {
                        "stock_code": stock_code,
                        "bsns_year": year,
                        "reprt_code": report_code,
                        "indicator_family": family,
                    },
                ),
            ),
            policy_applied=tuple(policy),
        )

    return ParameterResolution()


def missing_parameters(need: EvidenceNeed, text: str) -> tuple[str, ...]:
    """조회를 막고 있는 파라미터 이름. 종목·시각과 무관하게 답할 수 있다.

    n9 가 stock 없이도 "이 Claim 은 계획하지 않는 것이 맞다" 를 판정하려면
    이 질문이 종목 정보에서 독립이어야 한다. 그래서 placeholder 로 푼다.
    """
    if need is EvidenceNeed.UNKNOWN:
        return ()
    return resolve_parameters(
        need,
        text,
        stock_code="005930",
        stock_name="placeholder",
        as_of=datetime(2026, 1, 1),
    ).missing


def plan_claim_queries(
    claim: Claim,
    *,
    stock_code: str,
    stock_name: str,
    as_of: datetime,
    id_factory: Callable[[], str],
    clock: Callable[[], datetime],
) -> tuple[Query, ...]:
    need = classify_evidence_need(claim)
    if need is EvidenceNeed.UNKNOWN:
        return ()
    policy = get_slot_definition(claim.slot_id).evidence_policy
    intent = "counter" if policy is EvidencePolicy.SYSTEM_OPPOSING_SEARCH else "verify"
    try:
        resolution = resolve_parameters(
            need,
            claim.normalized_proposition,
            stock_code=stock_code,
            stock_name=stock_name,
            as_of=as_of,
        )
    except ValueError:
        return ()
    if not resolution.ready:
        return ()
    return tuple(
        Query(
            query_id=id_factory(),
            scope="claim",
            claim_id=claim.claim_id,
            intent=intent,
            provider=provider,
            endpoint=endpoint,
            params=params,
            created_at=clock(),
        )
        for provider, endpoint, params in resolution.planned
    )


def plan_baseline_queries(
    *,
    stock_code: str,
    stock_name: str,
    as_of: datetime,
    id_factory: Callable[[], str],
    clock: Callable[[], datetime],
) -> tuple[Query, ...]:
    """Build the review-level context plan; provider availability is an n6 concern."""
    specifications = [
        (
            "dart",
            "disclosure_list",
            disclosure_query_params(stock_code=stock_code, as_of=as_of),
        ),
        (
            "naver",
            "news_search",
            build_query_params(stock_code, stock_name)[0],
        ),
    ]
    if kiwoom_supports_stock_code(stock_code):
        specifications.insert(1, (
            "kiwoom",
            "daily_price_history",
            {
                "stock_code": stock_code,
                "base_date": as_of.strftime("%Y%m%d"),
                "adjusted_price": POLICY_ADJUSTED_PRICE,
            },
        ))
    return tuple(
        Query(
            query_id=id_factory(),
            scope="stock",
            claim_id=None,
            intent="context",
            provider=provider,
            endpoint=endpoint,
            params=params,
            created_at=clock(),
        )
        for provider, endpoint, params in specifications
    )


def _resolution_for_source(
    source: EvidenceSourceRule,
    category: EvidenceCategory,
    text: str,
    *,
    stock_code: str,
    stock_name: str,
    as_of: datetime,
    topic_terms: list[str],
) -> ParameterResolution:
    if source.provider == "naver":
        topic = (
            " ".join([stock_name, *topic_terms]).strip()
            if topic_terms
            else _claim_grounded_topic(stock_name, text)
        )
        return ParameterResolution(
            planned=((
                "naver",
                "news_search",
                build_query_params(
                    stock_code,
                    stock_name,
                    supplied_queries=[topic],
                )[0],
            ),)
        )
    if source.endpoint == "disclosure_list":
        return ParameterResolution(
            planned=(
                (
                    "dart",
                    source.endpoint,
                    disclosure_query_params(stock_code=stock_code, as_of=as_of),
                ),
            )
        )
    if source.endpoint == "daily_price_history":
        return resolve_parameters(
            EvidenceNeed.MARKET_PRICE,
            text,
            stock_code=stock_code,
            stock_name=stock_name,
            as_of=as_of,
        )
    if source.endpoint == "investor_flow":
        return resolve_parameters(
            EvidenceNeed.INVESTOR_FLOW,
            text,
            stock_code=stock_code,
            stock_name=stock_name,
            as_of=as_of,
        )
    need = (
        EvidenceNeed.FINANCIAL_STATEMENT
        if source.endpoint == "financial_statement"
        else EvidenceNeed.FINANCIAL_INDICATOR
    )
    resolution = resolve_parameters(
        need,
        text,
        stock_code=stock_code,
        stock_name=stock_name,
        as_of=as_of,
    )
    if source.indicator_family is not None and resolution.missing == ("indicator_family",):
        year, _ = _year(text)
        if year is None:
            return ParameterResolution(missing=("bsns_year",))
        return ParameterResolution(
            planned=((
                "dart",
                "financial_indicator",
                {
                    "stock_code": stock_code,
                    "bsns_year": year,
                    "reprt_code": POLICY_REPORT_CODE,
                    "indicator_family": source.indicator_family,
                },
            ),),
            policy_applied=("reprt_code",),
        )
    return resolution


def plan_hybrid_claim(
    claim: Claim,
    intent_draft: EvidenceIntentDraft,
    *,
    stock_code: str,
    stock_name: str,
    as_of: datetime,
    id_factory: Callable[[], str],
    clock: Callable[[], datetime],
) -> HybridClaimPlan:
    policy = get_slot_definition(claim.slot_id).evidence_policy
    primary_intent = "counter" if policy is EvidencePolicy.SYSTEM_OPPOSING_SEARCH else "verify"
    planned: list[PlannedRequirement] = []
    coverages: list[CoverageLevel] = []
    for requirement in intent_draft.requirements:
        rule = EVIDENCE_REQUIREMENT_REGISTRY[requirement.category]
        coverages.append(rule.coverage)
        has_primary = False
        for source in rule.sources:
            resolution = _resolution_for_source(
                source,
                requirement.category,
                claim.normalized_proposition,
                stock_code=stock_code,
                stock_name=stock_name,
                as_of=as_of,
                topic_terms=list(requirement.topic_terms),
            )
            if source.role is EvidenceRole.PRIMARY:
                has_primary = True
            query = None
            status = RequirementStatus.READY
            if resolution.missing:
                status = RequirementStatus.MISSING_USER_FACT
            elif resolution.ready:
                provider, endpoint, params = resolution.planned[0]
                query = Query(
                    query_id=id_factory(),
                    scope="claim",
                    claim_id=claim.claim_id,
                    intent=(primary_intent if source.role is EvidenceRole.PRIMARY else "context"),
                    provider=provider,
                    endpoint=endpoint,
                    params=params,
                    created_at=clock(),
                )
            else:
                status = RequirementStatus.SOURCE_UNAVAILABLE
            planned.append(PlannedRequirement(
                category=requirement.category,
                role=source.role,
                status=status,
                provider=source.provider,
                endpoint=source.endpoint,
                query=query,
                missing=resolution.missing,
            ))
        if not has_primary:
            planned.append(PlannedRequirement(
                category=requirement.category,
                role=EvidenceRole.UNAVAILABLE,
                status=RequirementStatus.SOURCE_UNAVAILABLE,
            ))
    coverage = (
        CoverageLevel.SOURCE_LIMITED
        if CoverageLevel.SOURCE_LIMITED in coverages
        else CoverageLevel.PARTIAL
        if CoverageLevel.PARTIAL in coverages
        else CoverageLevel.FULL
    )
    return HybridClaimPlan(tuple(planned), coverage)


__all__ = [
    "POLICY_ADJUSTED_PRICE",
    "DISCLOSURE_LOOKBACK_DAYS",
    "POLICY_FS_DIV",
    "POLICY_MEASURE",
    "POLICY_REPORT_CODE",
    "POLICY_UNIT",
    "ParameterResolution",
    "HybridClaimPlan",
    "PlannedRequirement",
    "RequirementStatus",
    "missing_parameters",
    "plan_baseline_queries",
    "disclosure_query_params",
    "plan_claim_queries",
    "plan_hybrid_claim",
    "resolve_parameters",
]
