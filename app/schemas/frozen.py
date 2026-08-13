"""
app/schemas/frozen.py — DDR v2.2 FINAL (FROZEN CANDIDATE) = v2.1d + 거짓-인쇄 차단 6건

Authority: v2.0 → v2.1 → v2.1a → FREEZE_CORRECTION(B) → v2.1c → v2.1d → **v2.2**

v2.1d 대비 변경 범위 (모두 "리포트가 사용자에게 거짓을 인쇄하는 경로"만 차단):
  S-1 OpposeBlock      verified → queries 최소 1건 / unverified → reason 필수
  S-2 ClaimEvaluation  verdict 와 근거 버킷의 정합 (support/partial_support/contradicted)
  S-3 NumericCheck     result 와 observed 의 정합
  S-4 Claim            superseded_by 자기참조 금지
  S-5 ConflictRecord   claim_id_a == claim_id_b 금지
  S-6 source_url       http(s) 스킴만 허용
  S-7 PROVIDER_SOURCE_TYPE 상수 신설 (필드 추가 아님 — 구현자 임의 결정 차단)
  S-8 미사용 import datetime 제거

의도적으로 닫지 않은 것 (근거는 DDR §2C 참조):
  - fetched_at vs as_of 순서      : D-16 as_of 정의 원문 미확인. 잘못 조이면 캐시/휴장일 경로가 막힌다
  - published_at vs as_of         : 정정공시·장중 데이터를 스키마가 잘라낼 위험 (KRXCode 사고와 같은 계열)
  - ctx_chars vs prompt_tokens    : 문자→토큰 계수 r 미확정 (DDR §8-2)
  - verdict unsupported/unverifiable 의 버킷 제약 : 정당한 공집합 케이스가 존재
  - Finding/GuardInput 의 citation 유효성 : packet 을 스키마가 모른다 → n9 조립기 담당

3인 approve 후 FROZEN 승격. 승인 없이 기존 의미 변경 금지.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Final, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StringConstraints,
    field_validator,
    model_validator,
)

# ──────────────────────────────────────────────────────────────────────
# Shared constrained contract types
# ──────────────────────────────────────────────────────────────────────
ULID = Annotated[
    str,
    StringConstraints(pattern=r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$"),
]
# 🔴 v2.1c 수정: 신형우선주 단축코드는 마지막 1자리가 영문이다.
#    실재 예 — 00781K(코리아써키트2우B) 03473K(SK우) 18064K(한진칼우) 02826K(삼성물산우B)
#    ^\d{6}$ 로 두면 이 종목들이 스키마 단계에서 원천 차단된다.
#    반증 조건: T1-B 가 KRX 상장종목 마스터를 적재할 때 이 패턴에
#              맞지 않는 단축코드가 1건이라도 나오면 패턴을 넓힌다.
KRXCode = Annotated[str, StringConstraints(pattern=r"^[0-9]{5}[0-9A-Z]$")]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonBlankStr = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
# 🆕 v2.2 S-6: 리포트에 그대로 링크로 실리는 값이다.
#    javascript: / data: 스킴이 통과하면 우리가 만든 리포트가 공격 벡터가 된다.
#    DART·네이버 어댑터는 이미 절대 https URL 을 만들도록 Task Card 에 고정돼 있으므로
#    이 제약으로 막히는 정상 케이스는 없다. 키움은 source_url=None 이라 무관하다.
HttpUrlStr = Annotated[str, StringConstraints(pattern=r"^https?://")]
SlotId = Annotated[int, Field(ge=1, le=8)]
NonNegativeInt = Annotated[int, Field(ge=0)]
NonNegativeFloat = Annotated[FiniteFloat, Field(ge=0)]
Probability = Annotated[FiniteFloat, Field(ge=0, le=1)]
PositiveFloat = Annotated[FiniteFloat, Field(gt=0)]
HttpStatus = Annotated[int, Field(ge=100, le=599)]

# 🆕 v2.2 S-7: provider(누가 호출했나) → source_type(무슨 출처인가) 매핑.
#    EvidenceDraft 에 provider 필드를 넣지 않는 이유: 어댑터 권한 경계를 흐린다.
#    게이트웨이는 q.provider 를 이미 알고 있으므로 조립 시점에 이 표로 대조하면 된다.
#    상수로 고정하는 이유: 이 표가 없으면 팀원1/2/3 이 각자 매핑을 정하게 되고,
#    naver 호출 결과가 source_type="dart" 로 집계되어도 스키마는 통과한다(v2.2 실측 H1).
PROVIDER_SOURCE_TYPE: Final[dict[str, str]] = {
    "dart": "dart",
    "naver": "news",
    "kiwoom": "quote",
}


class _ContractModel(BaseModel):
    """All cross-module contracts are closed: undeclared fields are rejected."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        validate_default=True,
    )


def _validate_span_offset(value: tuple[int, int]) -> tuple[int, int]:
    start, end = value
    if start < 0:
        raise ValueError("span_offset start는 0 이상이어야 함")
    if end <= start:
        raise ValueError("span_offset end는 start보다 커야 함")
    return value


def _validate_evidence_partition(
    citations: list["CitationRef"],
    support: list[str],
    oppose: list[str],
    neutral: list[str],
    unknown: list[str],
    numeric_checks: list["NumericCheck"] | None = None,
) -> None:
    """Evidence 4분할 검증.

    🔴 v2.1c: neutral 버킷을 추가했다.
    ClaimEvidence.stance 는 support/oppose/neutral/unknown 4값인데
    ClaimEvaluation 에는 neutral 을 담을 자리가 없었다.
    neutral 을 unknown 으로 접으면 리포트가 '확인할 수 없었습니다' 라고 쓰는데
    실제로는 읽고 무관하다고 판정한 것이 되어 D-14 와 같은 종류의 거짓이 된다.
    """
    groups = [support, oppose, neutral, unknown]
    labels = ["support", "oppose", "neutral", "unknown"]

    for label, values in zip(labels, groups, strict=True):
        if len(values) != len(set(values)):
            raise ValueError(f"{label}_evidence_ids에는 중복 ID를 둘 수 없음")

    sets = [set(values) for values in groups]
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            if sets[i] & sets[j]:
                raise ValueError(
                    f"{labels[i]}/{labels[j]} evidence 집합은 상호배타적이어야 함"
                )

    declared: set[str] = set().union(*sets)
    citation_ids = {c.evidence_id for c in citations}
    if not citation_ids <= declared:
        raise ValueError("citation은 선언된 evidence 집합만 참조할 수 있음")

    if numeric_checks is not None:
        numeric_ids = {n.evidence_id for n in numeric_checks}
        if not numeric_ids <= declared:
            raise ValueError("NumericCheck는 선언된 evidence 집합만 참조할 수 있음")


def _validate_verdict_backing(
    verdict: str,
    support: list[str],
    oppose: list[str],
    numeric_checks: list["NumericCheck"] | None = None,
) -> None:
    """🆕 v2.2 S-2 — 결론은 근거를 갖는다.

    문제: verdict="support" 인데 support_evidence_ids=[] 여도 v2.1d 는 통과했다(실측 H13).
          n9 는 이 verdict 를 읽어 Finding 을 만들고 리포트는 "근거로 뒷받침됩니다" 를 인쇄한다.
          인용할 근거가 0건이면 그 문장 자체가 거짓이 된다. D-31(인용 선행)이 막으려던 것과 같은 계열.

    왜 규칙 검산(NumericCheck)을 대안 근거로 인정하나:
          LLM 이 어떤 근거를 neutral 로 분류했더라도 규칙이 수치 일치를 확인했다면
          그 판단의 근거는 실재한다. 이 예외를 두지 않으면 향후 조립기가
          "규칙 검산 결과로 verdict 를 덮어쓰는" 경로를 만들 때 스키마가 막아버린다.

    왜 unsupported / unverifiable 에는 제약을 걸지 않나:
          "지지 근거가 없다" 와 "판단할 수 없다" 는 공집합이 정상값이다.
          여기까지 조이면 KRXCode 사고처럼 정당한 케이스를 스키마가 잘라낸다.
    """
    checks = numeric_checks or []
    if verdict in ("support", "partial_support"):
        has_rule_backing = any(n.result == "consistent" for n in checks)
        if not support and not has_rule_backing:
            raise ValueError(
                f"verdict='{verdict}'는 support_evidence_ids 최소 1건 또는 "
                "consistent NumericCheck 최소 1건이 필요함 (v2.2 S-2)"
            )
    if verdict == "contradicted":
        has_rule_backing = any(n.result == "inconsistent" for n in checks)
        if not oppose and not has_rule_backing:
            raise ValueError(
                "verdict='contradicted'는 oppose_evidence_ids 최소 1건 또는 "
                "inconsistent NumericCheck 최소 1건이 필요함 (v2.2 S-2)"
            )


# ══════════════════════════════════════════════════════════════════
# (1) SourceTrace — D-25
# ══════════════════════════════════════════════════════════════════
class SourceTrace(str, Enum):
    # v2.1d: Form/Survey의 직접 사용자 입력을 Chat/LLM 추출과 구별한다.
    # 🔴 선언 순서는 우선순위가 아니다. USER_CONFIRMED 가 CHAT_EXPLICIT 뒤에 있는 것이 그 증거다.
    #    충돌 해소 우선순위는 ConflictRecord + HITL 이 정하지 enum 순서가 정하지 않는다.
    SURVEY = "survey"
    CHAT_EXPLICIT = "chat_explicit"
    USER_CONFIRMED = "user_confirmed"
    LLM_EXTRACTION = "llm_extraction"
    SYSTEM_INFERENCE = "system_inference"
    MARKET_DATA = "market_data"
    UNKNOWN = "unknown"


# ══════════════════════════════════════════════════════════════════
# (2) NodeStatus + ReasonCode
# ══════════════════════════════════════════════════════════════════
class NodeStatus(str, Enum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    BLOCKED = "BLOCKED"


class ReasonCode(str, Enum):
    INPUT_INSUFFICIENT = "input_insufficient"
    OUT_OF_SCOPE = "out_of_scope"
    STOCK_UNRESOLVED = "stock_unresolved"
    PII_DETECTED = "pii_detected"
    ILLEGAL_REQUEST = "illegal_request"
    SELF_HARM_SIGNAL = "self_harm_signal"
    PROMPT_INJECTION = "prompt_injection"
    RATE_LIMIT = "rate_limit"
    UPSTREAM_5XX = "upstream_5xx"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    AUTH_FAILED = "auth_failed"
    IP_MISMATCH = "ip_mismatch"
    NO_RESULT = "no_result"
    COVERAGE_TRUNCATED = "coverage_truncated"
    STALE_DATA = "stale_data"
    SCHEMA_INVALID = "schema_invalid"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    FORBIDDEN_EXPRESSION = "forbidden_expression"
    SPAN_MISMATCH = "span_mismatch"
    BUDGET_EXCEEDED = "budget_exceeded"
    TIMEOUT_MACHINE = "timeout_machine"
    TIMEOUT_HITL = "timeout_hitl"
    STALE_SNAPSHOT = "stale_snapshot"
    CONFLICT_UNRESOLVED = "conflict_unresolved"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    CONTEXT_OVERFLOW = "context_overflow"
    CONTRACT_VIOLATION = "contract_violation"


class NodeResult(_ContractModel):
    node_name: NonBlankStr
    status: NodeStatus
    reason_code: ReasonCode | None = None
    detail: str | None = None
    retry_count: NonNegativeInt = 0
    elapsed_ms: NonNegativeInt


# ══════════════════════════════════════════════════════════════════
# (3) Claim — 사용자 진술에서 뽑은 명제
# ══════════════════════════════════════════════════════════════════
class Claim(_ContractModel):
    claim_id: ULID
    slot_id: SlotId
    user_text_span: NonBlankStr
    span_offset: tuple[int, int]
    normalized_proposition: NonBlankStr
    verifiable: bool
    origin: SourceTrace
    superseded_by: ULID | None = None
    created_at: AwareDatetime

    @field_validator("span_offset")
    @classmethod
    def validate_span_offset(cls, value: tuple[int, int]) -> tuple[int, int]:
        return _validate_span_offset(value)

    @model_validator(mode="after")
    def enforce_no_self_supersede(self):
        # 🆕 v2.2 S-4: 자기 자신으로 대체되면 계보 추적이 무한루프에 빠진다.
        #    긴 순환(A→B→A)은 스키마가 못 잡는다 — 그건 n4/조립기 담당.
        #    자기참조는 비용 0·오탐 0 으로 잡히므로 여기서 잡는다.
        if self.superseded_by is not None and self.superseded_by == self.claim_id:
            raise ValueError("Claim은 자기 자신으로 supersede될 수 없음 (v2.2 S-4)")
        return self


# ══════════════════════════════════════════════════════════════════
# (4) Query — n5의 출력. Evidence <-> Claim 라우팅의 유일한 경로
# ══════════════════════════════════════════════════════════════════
class Query(_ContractModel):
    query_id: ULID
    scope: Literal["claim", "stock"]
    claim_id: ULID | None = None
    intent: Literal["verify", "counter", "context"]
    provider: Literal["dart", "naver", "kiwoom"]
    endpoint: NonBlankStr
    params: dict[str, Any]
    created_at: AwareDatetime

    @model_validator(mode="after")
    def enforce_scope(self):
        if self.scope == "claim" and self.claim_id is None:
            raise ValueError("scope='claim'이면 claim_id 필수")
        if self.scope == "stock" and self.claim_id is not None:
            raise ValueError("scope='stock'이면 claim_id를 채울 수 없다")
        return self

    @property
    def expected_source_type(self) -> str:
        """게이트웨이 조립기가 draft.source_type 을 대조할 기준값 (v2.2 S-7)."""
        return PROVIDER_SOURCE_TYPE[self.provider]


# ══════════════════════════════════════════════════════════════════
# (5) Evidence proposal -> canonical Evidence
# ══════════════════════════════════════════════════════════════════
class EvidenceDraft(_ContractModel):
    """Provider adapter output.

    Adapter-owned semantic fields only. Canonical IDs, ProviderCall FK,
    canonical hash, fetch time, and review as_of are deliberately absent.
    The gateway owns those canonical/acquisition fields.
    """

    source_type: Literal["dart", "news", "quote"]
    source_ref: NonBlankStr
    source_url: HttpUrlStr | None = None
    publisher: NonBlankStr | None = None
    published_at: AwareDatetime | None = None
    raw_span: NonBlankStr = Field(max_length=500)
    span_scope: Literal["headline_snippet", "full_text", "structured_field"]
    normalized_value: dict[str, Any] | None = None


class Evidence(_ContractModel):
    """Canonical durable evidence.

    `evidence_id`, `content_sha256`, and `provider_request_id` are system-owned
    and therefore exist only on the canonical model, never on EvidenceDraft.
    """

    evidence_id: ULID
    source_type: Literal["dart", "news", "quote"]
    source_ref: NonBlankStr
    source_url: HttpUrlStr | None = None
    publisher: NonBlankStr | None = None
    published_at: AwareDatetime | None = None
    fetched_at: AwareDatetime
    raw_span: NonBlankStr = Field(max_length=500)
    span_scope: Literal["headline_snippet", "full_text", "structured_field"]
    content_sha256: Sha256Hex
    normalized_value: dict[str, Any] | None = None
    provider_request_id: ULID
    as_of: AwareDatetime


class EvidenceQueryLink(_ContractModel):
    evidence_id: ULID
    query_id: ULID


# ══════════════════════════════════════════════════════════════════
# (6) ClaimEvidence — 관계. stance는 여기 산다. n7 의 산출물
# ══════════════════════════════════════════════════════════════════
class ClaimEvidenceDraft(_ContractModel):
    """🆕 v2.2 S-9 — n7 LLM 이 근거 1건에 대해 낼 수 있는 것의 전부.

    문제: v2.1d 까지 n7 의 output_schema 가 무엇인지 어디에도 없었고,
          ClaimEvidence 를 그대로 쓰면 LLM 이 stance_source="rule" 을 스스로 선언할 수 있다.
          v2.1c 가 ClaimEvaluationDraft 를 분리한 이유(LLM 이 computed_by="rule" 선언)와
          글자 그대로 같은 결함이다. 그 결함을 한 모델에서만 고치고 다른 모델에 남겨두면
          "필드가 없으므로 샐 수 없다"는 원리가 반쪽이 된다.

    claim_id 를 넣지 않는 이유: EvidencePacket 은 Claim 1건에 대응한다(v2.1a §4).
          LLM 이 claim_id 를 다시 쓰게 하면 packet 과 다른 Claim 에 결과를 붙일 수 있다.
    query_id 를 넣지 않는 이유: EvidenceQueryLink 가 이미 갖고 있다. 조립기가 채운다.
    """

    evidence_id: ULID
    stance: Literal["support", "oppose", "neutral", "unknown"]
    confidence: Probability | None = None


class ClaimStanceDraft(_ContractModel):
    """🆕 v2.2 S-9 — n7 의 output_schema. packet 1개 → 객체 1개.

    ModelGateway.invoke 는 BaseModel 1개를 돌려주므로(v2.1a §5.4) 리스트를 감싼다.
    union(stances) == packet_evidence_ids 검사는 스키마가 packet 을 모르므로
    n7 조립기(assemble_claim_evidence)가 한다 — n8 과 동일한 구조다.
    """

    stances: list[ClaimEvidenceDraft]

    @model_validator(mode="after")
    def enforce_unique_evidence(self):
        ids = [s.evidence_id for s in self.stances]
        if len(ids) != len(set(ids)):
            raise ValueError("같은 evidence_id에 stance를 두 번 매길 수 없음 (v2.2 S-9)")
        return self


class ClaimEvidence(_ContractModel):
    """Canonical. 🔴 LLM output_schema 로 지정 금지 — ClaimStanceDraft 를 쓴다.

    stance_source 는 조립기가 채운다: n7 경로면 "llm", 규칙 파생이면 "rule".
    CI 불변식 I8 이 어떤 프롬프트도 이 모델을 output_schema 로 쓰지 않는지 검사한다.
    """

    claim_id: ULID
    evidence_id: ULID
    stance: Literal["support", "oppose", "neutral", "unknown"]
    stance_source: Literal["llm", "rule"]
    confidence: Probability | None = None
    query_id: ULID | None = None

    @property
    def key(self) -> str:
        return f"{self.claim_id}:{self.evidence_id}"


# ══════════════════════════════════════════════════════════════════
# (7) CitationRef · NumericCheck · ClaimEvaluation
# ══════════════════════════════════════════════════════════════════
class CitationRef(_ContractModel):
    evidence_id: ULID
    span: NonBlankStr = Field(max_length=500)


class NumericCheck(_ContractModel):
    metric: NonBlankStr
    claimed: NonBlankStr
    observed: FiniteFloat | None = None
    unit: NonBlankStr | None = None
    period: NonBlankStr | None = None
    result: Literal["consistent", "inconsistent", "not_comparable", "no_data"]
    evidence_id: ULID
    computed_by: Literal["rule"] = "rule"

    @model_validator(mode="after")
    def enforce_observed_consistency(self):
        """🆕 v2.2 S-3 — 대조 결과와 관측값의 정합.

        문제: result="inconsistent", observed=None 이 v2.1d 를 통과했다(실측 H14).
              리포트는 "주장 9,178,955 vs 실제 X" 형태로 인쇄하는데 X 가 없다.
              무엇과 비교해서 불일치라고 했는지가 없는 상태로 '불일치' 를 인쇄하게 된다.
        not_comparable 을 제외하는 이유: 단위·기간이 달라 비교 불가인 경우
              관측값이 있어도 없어도 정상이다. 여기까지 조이면 정당한 케이스가 막힌다.
        """
        if self.result in ("consistent", "inconsistent") and self.observed is None:
            raise ValueError(
                f"result='{self.result}'는 observed 필수 — 무엇과 대조했는지 없이 "
                "판정을 인쇄할 수 없음 (v2.2 S-3)"
            )
        if self.result == "no_data" and self.observed is not None:
            raise ValueError("result='no_data'인데 observed가 채워져 있음 (v2.2 S-3)")
        return self


class ClaimEvaluationDraft(_ContractModel):
    """LLM-facing output schema for n8.

    The model may classify/cite already-existing evidence, but cannot mint the
    canonical evaluation ID, bind itself to a claim ID, provide rule-owned
    NumericCheck values, or choose a canonical timestamp.
    """

    citations: list[CitationRef]
    support_evidence_ids: list[ULID]
    oppose_evidence_ids: list[ULID]
    neutral_evidence_ids: list[ULID] = Field(default_factory=list)   # 🆕 v2.1c
    unknown_evidence_ids: list[ULID]
    verdict: Literal[
        "support", "partial_support", "unsupported", "contradicted", "unverifiable"
    ]
    missing_dimensions: list[SlotId]
    uncertainty_codes: list[ReasonCode]

    @model_validator(mode="after")
    def enforce_evidence_partition(self):
        _validate_evidence_partition(
            self.citations,
            self.support_evidence_ids,
            self.oppose_evidence_ids,
            self.neutral_evidence_ids,
            self.unknown_evidence_ids,
        )
        # 🆕 v2.2 S-2. Draft 에는 numeric_checks 가 없으므로 버킷만으로 판정한다.
        _validate_verdict_backing(
            self.verdict, self.support_evidence_ids, self.oppose_evidence_ids
        )
        if len(self.missing_dimensions) != len(set(self.missing_dimensions)):
            raise ValueError("missing_dimensions에는 중복 slot_id를 둘 수 없음")
        return self


class ClaimEvaluation(_ContractModel):
    """Canonical n8 output persisted for n9 typed reduction."""

    claim_evaluation_id: ULID
    claim_id: ULID
    citations: list[CitationRef]
    support_evidence_ids: list[ULID]
    oppose_evidence_ids: list[ULID]
    neutral_evidence_ids: list[ULID] = Field(default_factory=list)   # 🆕 v2.1c
    unknown_evidence_ids: list[ULID]
    numeric_checks: list[NumericCheck]
    verdict: Literal[
        "support", "partial_support", "unsupported", "contradicted", "unverifiable"
    ]
    missing_dimensions: list[SlotId]
    uncertainty_codes: list[ReasonCode]
    created_at: AwareDatetime

    @model_validator(mode="after")
    def enforce_evidence_partition(self):
        _validate_evidence_partition(
            self.citations,
            self.support_evidence_ids,
            self.oppose_evidence_ids,
            self.neutral_evidence_ids,
            self.unknown_evidence_ids,
            self.numeric_checks,
        )
        # 🆕 v2.2 S-2. canonical 은 규칙 검산 결과를 대안 근거로 인정한다.
        _validate_verdict_backing(
            self.verdict,
            self.support_evidence_ids,
            self.oppose_evidence_ids,
            self.numeric_checks,
        )
        if len(self.missing_dimensions) != len(set(self.missing_dimensions)):
            raise ValueError("missing_dimensions에는 중복 slot_id를 둘 수 없음")
        return self


# ══════════════════════════════════════════════════════════════════
# (8) OpposeBlock — D-14
# ══════════════════════════════════════════════════════════════════
class OpposeBlock(_ContractModel):
    status: Literal["verified", "unverified"]
    count: NonNegativeInt | None = None
    queries: list[NonBlankStr] | None = None
    reason: ReasonCode | None = None

    @model_validator(mode="after")
    def enforce_invariant(self):
        if self.status == "unverified" and self.count is not None:
            raise ValueError("unverified 상태에서 count를 채울 수 없다")
        if self.status == "verified" and self.count is None:
            raise ValueError("verified 상태에서 count는 필수")

        # 🆕 v2.2 S-1a
        # 문제: v2.1d 는 status="verified", queries=None 을 통과시켰다(실측 H4).
        #       리포트는 "반대 근거를 찾아봤습니다" 를 인쇄하는데 검색을 하나도 안 돌린 상태다.
        #       count=0 이 정직한 "찾아봤는데 없었다" 인지, 아니면 아예 안 찾은 것인지
        #       구분할 방법이 없다. D-14 가 막으려던 count 부풀림과 정확히 같은 거짓이다.
        # count=0 은 계속 허용한다 — "검색은 돌렸고 반대 근거가 없었다" 는 정상값이다.
        if self.status == "verified" and not self.queries:
            raise ValueError(
                "verified 상태에서 queries 최소 1건 필수 — 검색을 돌리지 않고 "
                "'반대 검토를 했다'고 주장할 수 없음 (v2.2 S-1)"
            )

        # 🆕 v2.2 S-1b
        # 문제: unverified 인데 reason 이 없으면 리포트는 "확인하지 못했습니다" 만 쓰고
        #       왜인지 못 쓴다. 사용자는 그것이 한도초과인지 장애인지 알 수 없다.
        #       프로젝트 원칙 "Block/종료 처리 시 알람 안내" 와 같은 계열.
        # queries 는 unverified 에서도 허용한다 — 돌렸으나 RATE_LIMIT 로 실패한 경우가 있다.
        if self.status == "unverified" and self.reason is None:
            raise ValueError(
                "unverified 상태에서 reason 필수 — 왜 검증하지 못했는지 없이 "
                "'확인 못함'만 인쇄할 수 없음 (v2.2 S-1)"
            )
        return self

    def to_llm_payload(self) -> dict:
        return self.model_dump(exclude_none=True)


# ══════════════════════════════════════════════════════════════════
# (9) Finding
# ══════════════════════════════════════════════════════════════════
class Finding(_ContractModel):
    finding_id: ULID
    slot_id: SlotId
    kind: Literal["mismatch", "missing", "unverified", "conflict"]
    citations: list[CitationRef]
    claim_evaluation_id: ULID | None = None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def enforce_citation(self):
        if self.kind == "mismatch" and not self.citations:
            raise ValueError("mismatch Finding은 citation 최소 1개 (v2.0 §4.4)")
        return self


# ══════════════════════════════════════════════════════════════════
# (10) Gateway types
# ══════════════════════════════════════════════════════════════════
class Request(_ContractModel):
    provider: Literal["dart", "naver", "kiwoom"]
    endpoint: NonBlankStr
    method: Literal["GET", "POST"] = "GET"
    params: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] | None = None
    timeout_s: PositiveFloat = 10.0


class RateLimitHint(_ContractModel):
    provider: Literal["dart", "naver", "kiwoom"]
    retry_after_ms: NonNegativeInt | None = None
    remaining: NonNegativeInt | None = None
    window_s: NonNegativeInt | None = None
    source: Literal["header", "body_message", "status_only"]


class CollectionResult(_ContractModel):
    source: Literal["dart", "news", "quote"]
    status: NodeStatus
    reason_code: ReasonCode | None = None
    items_fetched: NonNegativeInt = 0
    items_adopted: NonNegativeInt = 0
    items_deduped: NonNegativeInt = 0
    queries_run: NonNegativeInt = 0

    @model_validator(mode="after")
    def enforce_counts(self):
        if self.items_adopted + self.items_deduped > self.items_fetched:
            raise ValueError("items_adopted + items_deduped는 items_fetched를 초과할 수 없음")
        return self


class ProviderCall(_ContractModel):
    provider_request_id: ULID
    run_id: NonBlankStr
    provider: Literal["dart", "naver", "kiwoom"]
    endpoint: NonBlankStr
    query_id: ULID
    http_status: HttpStatus | None = None
    latency_ms: NonNegativeInt
    cache_hit: bool = False
    reason_code: ReasonCode | None = None
    idempotency_key: Sha256Hex
    created_at: AwareDatetime


# ══════════════════════════════════════════════════════════════════
# (11) Stock resolution
# ══════════════════════════════════════════════════════════════════
class StockCandidate(_ContractModel):
    code: KRXCode
    name: NonBlankStr
    market: Literal["KOSPI", "KOSDAQ"]
    match_kind: Literal["exact_code", "exact_name", "alias", "chosung", "prefix"]
    score: FiniteFloat
    is_delisted: bool = False
    is_managed: bool = False


# ══════════════════════════════════════════════════════════════════
# (12) Model/cost
# ══════════════════════════════════════════════════════════════════
class ModelSpec(_ContractModel):
    slot: Literal["SMALL", "MID", "LARGE"]
    model_id: NonBlankStr
    base_url: NonBlankStr
    reasoning_effort: NonBlankStr | None = None
    price_in_krw_per_1m: NonNegativeInt
    price_cached_in_krw_per_1m: NonNegativeInt | None = None
    price_out_krw_per_1m: NonNegativeInt


class Usage(_ContractModel):
    """Normalized model-usage observation.

    `prompt_tokens` is the normalized total input count used by the current
    cost formula, so cached_input_tokens cannot exceed it. cache_write_tokens
    is observational provider usage: no ordering relation with prompt_tokens
    is assumed at the frozen-schema layer.
    """

    model_slot: Literal["SMALL", "MID", "LARGE"]
    prompt_tokens: NonNegativeInt
    cached_input_tokens: NonNegativeInt = 0
    cache_write_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt
    ctx_chars: NonNegativeInt

    @model_validator(mode="after")
    def enforce_token_relationships(self):
        if self.cached_input_tokens > self.prompt_tokens:
            raise ValueError("cached_input_tokens는 prompt_tokens를 초과할 수 없음")
        return self


class CostRecord(_ContractModel):
    usage: Usage
    cost_krw: NonNegativeFloat
    chars_per_token: NonNegativeFloat


# ══════════════════════════════════════════════════════════════════
# (13) Output guard
# ══════════════════════════════════════════════════════════════════
class Violation(_ContractModel):
    slot_no: SlotId
    rule_id: NonBlankStr
    kind: Literal["lexicon", "pattern", "structure"]
    matched: NonBlankStr
    span_offset: tuple[int, int]

    @field_validator("span_offset")
    @classmethod
    def validate_span_offset(cls, value: tuple[int, int]) -> tuple[int, int]:
        return _validate_span_offset(value)


class GuardInput(_ContractModel):
    slot_no: SlotId
    text: NonBlankStr
    quoted: bool
    citations: list[CitationRef]


# ══════════════════════════════════════════════════════════════════
# (14) Theory note
# ══════════════════════════════════════════════════════════════════
class TheoryNote(_ContractModel):
    theory_id: NonBlankStr
    trigger: tuple[SlotId, Literal["absent", "partial"]]
    name: NonBlankStr
    definition: NonBlankStr = Field(max_length=200)
    observable_pattern: NonBlankStr = Field(max_length=200)
    non_diagnostic_warning: NonBlankStr
    source_refs: list[NonBlankStr] = Field(min_length=1)


# ══════════════════════════════════════════════════════════════════
# (15) Alerts
# ══════════════════════════════════════════════════════════════════
class AlertLevel(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AlertPath(str, Enum):
    SYNC_INPROCESS = "sync_inprocess"
    WEBHOOK = "webhook"
    AGGREGATE = "aggregate"


class Alert(_ContractModel):
    run_id: NonBlankStr
    level: AlertLevel
    path: AlertPath
    reason_code: ReasonCode | None = None
    user_message: NonBlankStr
    detail: str | None = None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def enforce_sync_for_critical(self):
        if self.level == AlertLevel.CRITICAL and self.path != AlertPath.SYNC_INPROCESS:
            raise ValueError("CRITICAL 알람은 인프로세스 동기 경로만 허용 (§6.3)")
        return self


# ══════════════════════════════════════════════════════════════════
# (16) Concurrency/conflict
# ══════════════════════════════════════════════════════════════════
class ReviewRun(_ContractModel):
    run_id: NonBlankStr
    thread_id: NonBlankStr
    snapshot_version: NonNegativeInt = 0
    as_of: AwareDatetime
    machine_elapsed_ms: NonNegativeInt = 0
    status: NodeStatus = NodeStatus.OK


class StateChange(_ContractModel):
    change_id: ULID
    run_id: NonBlankStr
    from_version: NonNegativeInt
    to_version: NonNegativeInt
    change_type: Literal[
        "slot_commit", "stock_confirm", "evidence_commit", "report_publish", "block"
    ]
    actor: Literal["system", "user", "recollect_job"]
    payload_ref: NonBlankStr
    created_at: AwareDatetime

    @model_validator(mode="after")
    def enforce_version_advance(self):
        if self.to_version <= self.from_version:
            raise ValueError("to_version은 from_version보다 커야 함")
        return self


class ConflictRecord(_ContractModel):
    conflict_id: ULID
    slot_id: SlotId
    claim_id_a: ULID
    claim_id_b: ULID
    detected_by: Literal["rule"] = "rule"
    resolved_claim_id: ULID | None = None

    @model_validator(mode="after")
    def enforce_distinct_claims(self):
        # 🆕 v2.2 S-5: 같은 Claim 이 자기 자신과 충돌할 수 없다.
        #    resolved_claim_id 를 a/b 로 제한하지 않는 이유:
        #    HITL 에서 사용자가 제3의 답을 말하면 새 Claim 이 승자가 될 수 있다.
        #    그 케이스를 스키마가 막으면 정당한 충돌 해소 경로가 잘린다.
        if self.claim_id_a == self.claim_id_b:
            raise ValueError("claim_id_a와 claim_id_b는 서로 달라야 함 (v2.2 S-5)")
        return self
