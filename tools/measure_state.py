"""ReviewState 채널별 실측. 추정 금지 — 실제 모델을 만들어 JSON 직렬화한 바이트를 센다."""
import json
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from app.schemas import frozen as M  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 8, 13, 9, 0, tzinfo=KST)
def U(i: int) -> str:
    return f"01K5ZTQ9X7WPCVN2M4H8JR{i:02d}AB"[:26]


def b(x) -> int:
    return len(json.dumps(x, ensure_ascii=False, default=str).encode())


claim = M.Claim(
    claim_id=U(1), slot_id=3,
    user_text_span="영업이익이 계속 늘고 있어서 더 들고 갈 생각입니다",
    span_offset=(12, 40),
    normalized_proposition="해당 종목의 영업이익이 최근 분기 연속 증가하고 있다",
    verifiable=True, origin=M.SourceTrace.LLM_EXTRACTION, created_at=NOW,
)
claim_slim = {"claim_id": U(1), "slot_id": 3, "verifiable": True}
ce = M.ClaimEvidence(claim_id=U(1), evidence_id=U(2), stance="support",
                     stance_source="llm", confidence=0.82, query_id=U(3))
nr_full = M.NodeResult(node_name="n8_verify", status=M.NodeStatus.OK,
                       reason_code=None,
                       detail="claim 4건 중 4건 평가 완료, packet 12건 전량 분류됨",
                       retry_count=0, elapsed_ms=4820)
nr_slim = {"node": "n8_verify", "status": "OK", "reason_code": None, "elapsed_ms": 4820}
q = M.Query(query_id=U(4), scope="claim", claim_id=U(1), intent="verify", provider="dart",
            endpoint="/api/fnlttSinglAcntAll.json",
            params={"corp_code": "00126380", "bsns_year": "2025", "reprt_code": "11014",
                    "fs_div": "CFS", "crtfc_key": "0" * 40},
            created_at=NOW)
slot = {"slot_id": 3, "status": "present", "label": "반증 조건",
        "claim_id": U(1), "updated_by": "n3"}
conflict = {"conflict_id": U(5), "slot_id": 5, "claim_id_a": U(1), "claim_id_b": U(6),
            "detected_by": "rule", "resolved_claim_id": None}
stock = {"code": "005930", "name": "삼성전자", "market": "KOSPI",
         "is_delisted": False, "is_managed": False, "match_kind": "alias", "score": 0.97}
masked_short = {"text": "삼성전자 계속 들고 갈까요", "spans": [[0, 4]], "masked_count": 0}
masked = {"text": "삼성전자 영업이익이 계속 늘고 있어서 더 들고 갈 생각인데 괜찮을까요 " * 3,
          "spans": [[0, 4]], "masked_count": 0}
oppose = M.OpposeBlock(status="verified", count=2,
                       queries=["삼성전자 악재", "삼성전자 경쟁 점유율", "반도체 업황 규제"])
coll = {p: M.CollectionResult(source=s, status=M.NodeStatus.OK, items_fetched=7,
                              items_adopted=5, items_deduped=2, queries_run=3).model_dump()
        for p, s in [("dart", "dart"), ("naver", "news"), ("kiwoom", "quote")]}
counters = {"total_external_calls": 19, "total_llm_calls": 25,
            "hitl_reask": 1, "graph_recollect": 0, "verifiable_claims": 4}

UNIT = {
    "Claim 본문 1건": b(claim.model_dump()),
    "Claim 축약 1건": b(claim_slim),
    "claim_id 1건(리스트원소)": b(U(1)) + 1,
    "ClaimEvidence key 1건": b(f"{U(1)}:{U(2)}") + 1,
    "ClaimEvidence 본문 1건": b(ce.model_dump()),
    "NodeResult 전체 1건": b(nr_full.model_dump()),
    "NodeResult 축약 1건": b(nr_slim),
    "Query 본문 1건": b(q.model_dump()),
    "slot 1건": b(slot),
    "conflict 1건": b(conflict),
    "evidence_id 1건": b(U(2)) + 1,
    "slot 축약 1건": b({"slot_id": 3, "status": "present"}),
    "node_result 압축 1건": b("n8:OK:4820") + 1,
    "masked_input 본문(짧은 입력)": b(masked_short),
    "masked_input 본문(긴 입력)": b(masked),
}
print("── 단위 실측 (JSON UTF-8 bytes) ──")
for k, v in UNIT.items():
    print(f"  {k:<28}{v:>6} B")

FIXED_REF = None  # 아래에서 계산
FIXED = (b("01K5ZTQ9X7WPCVN2M4H8JRAB0D") + b("th_9f2c8a1b") + b(NOW.isoformat())
         + b(0) + b(masked) + b(stock) + b(None) + b(NOW.isoformat())
         + b(coll) + b(oppose.model_dump()) + b(U(9)) + b(counters) + 21 * 24)


FIXED_REF = FIXED - b(masked) + b(U(7)) + 1     # masked_input 을 input_id 참조로


def total(C: int, variant: str) -> dict:
    nq = 2 * C + 3
    ne = min(nq * 3, 40)
    n_slots, n_conf, n_nr = 8, 1, 13 + 7
    d = {
        "고정부(식별·입력·collections·oppose·counters·키)": FIXED,
        "slots(값)": UNIT["slot 1건"] * n_slots,
        "conflicts(값)": UNIT["conflict 1건"] * n_conf,
        "query_ids": UNIT["claim_id 1건(리스트원소)"] * nq,
        "evidence_ids": UNIT["evidence_id 1건"] * ne,
        "claim_evaluation_ids": UNIT["claim_id 1건(리스트원소)"] * C,
        "finding_ids": UNIT["claim_id 1건(리스트원소)"] * 8,
    }
    if variant == "v2.1a":
        d["claims(본문·값)"] = UNIT["Claim 본문 1건"] * C
        d["claim_evidence_keys"] = UNIT["ClaimEvidence key 1건"] * C * 12
        d["node_results(전체)"] = UNIT["NodeResult 전체 1건"] * n_nr
        d["queries(본문·값)"] = UNIT["Query 본문 1건"] * nq
    elif variant == "v2.1c":
        d["claims(본문·값)"] = UNIT["Claim 본문 1건"] * C
        d["claim_evidence_keys"] = UNIT["ClaimEvidence key 1건"] * C * 12
        d["node_results(전체)"] = UNIT["NodeResult 전체 1건"] * n_nr
    elif variant == "v2.2a":
        d["claim_ids"] = UNIT["claim_id 1건(리스트원소)"] * C
        d["node_results(축약)"] = UNIT["NodeResult 축약 1건"] * n_nr
    else:  # v2.2 최종
        d.pop("evidence_ids")
        d["고정부(식별·입력·collections·oppose·counters·키)"] = FIXED_REF
        d["slots(값·축약)"] = UNIT["slot 축약 1건"] * n_slots
        d.pop("slots(값)")
        d["claim_ids"] = UNIT["claim_id 1건(리스트원소)"] * C
        d["node_results(압축문자열)"] = UNIT["node_result 압축 1건"] * n_nr
    return d


print("\n── 총 blob 실측 (5,120B = 5KB 예산) ──")
print(f"{'버전':<12}{'C=4':>12}{'':>8}{'C=6':>12}{'':>8}{'C=8':>12}")
for v in ("v2.1a", "v2.1c", "v2.2a", "v2.2"):
    row = f"{v:<12}"
    for C in (4, 6, 8):
        t = sum(total(C, v).values())
        pct = t / 5120 * 100
        row += f"{t:>9,}B {'🔴' if pct > 100 else '✅'}{pct:>6.0f}%"
    print(row)

print("\n── v2.2 C=8 채널별 내역 ──")
d = total(8, "v2.2")
for k, v in sorted(d.items(), key=lambda x: -x[1]):
    print(f"  {k:<48}{v:>6} B  ({v/5120*100:4.1f}%)")
print(f"  {'합계':<48}{sum(d.values()):>6} B  ({sum(d.values())/5120*100:4.1f}%)")

print("\n── v2.1c C=8 채널별 내역 (무엇이 범인인가) ──")
d = total(8, "v2.1c")
for k, v in sorted(d.items(), key=lambda x: -x[1])[:5]:
    print(f"  {k:<48}{v:>6} B  ({v/5120*100:4.1f}%)")
print(f"  {'합계':<48}{sum(d.values()):>6} B  ({sum(d.values())/5120*100:4.1f}%)")


# 🔴 CI 진입점 (불변식 I11 · Stop 훅). 값은 문서가 아니라 코드가 진실이다.
#    --assert-under 5120 으로 C=4/6/8 을 전부 검사하고 하나라도 넘으면 exit 1.
if "--assert-under" in sys.argv:
    limit = int(sys.argv[sys.argv.index("--assert-under") + 1])
    over = [(C, sum(total(C, "v2.2").values())) for C in (4, 6, 8)]
    bad = [(C, t) for C, t in over if t > limit]
    print(f"\n── I11 체크포인트 예산 회귀 (상한 {limit:,}B) ──")
    for C, t in over:
        print(f"  C={C}  {t:>6,}B  {'🔴 초과' if t > limit else '✅'}")
    if bad:
        worst = max(bad, key=lambda x: x[1])
        print(f"🔴 I11 실패: C={worst[0]} 에서 {worst[1]:,}B > {limit:,}B. "
              f"어느 채널이 범인인지는 위 내역표를 봐라.", file=sys.stderr)
        raise SystemExit(1)
