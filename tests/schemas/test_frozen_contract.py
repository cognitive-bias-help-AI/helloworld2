"""P0-1 · `app/schemas/frozen.py` 계약 회귀.

이 파일이 고정하는 것은 세 가지다.

  거부  스키마가 막아야 하는 입력이 실제로 ValidationError 로 막히는가
  통과  🔴 **의도적으로 안 막기로 한 것이 여전히 통과하는가** (과잉 조임 검사)
  구조  필드 순서 · 금지 필드 부재 · 모델/enum 개수

🔴 테스트가 실패하면 테스트를 고치지 말고 보고한다. `frozen.py` 는 FROZEN 이다.

■ 건수 산술 (카드 헤더의 "30건" 은 이전 판의 잔재다 — T3 §4 P0-1 은 38건이라 적었다)
    번호 1~38 = 38건
    + 6c    = 39 줄
    - 15(1줄) + 15의 실제 5건 = 43개 개별 단언
  아래 REJECT 는 43건이고 `test_건수_산술` 이 그 값을 고정한다.
  건수를 바꾸려면 카드를 먼저 고쳐라. 테스트 숫자만 맞추지 마라.
"""
from __future__ import annotations

import py_compile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas import frozen as m

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 8, 13, 9, 0, tzinfo=KST)
NAIVE = datetime(2026, 8, 13, 9, 0)
SHA = "a" * 64
FROZEN_PATH = Path(m.__file__)


def U(i: int) -> str:
    """유효한 ULID. 패턴은 ^[0-7][0-9A-HJKMNP-TV-Z]{25}$ (I·L·O·U 없음)."""
    return f"01K5ZTQ9X7WPCVN2M4H8JRAB{i}D"


E1, E2, E3 = U(1), U(2), U(3)
C1, C2 = U(4), U(5)


# ══════════════════════════════════════════════════════════════════
# 최소 유효 생성자 — 여기서 만든 것은 전부 통과해야 한다.
# 거부 케이스는 이 기본값에서 **한 군데만** 바꾼다. 그래야 무엇이 원인인지 확실하다.
# ══════════════════════════════════════════════════════════════════
def claim(**kw: Any) -> m.Claim:
    d: dict[str, Any] = dict(
        claim_id=C1, slot_id=3, user_text_span="영업이익이 계속 늘고 있다",
        span_offset=(0, 13), normalized_proposition="영업이익이 증가 추세다",
        verifiable=True, origin=m.SourceTrace.LLM_EXTRACTION, created_at=NOW,
    )
    return m.Claim(**(d | kw))


def query(**kw: Any) -> m.Query:
    d: dict[str, Any] = dict(
        query_id=U(6), scope="claim", claim_id=C1, intent="verify", provider="dart",
        endpoint="/api/fnlttSinglAcntAll.json", params={"corp_code": "00126380"},
        created_at=NOW,
    )
    return m.Query(**(d | kw))


def test_Claim은_같은_slot의_서로_다른_주장_복수를_허용한다() -> None:
    demand = claim(
        claim_id=C1,
        slot_id=4,
        user_text_span="HBM 수요가 증가한다",
        span_offset=(0, 12),
        normalized_proposition="HBM 수요가 증가한다",
    )
    supply = claim(
        claim_id=C2,
        slot_id=4,
        user_text_span="HBM 공급이 부족하다",
        span_offset=(13, 25),
        normalized_proposition="HBM 공급이 부족하다",
    )

    assert demand.slot_id == supply.slot_id == 4
    assert demand.claim_id != supply.claim_id


def draft(**kw: Any) -> m.EvidenceDraft:
    d: dict[str, Any] = dict(
        source_type="dart", source_ref="20250814000123",
        raw_span="2025년 3분기 연결기준 영업이익 9,178,955백만원",
        span_scope="structured_field",
    )
    return m.EvidenceDraft(**(d | kw))


def evidence(**kw: Any) -> m.Evidence:
    d: dict[str, Any] = dict(
        evidence_id=E1, source_type="dart", source_ref="20250814000123",
        fetched_at=NOW, raw_span="영업이익 9,178,955백만원",
        span_scope="structured_field", content_sha256=SHA,
        provider_request_id=U(7), as_of=NOW,
    )
    return m.Evidence(**(d | kw))


def numeric(**kw: Any) -> m.NumericCheck:
    d: dict[str, Any] = dict(
        metric="operating_profit", claimed="9,178,955백만원", observed=9178955000000.0,
        unit="KRW", period="2025Q3", result="consistent", evidence_id=E1,
    )
    return m.NumericCheck(**(d | kw))


def eval_draft(**kw: Any) -> m.ClaimEvaluationDraft:
    d: dict[str, Any] = dict(
        citations=[], support_evidence_ids=[], oppose_evidence_ids=[],
        neutral_evidence_ids=[], unknown_evidence_ids=[], verdict="unverifiable",
        missing_dimensions=[], uncertainty_codes=[],
    )
    return m.ClaimEvaluationDraft(**(d | kw))


def evaluation(**kw: Any) -> m.ClaimEvaluation:
    d: dict[str, Any] = dict(
        claim_evaluation_id=U(8), claim_id=C1, citations=[],
        support_evidence_ids=[], oppose_evidence_ids=[], neutral_evidence_ids=[],
        unknown_evidence_ids=[], numeric_checks=[], verdict="unverifiable",
        missing_dimensions=[], uncertainty_codes=[], created_at=NOW,
    )
    return m.ClaimEvaluation(**(d | kw))


def theory(**kw: Any) -> m.TheoryNote:
    d: dict[str, Any] = dict(
        theory_id="TH-01", trigger=(3, "absent"), name="반증 가능성",
        definition="어떤 관측이 나오면 판단이 틀린 것으로 볼지 미리 정해두는 것.",
        observable_pattern="판단을 뒤집을 조건이 진술에 없었습니다.",
        non_diagnostic_warning="이것은 진단이 아닙니다.",
        source_refs=["Popper(1959)"],
    )
    return m.TheoryNote(**(d | kw))


def alert(**kw: Any) -> m.Alert:
    d: dict[str, Any] = dict(
        run_id="run-1", level=m.AlertLevel.HIGH, path=m.AlertPath.WEBHOOK,
        user_message="검증을 완료하지 못했습니다.", created_at=NOW,
    )
    return m.Alert(**(d | kw))


def stock(**kw: Any) -> m.StockCandidate:
    d: dict[str, Any] = dict(
        code="005930", name="삼성전자", market="KOSPI",
        match_kind="exact_code", score=1.0,
    )
    return m.StockCandidate(**(d | kw))


def usage(**kw: Any) -> m.Usage:
    d: dict[str, Any] = dict(
        model_slot="LARGE", prompt_tokens=1000, output_tokens=500, ctx_chars=4500,
    )
    return m.Usage(**(d | kw))


def state_change(**kw: Any) -> m.StateChange:
    d: dict[str, Any] = dict(
        change_id=U(9), run_id="run-1", from_version=3, to_version=4,
        change_type="slot_commit", actor="system", payload_ref="ref", created_at=NOW,
    )
    return m.StateChange(**(d | kw))


def collection(**kw: Any) -> m.CollectionResult:
    d: dict[str, Any] = dict(
        source="dart", status=m.NodeStatus.OK, items_fetched=7,
        items_adopted=5, items_deduped=2, queries_run=3,
    )
    return m.CollectionResult(**(d | kw))


def conflict(**kw: Any) -> m.ConflictRecord:
    d: dict[str, Any] = dict(conflict_id=U(0), slot_id=5, claim_id_a=C1, claim_id_b=C2)
    return m.ConflictRecord(**(d | kw))


def link(**kw: Any) -> m.EvidenceQueryLink:
    return m.EvidenceQueryLink(**({"evidence_id": E1, "query_id": U(6)} | kw))


def cite(eid: str) -> m.CitationRef:
    return m.CitationRef(evidence_id=eid, span="영업이익 9,178,955백만원")


# ══════════════════════════════════════════════════════════════════
# A. 거부 43건
#
# 🔴 각 케이스는 (에러 타입, 메시지 조각)까지 고정한다.
#    ValidationError 만 잡으면 **의도한 검증자가 아닌 다른 이유**로 막혀도 초록불이 된다.
#    예: 기본값을 하나 빠뜨리면 "Field required" 로 통과해버리고,
#        정작 막으려던 검증자는 한 번도 실행되지 않는다. 커버리지가 조용히 사라진다.
#    메시지 조각으로 고정해도 안전한 이유: 그 문구는 frozen.py 안에 있고
#    frozen.py 는 3인 approve 없이 못 바꾼다.
# ══════════════════════════════════════════════════════════════════
_EXTRA = ("extra_forbidden", "Extra inputs are not permitted")
_ULID = ("string_pattern_mismatch", "^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
_KRX = ("string_pattern_mismatch", "^[0-9]{4}[0-9A-Z]{2}$")
_BLANK = ("string_too_short", "at least 1 character")

REJECT: list[tuple[str, str, Any, tuple[str, str]]] = [
    ("01", "Query scope=claim 인데 claim_id=None",
     lambda: query(scope="claim", claim_id=None),
     ("value_error", "claim_id 필수")),
    ("02", "Query scope=stock 인데 claim_id 가 채워짐",
     lambda: query(scope="stock", claim_id=C1),
     ("value_error", "claim_id를 채울 수 없다")),
    ("03", "OpposeBlock unverified 인데 count 가 채워짐",
     lambda: m.OpposeBlock(status="unverified", count=0, reason=m.ReasonCode.RATE_LIMIT),
     ("value_error", "count를 채울 수 없다")),
    ("04", "OpposeBlock verified 인데 count=None",
     lambda: m.OpposeBlock(status="verified", count=None, queries=["q"]),
     ("value_error", "count는 필수")),
    ("05", "Finding kind=mismatch 인데 citation 0건",
     lambda: m.Finding(finding_id=U(2), slot_id=3, kind="mismatch",
                       citations=[], created_at=NOW),
     ("value_error", "citation 최소 1개")),
    ("06", "StockCandidate 5자리 종목코드", lambda: stock(code="00593"), _KRX),
    ("06c", "StockCandidate 전부 영문 종목코드", lambda: stock(code="ABCDEF"), _KRX),
    ("07", "Alert CRITICAL 인데 경로가 WEBHOOK",
     lambda: alert(level=m.AlertLevel.CRITICAL, path=m.AlertPath.WEBHOOK),
     ("value_error", "CRITICAL 알람은 인프로세스 동기 경로만")),
    ("08", "Alert user_message 가 빈 문자열", lambda: alert(user_message=""), _BLANK),
    ("09", "TheoryNote non_diagnostic_warning 이 빈 문자열",
     lambda: theory(non_diagnostic_warning=""), _BLANK),
    ("10", "EvidenceDraft raw_span 501자", lambda: draft(raw_span="가" * 501),
     ("string_too_long", "at most 500 characters")),
    ("11", "Claim created_at 이 naive datetime", lambda: claim(created_at=NAIVE),
     ("timezone_aware", "timezone info")),
    ("12", "Claim span_offset end <= start", lambda: claim(span_offset=(5, 5)),
     ("value_error", "start보다 커야 함")),
    ("13", "EvidenceQueryLink evidence_id 가 ULID 아님",
     lambda: link(evidence_id="claim-1"), _ULID),
    ("14", "EvidenceQueryLink 에 선언되지 않은 필드", lambda: link(extra_field="x"), _EXTRA),
    # 15 — canonical 5필드를 각각 주입. EvidenceDraft 에는 아예 존재하지 않는다.
    ("15a", "EvidenceDraft 에 evidence_id 주입", lambda: draft(evidence_id=E1), _EXTRA),
    ("15b", "EvidenceDraft 에 provider_request_id 주입",
     lambda: draft(provider_request_id=U(7)), _EXTRA),
    ("15c", "EvidenceDraft 에 content_sha256 주입", lambda: draft(content_sha256=SHA), _EXTRA),
    ("15d", "EvidenceDraft 에 fetched_at 주입", lambda: draft(fetched_at=NOW), _EXTRA),
    ("15e", "EvidenceDraft 에 as_of 주입", lambda: draft(as_of=NOW), _EXTRA),
    ("16", "ClaimEvaluation support 와 oppose 에 같은 evidence_id",
     lambda: evaluation(support_evidence_ids=[E1], oppose_evidence_ids=[E1]),
     ("value_error", "상호배타적이어야 함")),
    ("17", "ClaimEvaluation neutral 과 unknown 에 같은 evidence_id",
     lambda: evaluation(neutral_evidence_ids=[E1], unknown_evidence_ids=[E1]),
     ("value_error", "상호배타적이어야 함")),
    ("18", "citation 이 선언되지 않은 evidence 를 참조",
     lambda: evaluation(citations=[cite(E2)], unknown_evidence_ids=[E1]),
     ("value_error", "citation은 선언된 evidence 집합만")),
    ("19", "Usage cached_input_tokens > prompt_tokens",
     lambda: usage(prompt_tokens=10, cached_input_tokens=11),
     ("value_error", "prompt_tokens를 초과할 수 없음")),
    ("20", "StateChange to_version 이 전진하지 않음",
     lambda: state_change(from_version=3, to_version=3),
     ("value_error", "from_version보다 커야 함")),
    ("21", "NumericCheck 가 선언되지 않은 evidence 를 참조",
     lambda: evaluation(numeric_checks=[numeric(evidence_id=E2)],
                        unknown_evidence_ids=[E1]),
     ("value_error", "NumericCheck는 선언된 evidence 집합만")),
    ("22", "ClaimEvaluationDraft 에 numeric_checks (권한 밖 필드)",
     lambda: eval_draft(numeric_checks=[]), _EXTRA),
    ("23", "CollectionResult adopted+deduped > fetched",
     lambda: collection(items_fetched=5, items_adopted=4, items_deduped=3),
     ("value_error", "items_fetched를 초과할 수 없음")),
    ("24", "missing_dimensions 에 중복 slot_id",
     lambda: evaluation(missing_dimensions=[3, 3]),
     ("value_error", "중복 slot_id를 둘 수 없음")),
    ("25", "Claim slot_id=9 (상한 8 초과)", lambda: claim(slot_id=9),
     ("less_than_equal", "less than or equal to 8")),
    ("26", "Claim claim_id 가 소문자 ULID", lambda: claim(claim_id=C1.lower()), _ULID),
    ("27", "Query params 누락", lambda: m.Query(
        query_id=U(6), scope="claim", claim_id=C1, intent="verify", provider="dart",
        endpoint="/api/x.json", created_at=NOW),
     ("missing", "Field required")),
    # ── v2.2 델타 ─────────────────────────────────────────────────
    ("28", "🆕 OpposeBlock verified 인데 queries=None (검색 안 하고 검증 주장)",
     lambda: m.OpposeBlock(status="verified", count=0, queries=None),
     ("value_error", "queries 최소 1건 필수")),
    ("29", "🆕 OpposeBlock unverified 인데 reason=None (왜인지 없이 '확인 못함')",
     lambda: m.OpposeBlock(status="unverified", reason=None),
     ("value_error", "reason 필수")),
    ("30", "🆕 NumericCheck inconsistent 인데 observed=None (무엇과 대조했나)",
     lambda: numeric(result="inconsistent", observed=None),
     ("value_error", "observed 필수")),
    ("31", "🆕 NumericCheck no_data 인데 observed 가 채워짐",
     lambda: numeric(result="no_data", observed=1.0),
     ("value_error", "observed가 채워져 있음")),
    ("32", "🆕 Draft verdict=support 인데 지지 근거 0건",
     lambda: eval_draft(verdict="support"),
     ("value_error", "verdict='support'는 support_evidence_ids 최소 1건")),
    ("33", "🆕 ClaimEvaluation verdict=contradicted 인데 반대 근거·검산 0건",
     lambda: evaluation(verdict="contradicted"),
     ("value_error", "verdict='contradicted'는 oppose_evidence_ids 최소 1건")),
    ("34", "🆕 Claim 이 자기 자신으로 supersede", lambda: claim(superseded_by=C1),
     ("value_error", "자기 자신으로 supersede될 수 없음")),
    ("35", "🆕 ConflictRecord claim_id_a == claim_id_b",
     lambda: conflict(claim_id_b=C1),
     ("value_error", "서로 달라야 함")),
    ("36", "🆕 source_url 이 javascript: 스킴",
     lambda: draft(source_url="javascript:alert(1)"),
     ("string_pattern_mismatch", "^https?://")),
    ("37", "🆕 ClaimStanceDraft 에 같은 evidence_id 2건", lambda: m.ClaimStanceDraft(
        stances=[m.ClaimEvidenceDraft(evidence_id=E1, stance="support"),
                 m.ClaimEvidenceDraft(evidence_id=E1, stance="oppose")]),
     ("value_error", "stance를 두 번 매길 수 없음")),
    ("38", "🆕 ClaimEvidenceDraft 가 stance_source 를 스스로 선언",
     lambda: m.ClaimEvidenceDraft(evidence_id=E1, stance="support", stance_source="rule"),
     _EXTRA),
]


@pytest.mark.parametrize(
    "case_id,label,fn,expect", REJECT, ids=[c[0] for c in REJECT]
)
def test_거부(case_id: str, label: str, fn: Any, expect: tuple[str, str]) -> None:
    exp_type, exp_fragment = expect
    with pytest.raises(ValidationError) as exc:
        fn()
    errors = exc.value.errors()
    types = {e["type"] for e in errors}
    msgs = " | ".join(e["msg"] for e in errors)
    assert exp_type in types, (
        f"{case_id} 이 막히긴 했는데 이유가 다르다. "
        f"기대={exp_type} 실제={sorted(types)} :: {msgs}"
    )
    assert exp_fragment in msgs, (
        f"{case_id} 의 에러 메시지가 의도한 검증자의 것이 아니다. "
        f"기대 조각='{exp_fragment}' 실제='{msgs}'"
    )


# ══════════════════════════════════════════════════════════════════
# B. 통과 12건 — 🔴 과잉 조임 검사
#
# P1~P5: "거부되는가" 가 아니라 "통과하는가" 를 보는 유일한 케이스다.
#        종목코드 정규식을 ^\d{6}$ 로 조이다 우선주를 잘라낸 사고가 실제로 있었다.
# P8~P12: v2.2 가 verdict/NumericCheck 를 조였다. 정당한 공집합까지 막으면 재발한다.
# ══════════════════════════════════════════════════════════════════
PASS: list[tuple[str, str, Any]] = [
    ("P1", "삼성전자 005930", lambda: stock(code="005930")),
    ("P2", "🔴 코리아써키트2우B 00781K", lambda: stock(code="00781K", name="코리아써키트2우B")),
    ("P3", "🔴 SK우 03473K", lambda: stock(code="03473K", name="SK우")),
    ("P4", "🔴 한진칼우 18064K", lambda: stock(code="18064K", name="한진칼우")),
    ("P5", "🔴 삼성물산우B 02826K", lambda: stock(code="02826K", name="삼성물산우B")),
    ("P6", "EvidenceDraft raw_span 정확히 500자", lambda: draft(raw_span="가" * 500)),
    ("P7", "OpposeBlock 검색은 했고 반대 근거 0건",
     lambda: m.OpposeBlock(status="verified", count=0, queries=["삼성전자 악재"])),
    ("P8", "verdict=unsupported + 모든 버킷 공집합",
     lambda: evaluation(verdict="unsupported")),
    ("P9", "verdict=unverifiable + 모든 버킷 공집합",
     lambda: evaluation(verdict="unverifiable")),
    ("P10", "verdict=support 를 규칙 검산만으로 지지",
     lambda: evaluation(verdict="support", neutral_evidence_ids=[E1],
                        numeric_checks=[numeric(result="consistent")])),
    ("P11", "NumericCheck not_comparable + observed=None",
     lambda: numeric(result="not_comparable", observed=None)),
    ("P12", "Evidence published_at > as_of (정정공시·장중 데이터)",
     lambda: evidence(published_at=NOW + timedelta(days=1), as_of=NOW)),
]


@pytest.mark.parametrize("case_id,label,fn", PASS, ids=[c[0] for c in PASS])
def test_통과(case_id: str, label: str, fn: Any) -> None:
    """🔴 여기가 빨개지면 스키마가 정당한 케이스를 잘라내고 있다는 뜻이다.
    테스트를 고치지 말고 보고해라."""
    assert fn() is not None


@pytest.mark.parametrize("code", ["005930", "00088K", "03473K", "0126Z0", "0001A0"])
def test_KRXCode는_관측된_6자리_정규코드를_허용한다(code: str) -> None:
    assert stock(code=code).code == code


@pytest.mark.parametrize(
    "code", ["A126Z0", "01A6Z0", "0126z0", "0126-0", "126Z0", "00126Z0"]
)
def test_KRXCode는_관측범위_밖_형식을_fail_closed한다(code: str) -> None:
    with pytest.raises(ValidationError):
        stock(code=code)


# ══════════════════════════════════════════════════════════════════
# C. 구조 검사 13건
# ══════════════════════════════════════════════════════════════════
def _order(model: type[BaseModel], a: str, b: str) -> bool:
    names = list(model.model_fields)
    return names.index(a) < names.index(b)


def test_S1_canonical_은_인용을_verdict_보다_먼저_쓴다() -> None:
    """D-31. 판정 전에 인용을 먼저 쓰게 해서 근거 없는 결론을 줄인다."""
    assert _order(m.ClaimEvaluation, "citations", "verdict")


def test_S2_draft_도_인용이_verdict_보다_앞이다() -> None:
    assert _order(m.ClaimEvaluationDraft, "citations", "verdict")


def test_S3_free_text_summary_가_두_모델_모두에_없다() -> None:
    """자유 서술 필드가 있으면 인용 없는 문장이 리포트로 샌다."""
    assert "free_text_summary" not in m.ClaimEvaluation.model_fields
    assert "free_text_summary" not in m.ClaimEvaluationDraft.model_fields


def test_S4_GuardInput_에_금지_필드가_없다() -> None:
    assert not ({"findings", "evidences", "claims"} & set(m.GuardInput.model_fields))


def test_S5_Evidence_에_query_id_가_없다() -> None:
    """F4. Evidence 가 query_id 를 들면 같은 근거가 쿼리 수만큼 복제된다."""
    assert "query_id" not in m.Evidence.model_fields


def test_S6_SourceTrace_SURVEY() -> None:
    assert m.SourceTrace.SURVEY.value == "survey"


def test_S7_EvidenceDraft_에_canonical_5필드가_전부_없다() -> None:
    canonical = {"evidence_id", "provider_request_id", "content_sha256",
                 "fetched_at", "as_of"}
    assert not (canonical & set(m.EvidenceDraft.model_fields))


def test_S8_ClaimEvaluationDraft_에_권한_밖_필드가_없다() -> None:
    forbidden = {"numeric_checks", "claim_id", "claim_evaluation_id", "created_at"}
    assert not (forbidden & set(m.ClaimEvaluationDraft.model_fields))


def test_S9_enum_개수() -> None:
    assert len(list(m.ReasonCode)) == 27
    assert len(list(m.SourceTrace)) == 7


def test_S10_PROVIDER_SOURCE_TYPE() -> None:
    """이 표가 없으면 세 사람이 각자 매핑을 정하고, naver 결과가
    source_type="dart" 로 집계돼도 스키마는 통과한다."""
    assert m.PROVIDER_SOURCE_TYPE == {"dart": "dart", "naver": "news", "kiwoom": "quote"}


def test_S11_ClaimEvidenceDraft_에_시스템_소유_필드가_없다() -> None:
    """n7 LLM 이 stance_source="rule" 을 스스로 선언할 수 없어야 한다."""
    assert not ({"stance_source", "claim_id", "query_id"} & set(m.ClaimEvidenceDraft.model_fields))


def test_S12_BaseModel_파생_30개() -> None:
    """v2.1a 26 + Draft 4 = 30. 늘거나 줄면 계약이 바뀐 것이다."""
    derived = {
        n for n in dir(m)
        if isinstance(getattr(m, n), type)
        and issubclass(getattr(m, n), BaseModel)
        and n not in ("BaseModel", "_ContractModel")
    }
    assert len(derived) == 30, sorted(derived)


def test_S13_py_compile() -> None:
    py_compile.compile(str(FROZEN_PATH), doraise=True)


# ══════════════════════════════════════════════════════════════════
# D. 건수 고정 — 카드와 어긋나면 여기서 걸린다
# ══════════════════════════════════════════════════════════════════
def test_건수_산술() -> None:
    """카드 번호 1~38(38) + 6c(1) - 15줄(1) + 15의 실제 5건 = 43.

    통과 12 · 구조 13 은 카드 그대로다.
    숫자를 바꾸려면 카드를 먼저 고쳐라. 테스트만 맞추면 커버리지가 조용히 준다.
    """
    assert len(REJECT) == 43
    assert len(PASS) == 12
    assert len({c[0] for c in REJECT}) == len(REJECT), "중복 case_id"
    assert len({c[0] for c in PASS}) == len(PASS), "중복 case_id"


def test_v2_2_델타_11건이_전부_들어있다() -> None:
    """DDR §2.1 이 새로 닫은 경로. 하나라도 빠지면 '거짓 인쇄' 구멍이 다시 열린다.
    28~38 은 전부 v2.2 에서 신설된 거부다."""
    delta_ids = {str(i) for i in range(28, 39)}
    assert delta_ids <= {c[0] for c in REJECT}
