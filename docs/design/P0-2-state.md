# P0-2 · G3 설계 — `app/orchestration/state.py`

> **상태: 승인 대기.** 본문(구현)은 한 줄도 쓰지 않았다.
> 근거 문서: DDR §5 · `STATE_LIFECYCLE_v2_2.md` §2·§4 · `TASK_CARDS_v2_2.md` P0-2

---

## 1. 만들 것 — 파일 1개

```
app/orchestration/state.py      ← 이 파일만. 다른 파일을 만들지 않는다
```

## 2. 채널 19개 (카드 목록 그대로. 하나도 빼거나 더하지 않는다)

```python
class ReviewState(TypedDict):
    run_id: str
    thread_id: str
    as_of: str
    snapshot_version: int
    input_id: str | None
    stock: dict | None
    user_action: dict | None
    slots: Annotated[list[dict], merge_by_slot_id]
    claim_ids: Annotated[list[str], add_unique]
    conflicts: Annotated[list[dict], add_unique_by_id]
    query_ids: Annotated[list[str], add_unique]
    collections: Annotated[dict, merge_dict]
    claim_evaluation_ids: Annotated[list[str], add_unique]
    finding_ids: Annotated[list[str], add_unique]
    oppose: dict | None
    report_id: str | None
    node_results: Annotated[list[str], operator.add]
    counters: Annotated[dict, sum_counters]
    started_at: str
```

🔴 `evidence_ids` · `claim_evidence_keys` 채널은 만들지 않는다 (v2.2 변경 40·41).

## 3. 리듀서 5종 — 시그니처와 항등원

| 리듀서 | 시그니처 | 항등원 | 쓰는 채널 |
|---|---|---|---|
| `add_unique` | `(list[str], list[str]) -> list[str]` | `[]` | `claim_ids` `query_ids` `claim_evaluation_ids` `finding_ids` |
| `add_unique_by_id` | `(list[dict], list[dict]) -> list[dict]` | `[]` | `conflicts` |
| `merge_by_slot_id` | `(list[dict], list[dict]) -> list[dict]` | `[]` | `slots` |
| `merge_dict` | `(dict, dict) -> dict` | `{}` | `collections` |
| `sum_counters` | `(dict, dict) -> dict` | `{}` | `counters` |

## 4. 🔴 순서 독립성 — 5종이 서로 다르다

카드는 *"reduce(a,b) 를 셔플해도 결과가 1종"* 이라고 한 줄로 적었다.
**5종을 각각 대수적으로 따져보니 그 성질이 성립하는 것은 1종뿐이다.**

| 리듀서 | 교환법칙 | 결합법칙 | 판정 |
|---|:---:|:---:|---|
| `sum_counters` | ✅ | ✅ | **진짜 순서 독립.** 정수 덧셈이라 자명 |
| `add_unique` | ⚠️ | ✅ | 집합으로는 같고 **리스트로는 다르다** (§4.1) |
| `add_unique_by_id` | ❌ | ✅ | "나중 것이 이긴다" = 정의상 순서 의존 (§4.2) |
| `merge_by_slot_id` | ❌ | ✅ | 같음 (§4.2) |
| `merge_dict` | ❌ | ✅ | 같음. **🔴 손실 여부가 n6 모양에 달림** (§4.3) |

### 4.1 `add_unique` — 카드의 두 요구가 서로 충돌한다

```
카드 제약 2:  셔플해도 결과가 1종이어야 한다
카드 제약 3:  순서를 보존한다 (먼저 들어온 것이 앞)
```

`add_unique([A],[B]) = [A,B]` · `add_unique([B],[A]) = [B,A]`.
**둘 다 만족시킬 수 없다.** 도착 순서를 보존하면 셔플이 결과를 바꾼다.

해석이 두 가지다. 조용히 고르지 않고 둘 다 올린다.

| | 해석 A — 집합 동등 | 해석 B — 정렬 |
|---|---|---|
| 의미 | I2 는 **집합**으로 비교한다 | 결과를 `sorted()` 해 리스트까지 동일하게 |
| 구현 | `STATE_LIFECYCLE §4.1` 코드 그대로 | 반환 직전 정렬 1줄 추가 |
| 문서 정합 | ✅ DDR 이 적은 구현과 일치 | ❌ 문서 코드를 바꿔야 함 |
| 재현성 | 사용처에서 정렬한다 (프로젝트의 기존 패턴) | 채널 자체가 정렬돼 있음 |
| 실질 차이 | ULID 는 시간 순 정렬이라 **거의 같은 결과** | 위와 같음 |

**권장: A.** 이 저장소는 이미 *"순서가 결과에 영향을 주는 곳에서 정렬한다"* 를
두 군데에서 명시적으로 하고 있다 — `evidence_ids_for_claim`(카드: "정렬이 고정되지
않으면 재현성이 깨진다")과 `budget.truncate`(`sorted(key=(as_of, evidence_id))`).
리듀서까지 정렬하면 같은 보증을 두 층에서 하게 되고, DDR 이 적은 코드와도 어긋난다.

### 4.2 `add_unique_by_id` · `merge_by_slot_id` — 순서 의존이 설계다

`STATE_LIFECYCLE §4.2` 가 *"나중 것이 이긴다"* 를 **유일한 갱신 수단**으로 못박았다.
같은 `conflict_id`(또는 `slot_id`)가 양쪽에 오면 결과가 순서에 따라 갈린다.

**실제로 도달 가능한가**: 아니다.
`conflicts` 는 n3·n3b 가 쓰고, `slots` 도 n3·n3b 가 쓴다. **둘 다 순차 실행이다.**
병렬 브랜치는 n6(query 단위)·n7·n8(Claim 단위)뿐이고 이 채널들을 안 건드린다.

→ 순서 의존이 **잠재적이지만 도달 불가**. I2 셔플 테스트는 **서로 다른 id** 로만
   구성해야 의미가 있다. 같은 id 를 섞어 넣고 "1종"을 요구하면 통과할 수 없다.

### 4.3 🔴 `merge_dict` — 문서끼리 어긋난다. 답이 n6 의 모양에 달려 있다

`STATE_LIFECYCLE §4.4` 의 구현은 `{**left, **right}` 이고 `collections` 는
**provider 를 키로** 한다. 그러면 같은 provider 가 양쪽에 오면 왼쪽이 통째로 사라진다.
**그게 실제로 일어나는지는 n6 가 LangGraph 팬아웃인지에 달려 있는데, 문서가 두 갈래다.**

```
팬아웃이라고 읽히는 곳
  STATE_LIFECYCLE §4.1 Why4   "n6 는 query 단위 병렬이다"                    ← 명시
  STATE_LIFECYCLE §4.4 Why2   "n6 는 query 단위 병렬이고 한 provider 에
                               쿼리가 여러 개다"                              ← 명시

단일 노드라고 읽히는 곳
  STATE_LIFECYCLE §3 n6       Δ = {dart:…, news:…, quote:…}  ← 세 provider 가 한 Δ 안에
  DIAGRAMS §8.8               단선 흐름. "State Δ = collections 만"
  DDR §4.1 n6 근거            v2.1a §1.2 "n6_collect **1패스** 내 중복 제거"
  DDR §9.2                    "게이트웨이 async" 가 팀원3 S4 작업으로 따로 있음
  RateLimiter / max_concurrency  provider 별 동시성 제어가 **인프로세스 공유 상태**다
```

**내 판단: 단일 노드 쪽이 유력하다.** 유량 제한과 `max_concurrency` 가 인프로세스
공유 자원이라, 게이트웨이가 노드 안에서 `gather` 로 동시 호출하고 Δ 를 한 번만
반환하는 그림이 나머지 계약과 전부 맞는다. §4.1·§4.4 의 "query 단위 병렬" 은
**LangGraph 팬아웃이 아니라 게이트웨이 내부 동시성**을 가리킨 것으로 읽힌다.

그렇다면 `{**left, **right}` 는 안전하고, 합산은 n6 안에서 일어난다.

**그런데 그 판단이 틀리면 조용히 틀린다.** 배너가 *"DART 4건 수집"* 이라고
인쇄하는데 실제로는 11건이고, 어느 브랜치가 살아남는지가 비결정적이라
D-15 재현성까지 깨진다. `OpposeBlock.count` 부풀림과 같은 계열의 거짓이다 —
방향만 반대로, 부풀리는 대신 줄인다.

**선택지 3개:**

| | 무엇 | 대가 |
|---|---|---|
| **M1** | `{**left, **right}` 유지. n6 가 provider 단위 합산을 책임진다 | 문서·구현 변경 0. 🔴 단, 이 계약을 **n6 카드에 적어둬야** 나중에 팬아웃으로 바꾸는 사람이 배너를 조용히 깨뜨리지 않는다 |
| **M2** | `CollectionResult` 카운터 4종을 **합산** 병합 | 두 읽기 모두에서 맞다. 🔴 대신 **재수집(n9→n5→n6) 때 2패스가 합산**된다 — `items_fetched` 가 1차+2차 총계가 된다. 그게 맞는 의미인지가 별도 결정이다 |
| **M3** | 지금 정하지 않고 n6(S1) 에서 처리 | 🔴 최악. P0-2 는 리듀서 의미를 정하는 **유일한** 세션이다. 안 정하면 n6 작성자가 리듀서가 합산하는 줄 알고 짜거나, 덮어쓰는 줄 알고 노드에서 또 합산해 **두 번 세게** 된다 |

**권장: M1 + n6 카드에 계약 1줄 추가.**
M2 는 재수집 의미론(총계인가 최신 패스인가)이라는 **새 미결정 항목을 만든다.**
DDR 이 "미결정 0건"으로 닫은 문서인데 리듀서를 고치면서 하나를 여는 셈이다.
지금 필요한 건 리듀서 변경이 아니라 **누가 합산하는지를 글로 박는 것**이다.

## 5. 그 밖의 제약

1. 리듀서는 **순수 함수**. 입력 리스트를 in-place 로 바꾸지 않는다 (새 객체 반환).
2. `sum_counters` 는 없는 키를 0 으로 취급한다.
3. **왼쪽이 `None` 일 때**: LangGraph 가 채널 초기값 없이 리듀서를 부를 수 있다.
   5종 전부 `None` 을 항등원으로 받아들일지 정해야 한다.
   → 권장: 받아들인다. 안 그러면 n0 이 19채널을 전부 초기화해야 하고,
     그건 카드가 금지한 "채널을 더하는 것" 만큼이나 State 정의를 부풀린다.

## 6. 검증 계획 (구현 **전에** 쓴다)

카드 3단계: *"순서 독립성 테스트를 먼저 쓴다. 이 테스트가 구현 전에 실패하는 것을
확인하고 나서 구현해라."*

```
tests/orchestration/test_state.py

1. 셔플 5회 × 리듀서 5종        → verify: 결과 1종 (§4.1 에서 정한 동등 기준으로)
2. 항등원                        → verify: f(x, []) == f([], x) == x
3. 결합법칙                      → verify: f(f(a,b),c) == f(a,f(b,c))
4. in-place 변경 없음             → verify: 입력 리스트가 호출 전후 동일
5. add_unique 중복 제거          → verify: 같은 ID 2회 append 시 1건
6. merge_dict 합산 (M1 채택 시)   → verify: dart 2브랜치 → items_fetched 합
7. 채널 19개 정확히               → verify: ReviewState.__annotations__ 개수
8. 금지 채널 부재                 → verify: evidence_ids · claim_evidence_keys 없음

완료: uv run pytest tests/orchestration/test_state.py -q
      uv run python -m ci.invariants --only I1,I2
```

---

## 7. 🔴 이 설계에서 내가 가장 확신이 없는 결정

**n6 가 LangGraph 팬아웃인가, 게이트웨이 내부 동시성인가.**

`merge_dict` 를 그대로 둘지 합산으로 바꿀지가 여기 하나에 달려 있는데,
`STATE_LIFECYCLE §4.1·§4.4` 는 "query 단위 병렬" 이라 쓰고
`§3`·`DIAGRAMS §8.8`·`DDR §4.1` 은 단일 Δ 로 그린다. **문서가 서로 다르다.**

나는 단일 노드 쪽에 무게를 뒀다 — `RateLimiter` 와 `max_concurrency` 가
인프로세스 공유 자원이라 팬아웃과 잘 안 맞기 때문이다. 하지만 이건 **추론이지
문서의 확정이 아니다.** 그리고 내가 틀리면 배너 숫자가 조용히 줄어든다.

이건 내가 정할 게 아니다. §4.3 의 M1/M2/M3 중 하나를 골라주면 그대로 구현한다.
고르는 데 필요한 질문은 하나다 — **n6 는 Δ 를 몇 번 반환하는가.**

두 번째로 확신이 없는 것은 §4.1 의 해석 A/B 다. 다만 실질 차이가 거의 없다
(ULID 가 시간 순이라 정렬해도 도착 순서와 거의 같다) — A 로 가도 위험이 낮다.

## 8. 승인이 필요한 것 — 2건

```
Q1  §4.3  merge_dict:  M1(현행 유지 + n6 카드에 계약 명시)  ← 권장
                       M2(합산 병합)  ·  M3(보류)
Q2  §4.1  add_unique:  해석 A(집합 동등, 문서 코드 그대로)   ← 권장
                       해석 B(정렬해서 리스트까지 동일)
```

승인되면 §6 검증 계획대로 **테스트를 먼저 쓰고**(구현 전 실패 확인) 구현한다.
