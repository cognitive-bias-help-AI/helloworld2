# DDR v2.2 — State 생애주기 · 리듀서 · 파라미터 전수 해부

> 시작 State가 n0부터 n12까지 리듀서를 거치며 어떻게 변하는지, 그리고 **모든 파라미터가 왜 그 값인지**를 5단계 Why로 파고듭니다.
> **§2의 모든 바이트 수는 추정이 아니라 실측입니다** — `frozen.py`의 실제 모델을 만들어 JSON UTF-8로 직렬화한 값입니다. 재현 스크립트: `measure_state.py`.

---

# §1. 🔴 먼저 — 이 분석에서 나온 가장 큰 발견

`ReviewState`의 모든 채널을 실제로 직렬화해 재봤습니다. **v2.1c의 수정(`queries` → `query_ids`)은 필요했지만 전혀 충분하지 않았습니다.**

```
체크포인트 blob 실측 (예산 5,120B = 5KB, 불변식 I1)

버전            C=4                 C=6                 C=8
v2.1a       16,654B 🔴 325%     20,687B 🔴 404%     24,517B 🔴 479%
v2.1c       12,441B 🔴 243%     14,942B 🔴 292%     17,240B 🔴 337%   ← queries 만 고친 상태
v2.2 최종    3,016B ✅  59%      3,248B ✅  63%      3,480B ✅  68%
```

**v2.1c는 Claim 4개짜리 가장 작은 실행에서도 243%입니다.** `queries`를 뺐는데도 그렇습니다. 범인이 따로 있었습니다.

```
v2.1c · C=8 채널별 실측

  claim_evidence_keys      5,376 B   105.0%   ← 혼자 예산을 다 쓴다
  node_results(전체)        3,580 B    69.9%
  claims(본문·값)           3,120 B    60.9%
  고정부                    1,808 B    35.3%
  evidence_ids             1,160 B    22.7%
  ─────────────────────────────────────────
  합계                     17,240 B   336.7%
```

`claim_evidence_keys`는 `"claim_id:evidence_id"` 문자열이고 **실측 56B**입니다. C×12건이면 C=8에서 5,376B — 5KB 예산 전체보다 큽니다.

## 1.1 확정 처리 6건

| # | 변경 | 실측 근거 | 절감 (C=8) |
|---|---|---|---|
| 38 | `masked_input` → **`input_id`** (참조) | 본문 341B. **사용자 입력 길이에 비례하는 유일한 무한 채널**. 5,000자 입력이면 혼자 예산 초과 | 312 B + 무한 리스크 제거 |
| 39 | `claims` → **`claim_ids`** (참조) | Claim 본문 **390B 실측** × 8 = 3,120B (61%) | 2,888 B |
| 40 | **`evidence_ids` 채널 삭제** | 29B × 40 = 1,160B. `query_ids` → `evidence_query_link`로 **완전히 유도 가능**한 중복 채널 | 1,160 B |
| 41 | **`claim_evidence_keys` 채널 삭제** | 56B × C×12 = 5,376B. n8은 `get_claim_evidence(run_id, claim_id)`로 읽으면 됨 | 5,376 B |
| 42 | `slots` 축약 `{slot_id, status}` | 123B → **35B 실측**. `label`은 `slots.py` 정적, `claim_id`는 claim 테이블 | 704 B |
| 43 | `node_results` **압축 문자열** `"n8:OK:4820"` | 179B → **13B 실측**. `NodeResult` 본문은 trace + `node_result` 테이블 | 3,320 B |

**적용 후 C=8 = 3,480B (68%).** 향후 채널 추가 여유가 1.6KB 남습니다.

> **왜 v2.1c가 이걸 못 봤는가**: `queries`만 355자로 재고 나머지는 안 쟀기 때문입니다. `claim_evidence_keys`는 "ID 문자열이니 작겠지"라고 넘어갔는데, **ULID 두 개를 이어붙이면 56B이고 개수가 C×12로 곱해집니다.** 채널 하나만 재면 이런 걸 못 잡습니다.
>
> **그리고 이 6건은 `ReviewStore`가 없으면 불가능했습니다.** 본문을 놓을 곳이 생겼기 때문에 State를 비울 수 있게 된 것입니다.

---

# §2. 최종 `ReviewState` — 채널 19개

```python
class ReviewState(TypedDict):
    # ── 식별 ────────────────────────────────────────────
    run_id: str                     # n0. 26B
    thread_id: str                  # API. 12B
    as_of: str                      # n0. ISO8601. 25B
    snapshot_version: int           # n0·n12. 2B

    # ── 입력 ────────────────────────────────────────────
    input_id: str | None            # 🔴 v2.2 참조. 본문은 run_input 테이블
    stock: dict | None              # n2. 축약 dict
    user_action: dict | None        # n4. 되묻기 답변

    # ── 슬롯·주장 ───────────────────────────────────────
    slots: Annotated[list[dict], merge_by_slot_id]      # 🔴 {slot_id, status} 만
    claim_ids: Annotated[list[str], add_unique]         # 🔴 v2.2 참조
    conflicts: Annotated[list[dict], add_unique_by_id]  # 값 유지

    # ── 수집 ────────────────────────────────────────────
    query_ids: Annotated[list[str], add_unique]
    collections: Annotated[dict, merge_dict]
    # 🔴 evidence_ids · claim_evidence_keys 채널 삭제 (v2.2)

    # ── 분석 ────────────────────────────────────────────
    claim_evaluation_ids: Annotated[list[str], add_unique]
    finding_ids: Annotated[list[str], add_unique]
    oppose: dict | None

    # ── 출력 ────────────────────────────────────────────
    report_id: str | None

    # ── 제어 ────────────────────────────────────────────
    node_results: Annotated[list[str], operator.add]    # 🔴 "n8:OK:4820" 압축 문자열
    counters: Annotated[dict, sum_counters]
    started_at: str
```

## 2.1 채널 19개 전수표 — 실측 크기 포함

| # | 채널 | 리듀서 | 값/참조 | 쓰는 노드 | 읽는 노드 | 단위 실측 | C=8 합계 | 역할 |
|---|---|---|---|---|---|---:|---:|---|
| 1 | `run_id` | 덮어쓰기 | 값 | n0 | 전부 | 26 B | 26 B | 이 실행의 유일 식별자. 모든 DB 행의 스코프 |
| 2 | `thread_id` | 덮어쓰기 | 값 | API | Checkpointer | 12 B | 12 B | 대화 스레드. 되묻기 재개 앵커 |
| 3 | `as_of` | 덮어쓰기 | 값 | n0 | n5·n6·n7 | 25 B | 25 B | **스냅샷 기준 시각.** 캐시키·멱등키 구성요소 |
| 4 | `snapshot_version` | 덮어쓰기 | 값 | n0·n12 | D-24 CAS | 2 B | 2 B | 낙관적 동시성 제어 버전 |
| 5 | `input_id` | 덮어쓰기 | **참조** | n0 | n1·n3 | 29 B | 29 B | 마스킹된 원문 참조. 본문은 `run_input` |
| 6 | `stock` | 덮어쓰기 | 값 | n2 | n5·n11 | 178 B | 178 B | 확정 종목 + 상폐/관리 플래그 |
| 7 | `user_action` | 덮어쓰기 | 값 | n4 | n3b | ~200 B | 200 B | 되묻기 답변. 슬롯 번호별 |
| 8 | `slots` | `merge_by_slot_id` | 값 | n3·n3b | **라우팅**·n9·n10·n11 | **35 B** | 280 B | 8슬롯 상태. 라우팅이 매 step 읽는다 |
| 9 | `claim_ids` | `add_unique` | **참조** | n3·n3b | n5·n7·n8 | **29 B** | 232 B | 명제 ID. 본문 390B는 `claim` 테이블 |
| 10 | `conflicts` | `add_unique_by_id` | 값 | n3·n3b | n4·n9 | 197 B | 197 B | 슬롯 내 모순. 개수가 작아 값으로 |
| 11 | `query_ids` | `add_unique` | **참조** | n5 | n6·n7 | 29 B | 551 B | 쿼리 ID. 본문 383B는 `query` 테이블 |
| 12 | `collections` | `merge_dict` | 값 | n6 | n9·n11 | ~380 B | 380 B | provider별 `CollectionResult`. 배너 입력 |
| 13 | `claim_evaluation_ids` | `add_unique` | **참조** | n8 | n9 | 29 B | 232 B | 평가 ID. 본문은 `claim_evaluation` |
| 14 | `finding_ids` | `add_unique` | **참조** | n9 | n10·n11 | 29 B | 232 B | Finding ID |
| 15 | `oppose` | 덮어쓰기 | 값 | n9 | n11 | ~250 B | 250 B | `OpposeBlock` 1개. 리포트 직결 |
| 16 | `report_id` | 덮어쓰기 | 값 | n11 | API | 26 B | 26 B | 최종 산출물 참조 |
| 17 | `node_results` | `operator.add` | 값 | 전부 | n12 | **13 B** | 260 B | `"n8:OK:4820"`. 본문은 trace |
| 18 | `counters` | `sum_counters` | 값 | 전부 | **라우팅**·n12 | ~120 B | 120 B | 예산 카운터 5종 |
| 19 | `started_at` | 덮어쓰기 | 값 | n0 | n12 | 25 B | 25 B | 벽시계 시작 시각 |
| | **합계 (JSON 키 오버헤드 포함)** | | | | | | **3,480 B** | **68% of 5KB** ✅ |

**참조 채널 6개(5·9·11·13·14·16)의 본문이 사는 곳**

```
input_id             → run_input          (ReviewStore)
claim_ids            → claim              (ReviewStore)
query_ids            → query              (EvidenceStore)
claim_evaluation_ids → claim_evaluation   (ReviewStore)
finding_ids          → finding            (ReviewStore)
report_id            → report             (ReviewStore)
+ 삭제된 evidence_ids → evidence + evidence_query_link 로 유도 (EvidenceStore)
+ 삭제된 claim_evidence_keys → claim_evidence 로 직접 조회 (ReviewStore)
```

> 이 표가 **`ReviewStore`를 6테이블로 확정하는 근거**입니다. `run_input`과 `claim`이 §3에서 4테이블이었던 것에 추가됩니다.

---

# §3. 노드별 State 델타 추적 — 실측 크기

`Δ` = 그 노드가 반환하는 부분 딕셔너리. LangGraph는 이것만 받아 리듀서로 병합합니다. 누적 blob은 C=8 기준입니다.

```
━━━ n0 실행 초기화 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IN   thread_id, raw_text
동작  run_id=ULID · as_of=now KST 초절삭 · snapshot_version=0
     PII 마스킹 → ReviewStore.put_input(run_id, body) → input_id
Δ    run_id, as_of, snapshot_version, input_id, started_at
     node_results += ["n0:OK:12"]
누적  ~120 B                                              ← 체크포인트 v0
🔴   본문을 State 에 안 싣는 이유: 사용자 입력은 길이 상한이 없다.
     5,000자 입력 하나가 예산 전체를 먹는다. 유일한 무한 채널이었다

━━━ n1 입력 가드 (SMALL) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IN   input_id → ReviewStore.get_input
Δ    node_results += ["n1:OK:840"]
     counters += {total_llm_calls: 1}
누적  +33 B      ← 채널 신규 0개
🔴   BLOCKED 이면 Δ 에 reason_code 가 실리고 라우팅이 n12 로 보낸다

━━━ n2 종목 해소 (규칙 0콜) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IN   input_id
Δ    stock={code,name,market,match_kind,score,is_delisted,is_managed}
     node_results += ["n2:OK:31"]
누적  +191 B                                              ← 체크포인트 v1

━━━ n3 슬롯 추출 (SMALL) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IN   input_id, stock
동작  ReviewStore.put_claims(run_id, claims)  ← 본문 390B × C 는 DB 로
Δ    slots     = [{slot_id, status} × 8]      → merge_by_slot_id     280 B
     claim_ids = [ULID × C]                   → add_unique           232 B
     conflicts = [ConflictRecord]             → add_unique_by_id     197 B
     counters += {total_llm_calls: 1, verifiable_claims: C}
누적  +722 B                                              ← 체크포인트 v2
🔴   본문을 실었다면 +3,120B (61% of 5KB) 로 여기서 이미 위험

━━━ n4 되묻기 (SMALL) · interrupt ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IN   slots(결손·충돌), conflicts
Δ    user_action={2:"손절선은 -10%", 5:"모르겠습니다"}
     counters += {total_llm_calls: 1, hitl_reask: 1}
누적  +233 B     🔴 여기서 프로세스 반환. 커넥션을 잡고 있지 않는다

━━━ n3b 되묻기 병합 (규칙 0콜) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IN   user_action, slots, claim_ids
동작  put_claims(신규 1건) · 기존 Claim 에 superseded_by 연결 (DB)
Δ    slots     = [{slot_id:2, status:"present"}]  → merge_by_slot_id 로 2번만 갱신
     claim_ids = ["01K5...F"]                     → add_unique 로 추가만
     counters += {verifiable_claims: 1}           ← LLM 0회
누적  +64 B
🔴   merge_by_slot_id 가 없으면 8슬롯 전체를 다시 만들어야 하고,
     그러면 n3b 가 LLM 노드가 되어 예산 base 8 이 깨진다

━━━ n5 쿼리 설계 (규칙 0콜) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IN   claim_ids → get_claims, stock
동작  claim-scope 2C + stock-scope 3 = 19건 (C=8)
     EvidenceStore.put_queries(run_id, queries)
Δ    query_ids = [ULID × 19]                      → add_unique       551 B
누적  +564 B                                              ← 체크포인트 v3
🔴   본문을 실었다면 +7,277B (142% of 5KB) 로 즉시 I1 실패
     ← 이게 v2.1c 가 잡은 유일한 건이다

━━━ n6 수집 (LLM 0콜) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IN   query_ids → get_queries
동작  게이트웨이 · sha256 dedup · put_many · link
Δ    collections = {dart:{...}, news:{...}, quote:{...}}  → merge_dict  380 B
     counters += {total_external_calls: 19}
누적  +393 B                                              ← 체크포인트 v4
🔴   evidence_ids 채널을 안 만든다.
     evidence_query_link 가 정본이고, State 에 두면 1,160B 짜리 중복 사본이 된다.
     둘이 어긋나면 어느 쪽이 진실인지 판단할 근거가 없다

━━━ n7 stance 분류 (SMALL × C) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IN   claim_ids, query_ids  (evidence 는 link 테이블로 조회)
동작  packet 12건 → ClaimStanceDraft → assemble → put_claim_evidence
Δ    node_results += ["n7:OK:2100"]
     counters += {total_llm_calls: C}
누적  +33 B      🔴 채널 신규 0개
     claim_evidence_keys 를 실었다면 +5,376B (105%) — 혼자 예산 초과

━━━ n8 Claim 검증 (LARGE × C) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IN   claim_ids → get_claim_evidence(run_id, claim_id)
동작  compute_numeric_checks(규칙) → ClaimEvaluationDraft → assemble → upsert
Δ    claim_evaluation_ids = [ULID × C]            → add_unique       232 B
     counters += {total_llm_calls: C}
누적  +245 B                                              ← 체크포인트 v5

━━━ n9 typed reduction (LARGE) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IN   claim_evaluation_ids → get_claim_evaluations, slots, conflicts, collections
Δ    finding_ids = [ULID × ~8]                    → add_unique       232 B
     oppose      = {status:"verified", count:2, queries:[3개]}       250 B
     counters += {total_llm_calls: 1}
     재수집 시 counters += {graph_recollect: 1} 후 n5 로 복귀
누적  +495 B

━━━ n10 출력 가드 (LARGE ≤2) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IN   finding_ids → get_findings, slots
Δ    counters += {total_llm_calls: 1~2}
누적  +33 B      🔴 Violation 을 State 에 안 싣는다. 재작성 루프 안에서만 산다

━━━ n11 렌더 (MID) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IN   finding_ids, slots, oppose, stock + EvidenceStore.get_many(인용 원문)
Δ    report_id = "01K5..."
     counters += {total_llm_calls: 1}
누적  +59 B

━━━ n12 종료·차단 처리 (규칙 0콜) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IN   전부
Δ    snapshot_version += 1
     node_results += ["n12:OK:8"]
     🔴 Alert · StateChange · CostRecord 는 State 가 아니라 DB·관측으로
최종  3,480 B (68% of 5KB) ✅                              ← 체크포인트 v6
```

---

# §4. 리듀서 5종 — 각각 5단계 Why

## 4.1 `add_unique` — 참조 채널 전용 (`claim_ids` `query_ids` `claim_evaluation_ids` `finding_ids`)

```python
def add_unique(left: list[str], right: list[str]) -> list[str]:
    seen = set(left)
    return left + [x for x in right if not (x in seen or seen.add(x))]
```

```
Why 1  왜 operator.add 가 아니라 add_unique 인가?
       → 같은 ID 가 두 번 append 되면 리스트에 중복이 생긴다

Why 2  중복이 생기면 왜 문제인가?
       → 리포트의 "반대 근거 N건" 표기가 부풀어 오른다

Why 3  왜 부풀어 오르는가?
       → 같은 뉴스가 반대근거 쿼리 2개에서 각각 잡힌다.
         Evidence 는 UNIQUE(run_id, content_sha256) 로 1행이지만
         State 리스트는 두 브랜치에서 append 를 두 번 받는다

Why 4  왜 두 브랜치가 생기는가?
       → LangGraph 는 노드가 병렬 실행되면 각 브랜치의 Δ 를 전부 리듀서에 넣는다.
         n6 는 query 단위 병렬, n7·n8 은 Claim 단위 병렬이다

Why 5  왜 그게 D-14 위반인가?
       → D-14 는 "반대 근거를 실제로 몇 건 찾았는가"를 정직하게 말하라는 불변식이다.
         2건이라 쓰는데 실제로 1건이면 사용자가 반대 의견의 무게를 잘못 잡는다.
         확증편향을 줄이려고 만든 제품이 확증편향을 강화한다

∴  v2.0 §4.1 이 operator.add 를 버린 이유이고, F4 가 Evidence.query_id 를 지운 이유도 같다.
   리듀서 층과 스키마 층에서 같은 거짓을 두 번 막는다.
```

## 4.2 `add_unique_by_id` — `conflicts` 전용

```python
def add_unique_by_id(left: list[dict], right: list[dict]) -> list[dict]:
    """dict 의 *_id 키로 중복 판정. 나중 것이 이긴다."""
```

```
Why 1  왜 add_unique 로 안 되는가?
       → 원소가 str 이 아니라 dict 다. set 에 못 넣는다

Why 2  왜 dict 를 통째로 비교하지 않는가?
       → 같은 conflict 인데 resolved_claim_id 만 채워진 두 버전은
         dict 로는 다른 값이지만 논리적으로 같은 레코드다

Why 3  왜 나중 것이 이기게 하는가?
       → 갱신(충돌 해소)을 표현할 방법이 리듀서밖에 없다.
         State 는 불변 병합만 하고 in-place 수정을 하지 않는다

Why 4  왜 in-place 수정을 안 하는가?
       → LangGraph 체크포인트는 Δ 를 append-only 로 쌓아 재생한다.
         in-place 로 바꾸면 v3 체크포인트에서 재개했을 때 값이 달라진다

Why 5  왜 재생 값이 같아야 하는가?
       → D-15 재현성. 같은 입력·같은 as_of 면 같은 리포트가 나와야
         "왜 이 판정이 나왔는가"를 사후에 감사할 수 있다.
         감사 불가능한 금융 판단 보조 도구는 규제 리스크 그 자체다

∴  "나중 것이 이긴다"는 편의가 아니라 재생 가능성을 지키는 유일한 갱신 수단이다.
   v2.2 에서 claims 가 참조로 내려가면서 이 리듀서를 쓰는 채널은 conflicts 하나만 남았다.
```

## 4.3 `merge_by_slot_id` — `slots` 전용

```python
def merge_by_slot_id(left: list[dict], right: list[dict]) -> list[dict]:
    """slot_id 1~8 를 키로 병합. 필드 단위로 덮어쓴다."""
```

```
Why 1  왜 slots 만 전용 리듀서를 쓰는가?
       → 슬롯은 8개로 개수가 고정돼 있고, 노드마다 일부만 갱신한다

Why 2  왜 일부만 갱신하는가?
       → n3 는 8개 전부를, n3b 는 되묻은 1~2개만 건드린다.
         전체를 덮어쓰면 n3 가 채운 나머지 6개가 지워진다

Why 3  왜 n3b 가 전체를 다시 만들지 않는가?
       → n3b 는 되묻기 답변 1~2개만 안다. 나머지를 재구성하려면
         원문을 다시 추출해야 하고, 그러면 n3b 가 LLM 노드가 된다

Why 4  n3b 가 LLM 이 되면 왜 안 되는가?
       → 예산 base 가 정확히 8 로 떨어지는데 +2 가 생겨 4C+9 가 4C+11 이 되고,
         사용자가 확인해준 값의 origin 이 USER_CONFIRMED 에서 LLM_EXTRACTION 으로 오염된다

Why 5  provenance 오염이 왜 치명적인가?
       → D-25 SourceTrace 의 존재 이유가 "이 정보를 누가 말했는가"의 보존이다.
         사용자가 직접 말한 값과 LLM 이 추측한 값이 같은 태그를 달면
         충돌 해소 시 무엇을 신뢰할지 판단할 근거가 사라진다.
         리포트가 "당신이 말한 손절선"이라 쓰는데 실제로는 LLM 추측이 된다

∴  merge_by_slot_id 는 편의 함수가 아니라 n3b 를 규칙으로 유지시키는 전제조건이다.
```

## 4.4 `merge_dict` — `collections` 전용

```python
def merge_dict(left: dict, right: dict) -> dict:
    return {**left, **right}
```

```
Why 1  왜 provider 별 CollectionResult 를 dict 로 모으는가?
       → 리스트로 두면 같은 provider 결과가 여러 번 들어가 집계가 틀린다

Why 2  왜 여러 번 들어가는가?
       → n6 는 query 단위 병렬이고, 한 provider 에 쿼리가 여러 개다

Why 3  왜 provider 단위로 합치는가?
       → 리포트 배너가 "DART 3건 수집, 네이버 실패" 형태로 출처 단위로 표기된다

Why 4  왜 출처 단위 표기가 필요한가?
       → 네이버가 죽어서 반대 근거를 못 찾은 것과, 찾았는데 없는 것은
         사용자에게 완전히 다른 의미다. 전자는 "확인 못 함", 후자는 "확인했고 없음"

Why 5  왜 그 구분이 제품의 핵심인가?
       → OpposeBlock.status 가 verified/unverified 두 값인 이유가 이것이다.
         구분 못 하면 "반대 근거가 없습니다"라고 쓰는데 실제로는 못 찾은 것이 된다.
         v2.2 S-1 이 verified 에 queries 최소 1건을 강제한 이유도 같은 자리다

∴  merge_dict 는 OpposeBlock.status 판정의 입력을 만드는 리듀서다.
   evidence_ids 채널을 지울 수 있었던 것도 수집 성과가 여기 다 들어 있기 때문이다.
```

## 4.5 `sum_counters` — `counters` 전용

```python
def sum_counters(left: dict, right: dict) -> dict:
    return {k: left.get(k, 0) + right.get(k, 0) for k in set(left) | set(right)}
```

```
Why 1  왜 카운터를 State 에 두는가?
       → 라우팅이 매 super-step 마다 예산 초과를 판정해야 한다

Why 2  왜 모듈 전역 변수나 컨텍스트 객체로 안 하는가?
       → 되묻기 interrupt 로 프로세스가 반환됐다가 3일 뒤 재개될 수 있다.
         그때 프로세스 메모리는 없다

Why 3  왜 DB 에서 세지 않는가?
       → 라우팅은 매 super-step 마다 돈다. DB 왕복을 13번+ 추가하면
         지연이 눈에 띄게 늘고, 그 왕복이 실패하면 라우팅 자체가 멈춘다

Why 4  왜 합산 리듀서여야 하는가? 덮어쓰기면 안 되나?
       → n7·n8 은 Claim 별 병렬이다. 각 브랜치가 total_llm_calls=1 을 내는데
         덮어쓰기면 C 개가 1 이 된다. 예산이 C 배 과소계상된다

Why 5  예산이 과소계상되면 무엇이 무너지는가?
       → COUNTER_LIMITS 는 무한 루프를 막는 유일한 장치다.
         n9→n5 재수집 엣지와 n10 자기 루프가 있어 그래프에 사이클이 실재한다.
         카운터가 틀리면 사이클이 안 끊기고 비용이 발산한다

∴  sum_counters 는 회계가 아니라 정지성(termination) 보장 장치다.
   v2.2 에서 verifiable_claims 를 여기 넣은 것도 같은 이유다 —
   claims 가 참조로 내려가 라우팅이 C 를 셀 방법이 없어졌기 때문이다.
```

---

# §5. 핵심 설계 파라미터 — 각각 5단계 Why

## W1. 왜 State에 참조(ID)만 싣는가 — D-23

```
Why 1  왜 본문을 State 에 안 두는가?
       → 체크포인트 blob 이 5KB 를 넘는다. v2.1c 상태로 C=8 이면 17,240B (337%) [실측]

Why 2  왜 5KB 가 상한인가?
       → 체크포인트는 매 super-step 마다 통째로 직렬화·저장·역직렬화된다.
         13노드 + 병렬 브랜치면 run 1회에 20~30회 쓴다

Why 3  왜 그게 문제인가?
       → blob 이 17KB 면 run 당 약 500KB 를 쓰고 읽는다.
         동시 사용자 100명이면 초당 50MB 의 Postgres I/O 다

Why 4  DB 를 키우면 안 되는가?
       → 비용보다 지연이 크다. 체크포인트 저장은 노드 실행의 임계 경로에 있다.
         사용자가 기다리는 시간에 직접 더해진다

Why 5  왜 지연이 이 제품에서 특히 치명적인가?
       → 사용자는 "지금 팔아야 하나" 같은 시간 압박 상태에서 온다.
         응답이 30초를 넘으면 그 사이에 감정적 결정을 내려버린다.
         제품이 막으려는 행동을 제품의 지연이 유발한다

∴  D-23 은 성능 최적화가 아니라 제품 요구사항이다.
```

## W2. 왜 `as_of`를 n0에서 딱 한 번 고정하는가 — D-16

```
Why 1  왜 노드마다 now() 를 부르지 않는가?
       → 노드마다 시각이 다르면 한 run 안에 서로 다른 시점의 데이터가 섞인다

Why 2  섞이면 왜 문제인가?
       → n6 가 09:00 종가를, n8 이 09:05 시세를 보면
         "종가 71,800원인데 주장은 75,000원"이라는 검산의 기준 시점이 불명확해진다

Why 3  왜 기준 시점이 중요한가?
       → NumericCheck.result 가 consistent/inconsistent 를 가르는데
         장중이면 5분 사이에 판정이 뒤집힐 수 있다

Why 4  판정이 뒤집히면 왜 심각한가?
       → 같은 입력으로 두 번 돌렸을 때 리포트가 달라진다.
         D-15 재현성이 깨지고 골든셋 L0 회귀(비용 0)가 원리적으로 불가능해진다

Why 5  왜 L0 회귀가 없으면 안 되는가?
       → 이 제품은 프롬프트를 계속 고친다. 고칠 때마다 실 API 를 부르면
         비용과 유량 제한 때문에 회귀를 못 돈다. 회귀를 못 돌면 프롬프트를 못 고친다.
         결국 제품이 개선을 멈춘다

∴  as_of 는 타임스탬프가 아니라 "이 실행 전체의 진실 기준점"이다.
   그래서 ReplayCache.make_key 의 구성요소이고, 마이크로초를 버리고 초 단위로 절삭한다.
```

## W3. 왜 `slots`와 `counters`만 값 채널로 남기는가

```
Why 1  다른 건 다 참조로 내렸는데 이 둘은 왜 값인가?
       → 라우팅이 매 super-step 마다 이 둘을 읽어 분기를 결정한다

Why 2  라우팅이 왜 이 둘을 읽는가?
       → slots: n3→n4(결손·충돌 있나), n9→n10(결손 몇 개), n10(슬롯 단위 분할)
         counters: 모든 엣지의 예산·루프 판정

Why 3  라우팅이 DB 를 읽으면 안 되는가?
       → 라우팅 함수는 동기 순수 함수여야 한다. LangGraph 조건부 엣지는
         async I/O 를 전제하지 않고, 넣으면 라우팅 실패가 그래프 정지로 번진다

Why 4  왜 라우팅을 순수 함수로 유지하는가?
       → 라우팅은 CI 불변식 I2(순서 독립성)와 I6(루프 종료)의 검사 대상이다.
         I/O 가 들어가면 테스트가 DB 를 띄워야 하고, 그러면 아무도 안 돌린다

Why 5  왜 "아무도 안 돌리는 테스트"가 최악인가?
       → I6 은 무한 루프를 막는 유일한 정적 보증이다.
         돌지 않는 불변식은 없는 것과 같고, 사이클이 있는 그래프에 정지 보장이 사라진다

∴  slots 가 값인 것은 크기 문제가 아니라 라우팅 순수성 문제다.
   그래서 축약({slot_id,status} 35B)은 하되 참조로 내리지는 않았다.
   같은 이유로 v2.2 는 verifiable_claims 를 counters 에 넣었다 —
   claim_ids 만으로는 C 를 셀 수 없기 때문이다.
```

## W4. 왜 `evidence_ids` 채널을 지웠는가 — v2.2 신규

```
Why 1  왜 수집한 근거 ID 를 State 에 안 두는가?
       → query_ids → evidence_query_link 조인으로 완전히 유도된다.
         유도 가능한 값을 State 에 두면 사본이 두 개가 된다

Why 2  사본이 두 개면 왜 문제인가?
       → 둘이 어긋났을 때 어느 쪽이 진실인지 판단할 근거가 없다.
         State 는 리듀서로 병합되고 link 테이블은 트랜잭션으로 쓰인다. 실패 지점이 다르다

Why 3  실제로 어긋날 수 있는가?
       → n6 가 put_many 성공 후 State Δ 반환 전에 죽으면 DB 에만 있고 State 에는 없다.
         반대로 체크포인트가 저장된 뒤 트랜잭션이 롤백되면 State 에만 있다

Why 4  왜 link 테이블이 정본이어야 하는가?
       → UNIQUE(run_id, content_sha256) 와 PK(evidence_id, query_id) 가
         중복 제거의 실제 강제 지점이다. State 의 add_unique 는 보조 방어다

Why 5  왜 보조 방어를 지우는 게 맞는가?
       → 1,160B (예산의 23%) 를 쓰면서 정본과 어긋날 수 있는 사본이기 때문이다.
         D-14 정직성은 이미 스키마·DDL·리듀서 세 층에서 지켜진다.
         네 번째 층은 안전을 더하지 않고 불일치 가능성만 더한다

∴  "유도 가능한 값은 State 에 두지 않는다"가 D-23 의 자연스러운 따름정리다.
```

## W5. 왜 packet 상한이 12(claim 9 + stock 3)인가

```
Why 1  왜 무제한이 아닌가?
       → 컨텍스트가 길수록 중간 항목의 활용도가 떨어진다 (Liu et al. 2024 U자 곡선)

Why 2  왜 하필 12 인가?
       → v2.1 §4.2 는 15, §5.2 는 12 로 문서가 모순이었고 F1 이 12 로 통일했다.
         n8 예산 4,500자 ÷ raw_span 평균 250자 ≈ 18 에서
         Claim·지시문·스키마 오버헤드를 뺀 값이다 [추정 — G32 로 교정]

Why 3  왜 9 + 3 으로 쪼개는가?
       → stock-scope 는 사용자 주장과 무관하게 독립 수집한 반대 근거다.
         claim-scope 와 같은 풀에 넣고 자르면 반대 근거가 먼저 잘린다

Why 4  왜 반대 근거가 먼저 잘리는가?
       → 반대 근거는 claim 키워드와 매칭이 약해 관련성 순으로 두면 항상 뒤로 밀린다.
         쿼터를 안 나누면 확증편향이 절단 알고리즘 안에서 재생산된다

Why 5  왜 그게 제품의 실패인가?
       → OpposeBlock.count 가 구조적으로 0 에 수렴한다.
         "반대 근거를 찾아봤는데 없었습니다"가 매번 나오고
         사용자는 자기 판단이 검증됐다고 오해한다.
         확증편향을 줄이려고 만든 제품이 확증편향을 강화한다

∴  9+3 분리는 상한 관리가 아니라 확증편향 방지 장치다. n5 템플릿이 3개인 것도 여기서 나온다.
```

## W6. 왜 모델 슬롯이 SMALL / MID / LARGE 3개인가

```
Why 1  왜 하나로 통일하지 않는가?
       → n7 은 C×12건 분류로 호출 수가 지배적이고,
         n8 은 C회지만 추론 깊이가 지배적이다. 최적 모델이 다르다

Why 2  왜 2개(싼 것/비싼 것)로는 부족한가?
       → n11 렌더는 판단이 아니라 한국어 서술이다.
         SMALL 은 서술 품질이 부족하고 LARGE 는 낭비다

Why 3  왜 4개 이상으로 안 늘리는가?
       → ModelSpec 이 슬롯당 단가 3종(입력·캐시입력·출력)을 갖는다.
         슬롯이 늘면 CostRecord 집계와 골든셋 스윕 차원이 곱으로 늘어난다

Why 4  왜 차원이 느는 게 문제인가?
       → 골든셋 38건 × 슬롯 조합으로 회귀를 돈다.
         3슬롯이면 관리 가능하고 5슬롯이면 어떤 조합이 회귀에 걸렸는지 추적이 안 된다

Why 5  왜 추적 가능성이 중요한가?
       → 이 제품의 품질 문제는 "리포트가 이상하다"로 들어온다.
         원인이 프롬프트인지 모델인지 컨텍스트인지 분리 못 하면 고칠 수가 없다.
         슬롯 3개 + prompt_version 문자열이 그 분리축이다

∴  슬롯 3개는 비용 최적화이자 디버깅 축이다.
   ModelGateway.invoke 가 slot 과 prompt_version 을 따로 받는 이유가 이것이다.
```

## W7. 왜 `hitl_reask = 2`인가

```
Why 1  왜 무제한이 아닌가?
       → 사용자가 계속 답을 안 주면 그래프가 영원히 안 끝난다

Why 2  왜 1 이 아니라 2 인가?
       → 되묻기는 슬롯 최대 2개씩 묶어 묻는다 (AskBackContext ctx_items=2).
         8슬롯 중 결손이 4개면 2회가 필요하다

Why 3  왜 한 번에 4개를 안 묻는가?
       → 질문이 4개면 사용자가 답을 포기한다 [추정 — D-01 제품 가설].
         그리고 AskBackContext 1,500자 안에 4슬롯 설명이 안 들어간다

Why 4  왜 3회 이상은 안 되는가?
       → 3회면 대화가 취조가 된다. 각 회차가 SMALL 1콜 + interrupt 1회라
         지연이 사용자 응답 시간에 지배되어 run 이 수 시간으로 늘어난다

Why 5  결손이 남은 채 진행하는 게 왜 괜찮은가?
       → 결손 슬롯은 n9 가 Finding(kind="missing") 으로 리포트에 싣고
         TheoryNote(trigger=(slot,"absent")) 가 왜 그 축이 중요한지 설명한다.
         "당신은 손절 기준을 말하지 않았습니다"는 그 자체로 이 제품의 산출물이다.
         결손은 실패가 아니라 발견이다

∴  reask=2 는 타협이 아니라 제품 정의다. 그래서 n4 타임아웃이 n12 가 아니라 n5 로 간다.
```

## W8. 왜 `graph_recollect = 1`인가

```
Why 1  왜 재수집을 한 번만 허용하는가?
       → 재수집 1회 비용이 n7(C) + n8(C) + n9(1) = 2C+1 콜이다. C=8 이면 17콜

Why 2  왜 그게 큰가?
       → 전체 상한 4C+9 = 41 중 17 이면 41%다. 2회면 예산의 83%가 재수집이다

Why 3  왜 예산을 그렇게 쓰면 안 되는가?
       → 재수집으로 근거가 늘어난다는 보장이 없다.
         쿼리 템플릿이 같으면 같은 결과가 나오고 그건 캐시 히트로 끝난다

Why 4  그러면 왜 1회는 허용하는가?
       → n5 의 첫 쿼리는 n3 의 Claim 만 보고 만든다.
         n8 이 verdict 를 내면 "무엇이 부족한지"가 처음으로 구체화된다.
         2차 쿼리는 1차와 질적으로 다르다

Why 5  왜 3차는 질적으로 다르지 않은가?
       → 2차에서도 못 찾았다면 그 근거는 우리가 붙은 3개 provider 에 없는 것이다.
         DART·네이버·키움에 없는 정보를 더 뒤져도 안 나온다.
         그때는 EVIDENCE_INSUFFICIENT 를 배너로 정직하게 말하는 것이 맞다

∴  recollect=1 은 "한 번 더 보면 나아지고 두 번째부터는 안 나아진다"는 구조적 판단이다.
```

## W9. 왜 `max_concurrency`가 kiwoom=1 / dart=3 / naver=3인가

```
Why 1  왜 키움만 1 인가?
       → 키움 REST 는 유량 제한이 문서화돼 있지 않고
         1700/1701/1702 응답 메시지로만 온다

Why 2  왜 문서가 없으면 1 인가?
       → 초과하면 8010(IP 불일치)이나 계정 잠금으로 번질 수 있다.
         DART 는 초과해도 020(한도 초과) 응답만 온다

Why 3  왜 계정 잠금이 특별히 나쁜가?
       → 팀원1 의 T1-A 착수 조건이 계좌 개설 + IP 등록이고
         이게 셋 중 리드타임이 가장 길다. 잠기면 재개설까지 며칠이 날아간다

Why 4  왜 3 은 안전하다고 보는가?
       → DART·네이버는 초당 5회를 기본 추정값으로 두고 런타임 학습으로 교정한다(T1-C).
         3 은 그 절반 이하라 안전 마진이 있다 [추정 — S1 에서 교정]

Why 5  왜 처음부터 정확한 값을 안 넣는가?
       → 유량은 계정 등급·시간대·엔드포인트마다 다르다.
         문서에서 정확한 값을 찾는 것보다 RateLimitHint 를 런타임 파싱해
         낮추는 방향으로만 즉시 반영하는 편이 안전하고 실제로 정확하다

∴  max_concurrency 는 성능 튜닝이 아니라 착수 리스크 관리다.
   그래서 T1-A 카드에 "변경 금지"로 박혀 있다.
```

## W10. 왜 `MAX_VERIFIABLE_CLAIMS = 8`인가

```
Why 1  왜 Claim 수에 상한을 두는가?
       → 전체 LLM 예산이 4C+9 로 C 에 선형이다. C 가 무제한이면 비용도 무제한이다

Why 2  왜 하필 8 인가?
       → 슬롯이 8개이고 슬롯당 검증 가능한 명제 1개가 상한이다.
         SlotId 가 ge=1, le=8 인 것과 같은 근거다

Why 3  왜 슬롯당 1개인가?
       → 한 슬롯에 명제가 2개면 슬롯 정의가 잘못 쪼개진 것이다.
         "손절 기준" 슬롯에 명제가 2개면 서로 충돌하는 것이고
         그건 ConflictRecord 로 처리할 일이지 둘 다 검증할 일이 아니다

Why 4  왜 둘 다 검증하면 안 되는가?
       → 리포트가 같은 슬롯에 대해 상반된 verdict 두 개를 싣게 된다.
         사용자는 어느 쪽이 자기 판단인지 알 수 없다

Why 5  왜 그게 이 제품에서 특히 나쁜가?
       → 이 제품의 산출물은 "당신의 판단 구조는 이렇습니다"라는 거울이다.
         거울이 두 개의 상반된 상을 보여주면 거울이 아니라 혼란이다.
         인지 부하를 줄이려는 도구가 인지 부하를 늘린다

∴  C ≤ 8 은 비용 상한이자 슬롯 정의의 무결성 조건이다.
   C=8 · 재수집 1 · 되묻기 2 인 최악 시나리오가 41콜이고 이것이 total_llm_calls 상한이다.
```

---

# §6. 나머지 파라미터 일람 — 값과 근거

| 파라미터 | 값 | 어디에 | 근거 | 언제 바뀌나 |
|---|---|---|---|---|
| `COUNTER_LIMITS.total_external_calls` | 25 | 라우팅 | v2.0 유지. 쿼리 19 + 재시도 여유 6 | 유량 학습 후 |
| `COUNTER_LIMITS.total_llm_calls` | `4C+9` = 41 | 라우팅 | W10 | C 상한 변경 시 |
| `counters.verifiable_claims` | n3·n3b가 누적 | 라우팅 | 🆕 v2.2. `claims`가 참조로 내려가 C를 셀 수 없어짐 | — |
| `Request.timeout_s` | 10.0 | 게이트웨이 | 사용자 체감 30초 목표 ÷ 병렬 3 | S1 지연 실측 |
| `TokenBucket` 기본값 | kiwoom (3.0, 3) · dart (5.0, 5) · naver (5.0, 5) | T1-C | 전부 초기 **추정값**. 런타임 학습으로 교정 | `RateLimitHint` 수신 즉시 |
| `acquire` 내부 상한 | 30초 | T1-C | 무한 대기 금지. 초과 시 `TimeoutError` | — |
| n2 후보 모호 임계 | score 차 0.15 | 라우팅 | 종목 오선택 비용 ≫ 되묻기 1콜 `[추정]` | 골든셋 오선택률 |
| `n10` 재작성 | ≤2 | 라우팅 | 2회로 못 고치면 프롬프트 문제지 재시도 문제가 아님 | — |
| `ReplayCache` `as_of` 정밀도 | 초 단위 절삭 | T2-D | 마이크로초면 캐시가 절대 안 맞음 | — |
| Evidence 보존 | 90일 | T2-C | 외부 데이터. 재현 감사보다 짧아도 됨 | — |
| ReviewStore 보존 | 90일 초과 | T2-G | 판단 산출물. 감사 대상 | 규제 요건 확인 시 |
| `corp_code` 캐시 갱신 | 7일 | T2-B | DART 법인 목록 변경 주기 `[추정]` | 미스율 관측 |
| `normalized_value` 채움률 | ≥ 90% | 계약 테스트 | 비면 n8 규칙 검산 불가 → 수치 판단이 LLM으로 | — |
| `raw_span` 스키마 상한 | 500자 | `frozen.py` | 이보다 길면 버그 | — |
| `raw_span` p95 예산 | news 250 / dart 150 / quote 100 | 계약 테스트 | packet 4,000자 ÷ 12건 = 건당 333자 | fixture 20건 |
| `TheoryNote.definition` | ≤ 200자 | 스키마 | `RenderView` 3,500자에 8슬롯 삽입 | — |
| `Alert.detail` Slack 절단 | 500자 | T1-E | 개인정보 유출 면적 축소 | — |
| 골든셋 | 38건 | 검증 | 37 + G38 주입 내성 | 회귀 추가 시 |
| CI 불변식 | 11종 | `ci/invariants` | I1~I7 + v2.2 I8·I9·I10·I11 | approve 필요 |

---

# §7. 변경 이력 추가분 (v2.2 §11.3에 이어붙임)

| # | 변경 | 실측 근거 | 안 고치면 |
|---|---|---|---|
| 38 | `masked_input` → `input_id` (참조) | 본문 341B. 사용자 입력 길이에 비례하는 **유일한 무한 채널** | 긴 입력 하나가 예산 전체를 먹는다 |
| 39 | `claims` → `claim_ids` (참조) + `claim` 테이블 | Claim 본문 **390B** × 8 = 3,120B (61%) | C=6 부근에서 I1 실패 |
| 40 | `evidence_ids` 채널 삭제 | 1,160B (23%). `query_ids` → link 조인으로 완전 유도 | 정본과 어긋날 수 있는 사본 유지 |
| 41 | `claim_evidence_keys` 채널 삭제 | key **56B** × C×12 = 5,376B (105%). **혼자 예산 초과** | C=4에서도 I1 실패 |
| 42 | `slots` 축약 `{slot_id, status}` | 123B → **35B**. label은 정적, claim_id는 claim 테이블 | 704B 낭비 |
| 43 | `node_results` 압축 문자열 | 179B → **13B**. 본문은 trace + `node_result` 테이블 | 3,320B 낭비 |
| 44 | `counters.verifiable_claims` 신설 | 39의 부작용. 라우팅이 C를 셀 수단 | I6이 C를 모른 채 판정 |
| 45 | `ReviewStore` 4테이블 → **6테이블** | 38·39가 `run_input`·`claim`을 요구 | 본문 저장 경로 부재 |
