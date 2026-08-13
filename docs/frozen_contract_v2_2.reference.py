"""v2.1d ↔ v2.2 동시 실행 검증.

A) 회귀 세트  — v2.1d 에서 통과하던 불변식이 v2.2 에서도 동일하게 동작하는가 (약화 0건)
B) 델타 세트  — v2.2 가 새로 막기로 한 6건이 v2.1d 에서는 뚫려 있고 v2.2 에서는 막히는가
C) 개방 세트  — 의도적으로 안 막기로 한 것들이 v2.2 에서도 여전히 통과하는가 (과잉 조임 0건)
"""
import importlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/home/claude/ddr")
from pydantic import ValidationError

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 8, 13, 9, 0, tzinfo=KST)
NAIVE = datetime(2026, 8, 13, 9, 0)
U = [f"01K5ZTQ9X7WPCVN2M4H8JRAB{i}D" for i in range(6)]
SHA = "a" * 64


def build_cases(m):
    """m = 스키마 모듈. (id, 라벨, 호출, 기대) 목록을 돌려준다."""
    ST, RC, NS, AL, AP = m.SourceTrace, m.ReasonCode, m.NodeStatus, m.AlertLevel, m.AlertPath

    def claim(**kw):
        d = dict(claim_id=U[0], slot_id=1, user_text_span="반토막", span_offset=(0, 3),
                 normalized_proposition="p", verifiable=True, origin=ST.CHAT_EXPLICIT,
                 created_at=NOW)
        d.update(kw)
        return m.Claim(**d)

    def draft(**kw):
        d = dict(source_type="dart", source_ref="r1", raw_span="영업이익 9,178,955백만원",
                 span_scope="structured_field")
        d.update(kw)
        return m.EvidenceDraft(**d)

    def edraft(**kw):
        d = dict(citations=[], support_evidence_ids=[], oppose_evidence_ids=[],
                 neutral_evidence_ids=[], unknown_evidence_ids=[], verdict="unverifiable",
                 missing_dimensions=[], uncertainty_codes=[])
        d.update(kw)
        return m.ClaimEvaluationDraft(**d)

    def ev(**kw):
        d = dict(claim_evaluation_id=U[0], claim_id=U[1], citations=[],
                 support_evidence_ids=[], oppose_evidence_ids=[], neutral_evidence_ids=[],
                 unknown_evidence_ids=[], numeric_checks=[], verdict="unverifiable",
                 missing_dimensions=[], uncertainty_codes=[], created_at=NOW)
        d.update(kw)
        return m.ClaimEvaluation(**d)

    def nc(**kw):
        d = dict(metric="operating_profit", claimed="9178955", observed=9178955.0,
                 result="consistent", evidence_id=U[0])
        d.update(kw)
        return m.NumericCheck(**d)

    A = [  # 회귀 — 두 버전 모두 동일해야 함
        ("A01", 'Query(claim, claim_id=None)', lambda: m.Query(query_id=U[0], scope="claim", intent="verify", provider="dart", endpoint="e", params={}, created_at=NOW), "reject"),
        ("A02", 'Query(stock, claim_id=set)', lambda: m.Query(query_id=U[0], scope="stock", claim_id=U[1], intent="counter", provider="naver", endpoint="e", params={}, created_at=NOW), "reject"),
        ("A03", 'OpposeBlock(unverified, count=0)', lambda: m.OpposeBlock(status="unverified", count=0, reason=RC.RATE_LIMIT), "reject"),
        ("A04", 'OpposeBlock(verified, count=None)', lambda: m.OpposeBlock(status="verified", count=None, queries=["q"]), "reject"),
        ("A05", 'Finding(mismatch, citations=[])', lambda: m.Finding(finding_id=U[0], slot_id=1, kind="mismatch", citations=[], created_at=NOW), "reject"),
        ("A06", 'StockCandidate("00593")', lambda: m.StockCandidate(code="00593", name="x", market="KOSPI", match_kind="exact_code", score=1.0), "reject"),
        ("A07", 'StockCandidate("03473K") SK우', lambda: m.StockCandidate(code="03473K", name="SK우", market="KOSPI", match_kind="exact_code", score=1.0), "accept"),
        ("A08", 'StockCandidate("00781K")', lambda: m.StockCandidate(code="00781K", name="코리아써키트2우B", market="KOSPI", match_kind="exact_code", score=1.0), "accept"),
        ("A09", 'StockCandidate("18064K")', lambda: m.StockCandidate(code="18064K", name="한진칼우", market="KOSPI", match_kind="exact_code", score=1.0), "accept"),
        ("A10", 'StockCandidate("02826K")', lambda: m.StockCandidate(code="02826K", name="삼성물산우B", market="KOSPI", match_kind="exact_code", score=1.0), "accept"),
        ("A11", 'StockCandidate("ABCDEF")', lambda: m.StockCandidate(code="ABCDEF", name="x", market="KOSPI", match_kind="exact_code", score=1.0), "reject"),
        ("A12", 'Alert(CRITICAL, WEBHOOK)', lambda: m.Alert(run_id="r", level=AL.CRITICAL, path=AP.WEBHOOK, user_message="u", created_at=NOW), "reject"),
        ("A13", 'Alert(user_message="")', lambda: m.Alert(run_id="r", level=AL.HIGH, path=AP.WEBHOOK, user_message="", created_at=NOW), "reject"),
        ("A14", 'TheoryNote(warning="")', lambda: m.TheoryNote(theory_id="t", trigger=(1, "absent"), name="n", definition="d", observable_pattern="o", non_diagnostic_warning="", source_refs=["s"]), "reject"),
        ("A15", 'EvidenceDraft(raw_span=501)', lambda: draft(raw_span="가" * 501), "reject"),
        ("A16", 'EvidenceDraft(raw_span=500)', lambda: draft(raw_span="가" * 500), "accept"),
        ("A17", 'Claim(created_at=naive)', lambda: claim(created_at=NAIVE), "reject"),
        ("A18", 'Claim(span_offset=(5,5))', lambda: claim(span_offset=(5, 5)), "reject"),
        ("A19", 'EvidenceQueryLink(비ULID)', lambda: m.EvidenceQueryLink(evidence_id="claim-1", query_id=U[0]), "reject"),
        ("A20", 'EvidenceQueryLink(extra)', lambda: m.EvidenceQueryLink(evidence_id=U[0], query_id=U[1], x="y"), "reject"),
        ("A21", 'EvidenceDraft(evidence_id)', lambda: draft(evidence_id=U[0]), "reject"),
        ("A22", 'EvidenceDraft(as_of)', lambda: draft(as_of=NOW), "reject"),
        ("A23", 'EvidenceDraft(content_sha256)', lambda: draft(content_sha256=SHA), "reject"),
        ("A24", 'EvidenceDraft(provider_request_id)', lambda: draft(provider_request_id=U[0]), "reject"),
        ("A25", 'EvidenceDraft(fetched_at)', lambda: draft(fetched_at=NOW), "reject"),
        ("A26", 'Draft support∩oppose', lambda: edraft(support_evidence_ids=[U[0]], oppose_evidence_ids=[U[0]], verdict="support"), "reject"),
        ("A27", 'Draft neutral∩unknown', lambda: edraft(neutral_evidence_ids=[U[0]], unknown_evidence_ids=[U[0]]), "reject"),
        ("A28", 'Draft 미선언 인용', lambda: edraft(citations=[m.CitationRef(evidence_id=U[2], span="s")], support_evidence_ids=[U[0]], verdict="support"), "reject"),
        ("A29", 'Usage(prompt=10,cached=11)', lambda: m.Usage(model_slot="LARGE", prompt_tokens=10, cached_input_tokens=11, output_tokens=1, ctx_chars=1), "reject"),
        ("A30", 'StateChange(3→3)', lambda: m.StateChange(change_id=U[0], run_id="r", from_version=3, to_version=3, change_type="block", actor="system", payload_ref="p", created_at=NOW), "reject"),
        ("A31", 'Canonical NumericCheck 미선언 참조', lambda: ev(support_evidence_ids=[U[0]], verdict="support", numeric_checks=[nc(evidence_id=U[2])]), "reject"),
        ("A32", 'Canonical 4분할 정상 전체', lambda: ev(support_evidence_ids=[U[0]], oppose_evidence_ids=[U[1]], neutral_evidence_ids=[U[2]], unknown_evidence_ids=[U[3]], citations=[m.CitationRef(evidence_id=U[0], span="s")], numeric_checks=[nc(evidence_id=U[1])], verdict="partial_support"), "accept"),
        ("A33", 'Draft numeric_checks 주입', lambda: edraft(numeric_checks=[]), "reject"),
        ("A34", 'CollectionResult 합계 초과', lambda: m.CollectionResult(source="dart", status=NS.OK, items_fetched=3, items_adopted=2, items_deduped=2), "reject"),
        ("A35", 'missing_dimensions 중복', lambda: edraft(missing_dimensions=[1, 1]), "reject"),
        ("A36", 'SlotId=9', lambda: claim(slot_id=9), "reject"),
        ("A37", 'ULID 소문자', lambda: claim(claim_id=U[0].lower()), "reject"),
        ("A38", 'Query.params 누락', lambda: m.Query(query_id=U[0], scope="stock", intent="context", provider="kiwoom", endpoint="e", created_at=NOW), "reject"),
        ("A39", 'OpposeBlock(verified, count=0, queries=[q])', lambda: m.OpposeBlock(status="verified", count=0, queries=["삼성전자 실적 악화"]), "accept"),
    ]

    B = [  # v2.2 델타 — v2.1d: 통과 / v2.2: 거부 여야 함
        ("B1", "OpposeBlock(verified, queries=None) — 검색 안 하고 '검증했다'", lambda: m.OpposeBlock(status="verified", count=3)),
        ("B2", "OpposeBlock(unverified, reason=None) — 이유 없이 '확인 못함'", lambda: m.OpposeBlock(status="unverified")),
        ("B3", "Draft verdict='support' + support 버킷 공집합", lambda: edraft(verdict="support", unknown_evidence_ids=[U[0]])),
        ("B4", "Canonical verdict='contradicted' + oppose 공집합·검산 없음", lambda: ev(verdict="contradicted", unknown_evidence_ids=[U[0]])),
        ("B5", "NumericCheck(inconsistent, observed=None)", lambda: nc(result="inconsistent", observed=None)),
        ("B6", "NumericCheck(no_data, observed=1.0)", lambda: nc(result="no_data", observed=1.0)),
        ("B7", "Claim.superseded_by == 자기 자신", lambda: claim(superseded_by=U[0])),
        ("B8", "ConflictRecord(a == b)", lambda: m.ConflictRecord(conflict_id=U[0], slot_id=1, claim_id_a=U[1], claim_id_b=U[1])),
        ("B9", "source_url='javascript:alert(1)'", lambda: draft(source_url="javascript:alert(1)")),
    ]

    C = [  # 개방 — 두 버전 모두 통과해야 함 (v2.2 가 과하게 조이지 않았는지)
        ("C1", "verdict='unsupported' + 모든 버킷 공집합", lambda: ev(verdict="unsupported")),
        ("C2", "verdict='unverifiable' + 모든 버킷 공집합", lambda: ev(verdict="unverifiable")),
        ("C3", "verdict='support' 인데 근거는 규칙 검산뿐", lambda: ev(verdict="support", neutral_evidence_ids=[U[0]], numeric_checks=[nc(evidence_id=U[0])])),
        ("C4", "NumericCheck(not_comparable, observed=None)", lambda: nc(result="not_comparable", observed=None)),
        ("C5", "NumericCheck(not_comparable, observed=1.0)", lambda: nc(result="not_comparable", observed=1.0)),
        ("C6", "OpposeBlock(unverified, reason, queries 있음) — 돌렸으나 실패", lambda: m.OpposeBlock(status="unverified", reason=RC.RATE_LIMIT, queries=["q1"])),
        ("C7", "published_at 이 as_of 이후 (정정공시·장중)", lambda: m.Evidence(evidence_id=U[0], source_type="dart", source_ref="r", published_at=datetime(2030, 1, 1, tzinfo=KST), fetched_at=NOW, raw_span="x", span_scope="structured_field", content_sha256=SHA, provider_request_id=U[1], as_of=NOW)),
        ("C8", "fetched_at 이 as_of 이전 (캐시 재사용)", lambda: m.Evidence(evidence_id=U[0], source_type="dart", source_ref="r", fetched_at=datetime(2020, 1, 1, tzinfo=KST), raw_span="x", span_scope="structured_field", content_sha256=SHA, provider_request_id=U[1], as_of=NOW)),
        ("C9", "ctx_chars=100000, prompt_tokens=1", lambda: m.Usage(model_slot="LARGE", prompt_tokens=1, output_tokens=1, ctx_chars=100000)),
        ("C10", "source_url='https://dart.fss.or.kr/...'", lambda: draft(source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250814000123")),
        ("C11", "source_url=None (키움)", lambda: draft(source_type="quote", source_url=None)),
        ("C12", "ConflictRecord(resolved=제3의 claim)", lambda: m.ConflictRecord(conflict_id=U[0], slot_id=1, claim_id_a=U[1], claim_id_b=U[2], resolved_claim_id=U[3])),
    ]
    return A, B, C


def run(fn):
    try:
        fn()
        return "accept", ""
    except ValidationError as e:
        return "reject", e.errors()[0]["msg"][:64]
    except Exception as e:
        return f"ERR:{type(e).__name__}", str(e)[:64]


mods = {v: importlib.import_module(f"app.schemas.frozen_{v}") for v in ("v2_1d", "v2_2")}
A1d, B1d, C1d = build_cases(mods["v2_1d"])
A22, B22, C22 = build_cases(mods["v2_2"])

print("=" * 108)
print("A) 회귀 세트 — v2.1d 와 v2.2 가 동일해야 함")
print("=" * 108)
bad = 0
for (i, lbl, f1, exp), (_, _, f2, _) in zip(A1d, A22):
    r1, _ = run(f1)
    r2, m2 = run(f2)
    ok = (r1 == exp) and (r2 == exp)
    bad += 0 if ok else 1
    print(f"{'✅' if ok else '🔴'} {i:<5}{lbl:<46}기대={exp:<7}v2.1d={r1:<8}v2.2={r2:<8}{m2 if not ok else ''}")
print(f"→ 회귀 {len(A1d)}건 중 불일치 {bad}건")

print("\n" + "=" * 108)
print("B) v2.2 델타 — v2.1d=통과(구멍) / v2.2=거부(닫힘) 이어야 함")
print("=" * 108)
bad_b = 0
for (i, lbl, f1), (_, _, f2) in zip(B1d, B22):
    r1, _ = run(f1)
    r2, m2 = run(f2)
    ok = (r1 == "accept") and (r2 == "reject")
    bad_b += 0 if ok else 1
    print(f"{'✅' if ok else '🔴'} {i:<4}{lbl:<52}v2.1d={r1:<8}v2.2={r2:<8}{m2[:52]}")
print(f"→ 델타 {len(B1d)}건 중 미달 {bad_b}건")

print("\n" + "=" * 108)
print("C) 개방 세트 — 두 버전 모두 통과해야 함 (과잉 조임 검사)")
print("=" * 108)
bad_c = 0
for (i, lbl, f1), (_, _, f2) in zip(C1d, C22):
    r1, _ = run(f1)
    r2, m2 = run(f2)
    ok = (r1 == "accept") and (r2 == "accept")
    bad_c += 0 if ok else 1
    print(f"{'✅' if ok else '🔴'} {i:<5}{lbl:<52}v2.1d={r1:<8}v2.2={r2:<8}{m2[:48]}")
print(f"→ 개방 {len(C1d)}건 중 과잉 조임 {bad_c}건")

print("\n" + "=" * 108)
m = mods["v2_2"]
S = [
    ("S1 ClaimEvaluation citations < verdict", list(m.ClaimEvaluation.model_fields).index("citations") < list(m.ClaimEvaluation.model_fields).index("verdict")),
    ("S2 Draft citations < verdict", list(m.ClaimEvaluationDraft.model_fields).index("citations") < list(m.ClaimEvaluationDraft.model_fields).index("verdict")),
    ("S3 free_text_summary 부재", "free_text_summary" not in m.ClaimEvaluation.model_fields),
    ("S4 GuardInput 금지필드 없음", not ({"findings", "evidences", "claims"} & set(m.GuardInput.model_fields))),
    ("S5 query_id not in Evidence", "query_id" not in m.Evidence.model_fields),
    ("S6 SourceTrace.SURVEY", m.SourceTrace.SURVEY.value == "survey"),
    ("S7 EvidenceDraft canonical 5필드 부재", not ({"evidence_id", "provider_request_id", "content_sha256", "fetched_at", "as_of"} & set(m.EvidenceDraft.model_fields))),
    ("S8 ReasonCode 27종", len(list(m.ReasonCode)) == 27),
    ("S9 PROVIDER_SOURCE_TYPE 3종", m.PROVIDER_SOURCE_TYPE == {"dart": "dart", "naver": "news", "kiwoom": "quote"}),
]
_names = lambda mod: {n for n in dir(mod) if isinstance(getattr(mod, n), type)
                      and issubclass(getattr(mod, n), mod.BaseModel)
                      and n not in ("BaseModel", "_ContractModel")}
_old, _new = _names(mods["v2_1d"]), _names(m)
S.append(("S10 기존 28개 모델 필드 집합·순서 완전 동일(변경 0)", all(
    list(getattr(m, n).model_fields) == list(getattr(mods["v2_1d"], n).model_fields) for n in _old)))
S.append(("S11 삭제된 모델 0개", not (_old - _new)))
S.append((f"S12 신설 모델 = {sorted(_new - _old)}", (_new - _old) == {"ClaimEvidenceDraft", "ClaimStanceDraft"}))
S.append(("S13 ClaimEvidenceDraft 에 stance_source/claim_id/query_id 부재",
          not ({"stance_source", "claim_id", "query_id"} & set(m.ClaimEvidenceDraft.model_fields))))
for name, ok in S:
    print(f"{'✅' if ok else '🔴'} {name}")
print(f"\n총평: 회귀 불일치 {bad} · 델타 미달 {bad_b} · 과잉 조임 {bad_c} · 구조 실패 {sum(1 for _, o in S if not o)}")
