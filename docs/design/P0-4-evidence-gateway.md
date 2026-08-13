# P0-4 · Evidence Gateway 기반

> **상태: G2 완료 / G3 사용자 승인 완료 / G4 구현 완료.**
> 권위: DDR v2.2 → `app/schemas/frozen.py` → 승인된 P0-3 설계 → 다이어그램·생명주기·카드.
> G4는 승인된 Approach B로 구현·검증되었다.

## 1. 목적과 비개발자용 정의

P0-4는 외부 API가 내놓은 참고 자료를 그대로 믿지 않고, 출처와 획득 이력을 검증한 뒤
시스템이 책임지는 근거로 확정하여 중복 없이 저장하는 경계다.

```text
Query
  → ProviderAdapter
  → EvidenceDraft
  → Evidence Gateway / assemble_evidence
  → canonical Evidence
  → EvidenceStore
  → EvidenceQueryLink
```

P0-4의 완료 목적은 팀원1·2가 실제 Adapter/Postgres 내부를 몰라도 동일 Protocol과 reference
implementation을 따라 독립 개발할 수 있게 만드는 것이다. 실제 provider, Postgres, ReplayCache,
n6 LangGraph node는 범위 밖이다.

## 2. G2 권한 경계 팩트맵

표의 `생성`은 값을 정할 권한, `검증`은 경계에서 대조할 책임, `정본`은 지속성·유일성 책임이다.

| 필드 | Adapter | Gateway | Store | 근거 |
|---|---:|---:|---:|---|
| source_type | 생성 | provider 매핑 검증 | 정본 저장 | frozen `PROVIDER_SOURCE_TYPE` / DDR §7.1 |
| source_ref | 생성 | hash 입력 | 정본 저장 | EvidenceDraft semantic field |
| source_url | 생성 | frozen validation | 정본 저장 | Adapter 출처 의미 |
| publisher | 생성 | 전달 | 정본 저장 | Adapter 출처 의미 |
| published_at | 생성 | timezone-aware model validation | 정본 저장 | Adapter 계약 |
| raw_span | 생성 | hash 입력·원문 보존 | 정본 저장 | 최대 500자, 표시값 변경 금지 |
| span_scope | 생성 | 전달 | 정본 저장 | EvidenceDraft semantic field |
| normalized_value | 생성 | 전달 | 정본 저장 | 규칙 검산 입력 |
| evidence_id | 금지 | 신규에만 생성 | PK 정본 | system-owned canonical field |
| content_sha256 | 금지 | 계산 | `(run_id, hash)` unique 정본 | F4 / DDR §8 |
| provider_request_id | 금지 | `ProviderCall`에서 주입 | FK 정본 | acquisition lineage |
| fetched_at | 금지 | 주입 | 정본 저장 | acquisition field, 생성 규칙 미결정 |
| as_of | 금지 | run 입력값 주입 | 정본 저장 | D-16 snapshot anchor |

핵심 경계:

- Adapter는 `EvidenceDraft`까지만 만든다.
- Gateway는 `call.run_id`, provider/source 매핑을 확인하고 canonical acquisition field를 만든다.
- Store는 run binding, 저장·조회, `(run_id, content_sha256)` 유일성과 link 멱등성의 정본이다.
- `raw_span` 정규화는 hash 계산에만 사용한다. 저장되는 `Evidence.raw_span`은 Adapter 값을 그대로 둔다.

## 3. MockAdapter reference contract

### 3.1 형태

하나의 `MockAdapter` 인스턴스는 provider 하나만 대표한다.

```text
MockAdapter(provider="dart")   → name="dart", max_concurrency=3
MockAdapter(provider="naver")  → name="naver", max_concurrency=3
MockAdapter(provider="kiwoom") → name="kiwoom", max_concurrency=1
```

`build_request`는 `q.provider != self.name`을 거부한다. 단일 인스턴스의 `name`을 호출마다
바꾸면 ProviderAdapter 계약과 adapter registry의 안정성이 깨진다.

### 3.2 5개 메서드

| 메서드 | 입력 | 출력 | 책임 | 절대 금지 |
|---|---|---|---|---|
| build_request | Query, aware `as_of` | Request | provider/endpoint/params와 timeout 구성 | canonical ID/hash/현재시각 생성 |
| acall | Request | deterministic raw dict | provider별 고정 응답 모사 | 네트워크·sleep·random·실시각 |
| parse_response | raw dict, Query | `list[EvidenceDraft]` | semantic field 추출·원문 정리·단위 정규화 | Evidence 생성, system field 주입 |
| classify_error | deterministic error dict | `(ReasonCode, retryable)` | 최소 공통 오류 계약 | 임의 문자열 reason |
| rate_limit_hint | error dict | `RateLimitHint | None` | 429 fixture에서 결정적 hint 제공 | 실제 provider header 규칙 추측 |

`base.py`에는 두 번째 ABC를 만들지 않는다. P0-3 `ProviderAdapter`가 유일한 인터페이스다.
G4에서 공통 HTTP 오류 분류가 두 곳 이상 실제 사용될 때만 작은 순수 helper를 두고, 그렇지 않으면
`MockAdapter`만으로 reference behavior를 보여준다.

### 3.3 재현성

Adapter가 허용하는 가변값은 없다. 같은 provider·Query·raw fixture를 두 번 처리하면 Request를
포함한 Adapter 결과가 완전히 같아야 한다. canonical 단계에서는 신규 row의 `evidence_id`와
승인된 `fetched_at`만 외부 주입값에 따라 달라질 수 있다. hash, dedup 결과, link 집합은 달라지면 안 된다.

### 3.4 provider별 고정 EvidenceDraft

| provider | fixture 제안 |
|---|---|
| dart | `source_type="dart"`; `source_ref="20250814000123"`; HTTPS DART URL; publisher=`"삼성전자"`; `2025-08-14T00:00:00+09:00`; 영업이익 한 문장; `structured_field`; metric/value/unit/period/yoy/amend_flag |
| naver | `source_type="news"`; `source_ref="news-0001"`; HTTPS 기사 URL; publisher=`"예시경제"`; aware KST 발행시각; 정리된 제목·snippet; `headline_snippet`; title/company/published_date metadata |
| kiwoom | `source_type="quote"`; `source_ref="ka10001:005930"`; URL `None`; publisher=`"키움증권"`; aware KST 거래시각; 종가·등락률·거래량 한 문장; `structured_field`; close/chg_pct/volume/trade_date |

매핑은 `dart→dart`, `naver→news`, `kiwoom→quote` 이외를 허용하지 않는다.
`published_at` aware 여부와 `published_at <= fixture 수집시각` 검사는 별개다. frozen에
`published_at <= as_of` 부등식을 새로 추가하지 않는다.

### 3.5 최소 오류 분류

| 입력 | ReasonCode | retryable |
|---|---|---:|
| HTTP 5xx | `UPSTREAM_5XX` | true |
| HTTP 429 | `RATE_LIMIT` | true |
| HTTP 401/403 | `AUTH_FAILED` | false |
| timeout marker | `UPSTREAM_TIMEOUT` | true |

404와 일반 connection error는 provider별 의미가 고정되지 않았다. Mock reference의 최소 계약에
추가하지 않는다. 실제 provider 카드는 명시된 body/status 매핑을 각자 구현한다. timeout이 아닌
connection error를 조용히 timeout으로 접지 않는다.

429 fixture에서는 `RateLimitHint(provider=name, retry_after_ms=1000, remaining=0,
window_s=1, source="status_only")`처럼 결정적 hint를 반환하고, 그 외에는 `None`을 추천한다.

## 4. EvidenceDraft → Evidence 실행 계약

### 4.1 추천 signature 후보

두 개의 기존 gap을 함께 닫는 추천 형태다. 아직 승인되지 않았다.

```python
async def assemble_evidence(
    drafts: list[EvidenceDraft],
    q: Query,
    call: ProviderCall,
    as_of: datetime,
    run_id: str,
    fetched_at: datetime,
    store: EvidenceStore,
) -> tuple[list[Evidence], int]: ...
```

그리고 EvidenceStore는 다음 freeze correction이 필요하다.

```python
async def put_many(self, run_id: str, evs: list[Evidence]) -> list[str]: ...
```

현재 `put_many(evs)`와 `Evidence`만으로는 Store가 `(run_id, content_sha256)` index를 만들 수 없다.
숨은 context나 `provider_request_id` 역조회를 전제하면 Protocol 밖 의존성이 생긴다.

### 4.2 단계별 순서

1. `call.run_id == run_id`, `call.query_id == q.query_id`, `call.provider == q.provider`,
   `call.endpoint == q.endpoint`를 대조한다. DDR 최소 2개보다 lineage 검사를 좁고 명시적으로 강화한다.
2. 모든 Draft의 `source_type == PROVIDER_SOURCE_TYPE[q.provider]`를 확인한다.
3. 각 Draft의 hash preimage를 만들고 SHA-256을 계산한다.
4. 같은 batch 내부의 동일 hash를 먼저 collapse한다.
5. `find_by_sha256(run_id, unique_hashes)`로 기존 canonical ID를 조회한다.
6. Store에 없는 hash에만 `evidence_id`와 acquisition field를 주입해 Evidence를 만든다.
7. `put_many(run_id, new_evidence)`로 신규만 저장한다.
8. batch 신규·기존 모두에 `(canonical evidence_id, q.query_id)` link를 만들고 `link()`한다.
9. `(new_evidence, dedup_count)`를 반환한다.

hash가 없으면 dedup key가 없으므로 조회를 먼저 할 수 없다. dedup 전에 ID를 만들면 중복 Draft에
버려질 ID를 발급하고 link가 그 임시 ID를 참조할 위험이 있다. 기존 Evidence도 새 Query에서
발견된 사실은 보존해야 하므로 link는 항상 만든다. dedup이 run 범위인 이유는 다른 run의
`as_of`를 재사용하면 snapshot consistency가 깨지기 때문이다.

### 4.3 batch 내부 duplicate

Store 조회 전 `hash → 대표 Draft` ordered map으로 collapse한다. 같은 hash의 첫 Draft를 대표로
유지하고 Adapter의 deterministic ordering을 계약 테스트로 고정한다. 입력 Draft 2건이 같은 hash면
canonical 신규 1건, dedup 1건, link 1건이다. Store에도 이미 있으면 신규 0건, dedup 2건이다.

향후 입력 순서 독립성이 필요해지면 대표 선택 canonical sort가 별도 결정이다. P0-4에서는 동일
hash가 identity이고 Adapter 출력 순서가 deterministic하므로 추가 정렬 정책을 만들지 않는다.

### 4.4 EvidenceQueryLink

- 신규 Evidence: link 생성.
- 기존 Evidence: 기존 ID로 link 생성.
- 동일 Evidence + 동일 Query 재실행: `(evidence_id, query_id)` set/PK로 1개 유지.
- 같은 Evidence가 다른 Query에서 발견됨: Query별 link를 각각 유지.
- Evidence 본문에 `query_id`를 복제하지 않는다.

### 4.5 반환 의미

추천은 Option A다.

```text
list[Evidence] = 이번 호출에서 신규 저장된 Evidence만
int            = 입력 Draft 중 batch/persisted duplicate 수
items_adopted  = len(new_evidence)
items_deduped  = dedup_count
items_fetched  = len(drafts)
```

따라서 `adopted + deduped == fetched`가 된다. n7/n8에 필요한 전체 ID는 정본 link에서
`evidence_ids_for_queries()`로 다시 읽는다. Option B는 기존 본문을 `get_many()`로 재조회해야 하고,
Option C DTO는 현재 소비자가 없어 YAGNI다.

## 5. 신규 gap 비교

### 5.1 fetched_at

| 안 | frozen 호환 | 의미 | replay | 테스트 | 변경량 |
|---|---:|---|---|---|---:|
| A `call.created_at` | 예 | created_at을 응답 완료로 재정의해야 함 | cache 사용 시 원 획득시각 소실 | 쉬움 | 작음 |
| B `created_at + latency_ms` | 예 | created_at=request start 전제가 문서에 없음 | 같은 문제 | 쉬움 | 작음 |
| C 별도 `fetched_at` 인자 | 예 | 호출자가 실제/재생 획득시각을 명시 | cache metadata 사용 가능 | 가장 명확 | 중간 |
| D ProviderCall 필드 추가 | frozen 변경 | 가장 풍부 | 명확 | 명확 | 가장 큼 |

**추천: C.** A/B는 `created_at`에 문서에 없는 의미를 덧씌운다. D는 frozen 변경에 비해 얻는 것이
작다. G4 테스트는 aware 고정시각을 주입하고, 실제 gateway는 응답 완료시각 또는 replay 원본
metadata를 넘긴다. ReplayCache metadata 계약은 P0-4에서 구현하지 않는다.

### 5.2 content hash normalize

| 안 | 중복 안정성 | 원문 왜곡/충돌 위험 | 단순성 |
|---|---|---|---:|
| A `strip()` | 줄바꿈·연속공백 차이를 못 접음 | 가장 낮음 | 최고 |
| B trim + 연속 whitespace 1칸 | provider formatting 차이를 접음 | 낮음 | 높음 |
| C NFKC + whitespace | 호환문자까지 접음 | 숫자·기호 의미를 과도하게 합칠 수 있음 | 보통 |

**추천: B.** `" ".join(raw_span.split())` 결과와 exact `source_ref`를 `|`로 결합하여 UTF-8
SHA-256을 계산한다. 표시용 raw_span은 수정하지 않는다. NFKC는 `①`, 전각 기호 같은 재무 원문의
구분을 합칠 수 있어 P0-4 증거 없이 적용하지 않는다.

### 5.3 반환형

- A 신규 Evidence만 + dedup count: 현 signature와 CollectionResult에 정확히 맞음. **추천.**
- B 신규+기존 Evidence + count: 기존 본문 재조회와 반환 순서 계약이 추가됨.
- C DTO: 의미는 가장 명확하지만 새 frozen/contract type이 필요해 현 소비자 대비 과함.

### 5.4 동일 batch duplicate

- Gateway가 Store 조회 전에 collapse한다. DDR의 “n6 1패스 내 중복 제거”와 일치한다.
- dedup count에는 batch duplicate와 persisted duplicate를 모두 포함한다.
- hash identity가 같은 Draft가 metadata만 다르면 첫 deterministic Draft를 대표로 쓴다.

### 5.5 EvidenceStore run binding — 추가 발견

`find_by_sha256(run_id, hashes)`와 Postgres UNIQUE는 run-scoped인데 `put_many(evs)`에는 run_id가 없다.

- A `put_many(run_id, evs)`로 Protocol 보정: 명시적이고 Memory/Postgres가 동일. **추천.**
- B Store가 provider_request_id로 run을 역조회: Protocol에 ProviderCall 저장/조회가 없어 hidden dependency.
- C Evidence에 run_id 추가: frozen 모델 변경이며 persistence binding을 도메인 DTO에 섞음.

이 보정 없이 MemoryEvidenceStore를 정직하게 구현할 수 없으므로 G4 blocker다.

## 6. MemoryEvidenceStore

목적은 S0와 gateway contract test 전용 reference implementation이다. 운영 사용은 금지한다.
승인된 EvidenceStore Protocol 전 메서드를 구현한다.

```text
queries_by_id:            query_id → Query
query_run_by_id:          query_id → run_id
evidence_by_id:           evidence_id → Evidence
evidence_run_by_id:       evidence_id → run_id
evidence_id_by_run_hash:  (run_id, content_sha256) → evidence_id
links:                    set[(evidence_id, query_id)]
```

`claim_id → evidence_id` 별도 index는 두지 않는다. Query의 `claim_id`와 link를 조인해 계산한다.
중복 상태를 두 군데 저장하지 않는 것이 reference 구현의 핵심이다.

결정론적 조회 규칙 추천:

- 명시적 ID list를 받는 `get_queries/get_many`: 요청 ID 순서를 보존하고 없는 ID는 생략하지 말고
  `KeyError`로 계약 위반을 드러낸다.
- 관계 조회 `evidence_ids_for_claim/evidence_ids_for_queries`: 중복 제거 후 `evidence_id` 오름차순.
- `put_queries/put_many`: 입력 순서대로 ID 반환.
- `link`: set add로 멱등.
- 동일 evidence_id를 다른 payload/run으로 재삽입하면 `ValueError`; 완전히 같으면 idempotent.

Store의 find→put 동시성 race는 Memory reference의 단일 프로세스 범위 밖이며 T2-C의 DB UNIQUE와
transaction이 최종 강제점이다. Postgres 구현은 unique conflict 시 기존 canonical ID를 회수해야 한다.

## 7. MemoryReviewStore

ReviewStore 12개 메서드를 전부 구현하는 S0 전용 reference다.

```text
input_by_id / input_id_by_run
claims_by_id / claim_run_by_id
claim_evidence_by_key[(run_id, claim_id, evidence_id)]
evaluations_by_id / evaluation_id_by_run_claim[(run_id, claim_id)]
findings_by_id / finding_run_by_id
reports_by_id / report_id_by_run
```

`put_claim_evaluations`는 `(run_id, claim_id)` 기준 upsert한다. 재수집에서 새 평가가 오면 이전
evaluation ID의 본문을 제거하고 새 ID를 current로 만든다. `get_claim_evaluations(ids)`는 요청된
ID 중 현재 존재하는 것만 요청 순서로 반환한다. State의 `add_unique`에 이전 ID와 새 ID가 함께
남더라도 n9에는 최신 1건만 도달해 중복 리포트를 막는다.

`put_input`과 `put_report`도 run당 1개 의미를 유지한다. claims/findings는 canonical ID 기준
idempotent insert, claim evidence는 `(run, claim, evidence)` upsert다. 운영 보존·DB transaction은
구현하지 않는다.

## 8. CODEOWNERS

| 안 | 병렬 개발 | reference 목적 | 기존 패턴 | 결론 |
|---|---|---|---|---|
| A `/app/store/memory_evidence_store.py @팀원3` 예외 | 팀원2 Postgres와 독립 | 충족 | memory_review_store 예외와 동일 | **추천** |
| B 팀원2 구현 | T2-C 일정에 S0 종속 | 약화 | store 기본 소유와 일치 | 비추천 |
| C 팀원3 디렉터리로 이동 | 독립 | 충족 | Store 구현이 다른 위치로 분산 | 비추천 |

G4에서 CODEOWNERS의 기존 예외 설명을 “세 파일”로 갱신하고 명시적 경로를 추가한다.

## 9. 파일 구조

| 파일 | 단일 책임 |
|---|---|
| `app/gateway/adapters/mock.py` | provider별 deterministic ProviderAdapter reference |
| `app/gateway/adapters/base.py` | Protocol을 대체하지 않는 공통 순수 helper가 실제로 필요할 때만 사용 |
| `app/gateway/assemble.py` | Draft 검증·hash·dedup·canonical injection·link |
| `app/store/memory_evidence_store.py` | EvidenceStore in-memory reference |
| `app/store/memory_review_store.py` | ReviewStore in-memory reference |
| `app/store/protocols.py` | 승인 시 `put_many(run_id, evs)` freeze correction만 |
| `CODEOWNERS` | memory_evidence_store 팀원3 예외 |

불필요한 abstract base class, 실제 gateway orchestration, cache, provider I/O는 만들지 않는다.

## 10. 테스트 설계

| 파일 | 증명할 계약 |
|---|---|
| `tests/gateway/test_mock_adapter.py` | 5메서드, provider 고정, Draft-only, aware time, HTTPS/None, normalized value, error/hint, determinism |
| `tests/gateway/test_assemble.py` | lineage/source validation, hash, batch+persistent dedup, canonical injection, link, run scope, 반환 의미 |
| `tests/store/test_memory_evidence_store.py` | Protocol 전 메서드, run hash index, idempotent link/put, derived claim relation, ordering |
| `tests/store/test_memory_review_store.py` | 12메서드, run uniqueness, evaluation upsert, ordering, idempotency |

### 10.1 assemble 필수 케이스

- `call.run_id != run_id`, provider/query/endpoint lineage 불일치 → `CONTRACT_VIOLATION`.
- `q.provider=naver`, Draft `source_type=dart` → `CONTRACT_VIOLATION`.
- 같은 Draft 동일 run 2회 → 같은 hash, Evidence 1행, 두 번째 dedup.
- 같은 내용 다른 run → 별도 Evidence 허용.
- 기존 Evidence + 새 Query → 새 link.
- 동일 Evidence + 동일 Query 재실행 → link 1개.
- batch 내부 duplicate → canonical 1행, dedup count 1.
- `as_of`, `provider_request_id`, 승인된 `fetched_at`이 입력값 그대로 주입.
- 반환 list는 신규만, `len(new)+dedup == len(drafts)`.

### 10.2 hash 케이스

추천 B 승인 시 앞뒤 공백·연속 공백·줄바꿈은 같은 hash다. Unicode 호환문자와 의미가 다른
문장은 같은 hash라고 기대하지 않는다. NFC/NFKC 동등성 테스트는 승인 범위에 없으므로 넣지 않는다.

### 10.3 MemoryStore 케이스

- `find_by_sha256`가 run 범위를 넘지 않음.
- 동일 ID+동일 payload는 idempotent, 동일 ID+다른 payload/run은 거부.
- link set 멱등.
- explicit lookup은 요청 순서, derived lookup은 ID 정렬.
- claim relation은 Query.claim_id + link에서 유도됨.
- evaluation upsert 후 이전 ID를 함께 조회해도 최신 평가만 반환됨.

## 11. 접근안 A/B/C

| 안 | 범위 | S0 가능성 | 병렬 개발 | 책임 분리 | rollback | YAGNI |
|---|---|---|---|---|---|---|
| A 카드 최소 | MockAdapter + assembler + MemoryReviewStore | EvidenceStore 부재로 n6 중단 | T2-C에 종속 | 불완전 | 쉬움 | 표면상 작음 |
| B vertical-slice-ready | A + MemoryEvidenceStore + CODEOWNERS + explicit DI/run binding | Postgres 없이 n5~n8 경계 관통 | 가장 좋음 | 명확 | 파일별 쉬움 | 필요한 만큼 |
| C 다음 카드 선행 | B + n6 helper + ReplayCache | 높음 | scope가 섞임 | cache/orchestration 혼합 | 어려움 | 과함 |

**추천: B.** A는 P0-4의 S0 목적을 달성하지 못하고, C는 T2-D와 n6 소유 범위를 선행한다.

## 12. 승인 후 P0-4 G4 TDD 계획

1. `tests/gateway/test_mock_adapter.py` 작성. 실행: 해당 파일 pytest. 예상 RED: mock 모듈 부재.
   GREEN: provider별 5메서드·determinism·Draft-only 계약 통과.
2. `app/gateway/adapters/mock.py` 최소 구현. 공통 helper가 실제 중복될 때만 base.py 추가.
3. `tests/store/test_memory_evidence_store.py` 작성. 예상 RED: store 모듈과 run-aware put 계약 부재.
4. 승인된 `app/store/protocols.py` correction과 `memory_evidence_store.py` 구현.
   GREEN: run dedup, 전 메서드, ordering, link 멱등 통과.
5. `tests/store/test_memory_review_store.py` 작성. 예상 RED: 모듈 부재.
6. `memory_review_store.py` 구현. GREEN: 12메서드와 evaluation upsert 통과.
7. `tests/gateway/test_assemble.py` 작성. 예상 RED: assembler 모듈 부재.
8. `assemble.py` 최소 구현. GREEN: lineage, hash, batch/store dedup, link, 반환 의미 통과.
9. mutation: source 검증 제거, whitespace normalize 제거, batch collapse 제거, run scope 제거,
   duplicate link 허용, fetched/as_of 교환, evaluation upsert 제거 시 관련 테스트 실패 확인 후 복구.
10. 대상 pytest → 전체 pytest → Ruff → `git diff --check` → frozen diff 없음 확인.
11. CODEOWNERS에 정확한 memory evidence 예외만 추가하고 P0-4 문서/status를 최소 동기화.
12. 변경 목록에서 실제 provider/Postgres/ReplayCache/n6/P0-5/CI 파일 0건 확인 후 구현·문서 커밋 분리.

각 production function은 테스트가 모듈/동작 부재라는 예상 이유로 RED인 것을 먼저 확인한다.

## 13. 결정 등급과 승인 요청

| 항목 | 등급 | 제안 |
|---|---|---|
| Adapter→Draft, Gateway→canonical, Store→정본 | DDR_DECIDED | 변경 없음 |
| provider/source 매핑 | FROZEN_DECIDED | 변경 없음 |
| dedup run scope, link PK | DDR_DECIDED | 변경 없음 |
| Adapter 5메서드와 최소 오류 4종 | DDR_DECIDED | 변경 없음 |
| assemble Store DI | APPROVED_DIRECTION_CONFIRM_G4 | 함수 인자 A |
| `put_many` run binding | USER_APPROVAL_REQUIRED | `put_many(run_id, evs)` |
| fetched_at | USER_APPROVAL_REQUIRED | 별도 aware 인자 C |
| hash normalization | USER_APPROVAL_REQUIRED | whitespace B |
| 반환 의미 | USER_APPROVAL_REQUIRED | 신규 list + dedup count A |
| batch duplicate | USER_APPROVAL_REQUIRED | Gateway pre-collapse |
| MemoryEvidenceStore | APPROVED_DIRECTION_CONFIRM_G4 | P0-4 추가 |
| CODEOWNERS | USER_APPROVAL_REQUIRED | 팀원3 명시 예외 A |
| MemoryStore ordering | USER_APPROVAL_REQUIRED | explicit=request order, derived=ID sort |
| G4 범위 | USER_APPROVAL_REQUIRED | 접근안 B |

## 14. 가장 확신이 낮은 결정

`fetched_at`이다. 실 API 응답 완료시각은 명확하지만 cache hit에서 “원 응답 획득시각”을 어디서
복원할지 ReplayCache 계약에 metadata가 없다. 별도 인자는 assembler의 의미를 정직하게 만들지만,
실제 cache 계층의 provenance 문제를 해결하지는 않는다. P0-4에서는 aware 시각의 명시적 주입만
고정하고, replay 원본 시각 보존은 T2-D 설계에서 별도로 닫아야 한다.

## 15. G4 close-out

- `assemble_evidence`는 explicit Store DI와 caller-injected aware `fetched_at`을 사용한다.
- EvidenceStore correction은 `put_many(run_id, evs)`로 확정했다.
- 같은 hash + 같은 payload만 collapse하며, 다른 payload는 순서 무관 `CONTRACT_VIOLATION`이다.
- MemoryEvidenceStore와 MemoryReviewStore가 P0/S0 reference contract를 제공한다.
- `FREEZE_CORRECTION_CANDIDATE: HASH_SERIALIZATION_AMBIGUITY`는 유지한다.
- T2-D FOLLOW-UP: ReplayCache hit의 원 `fetched_at` provenance 복원 계약.
- P0-7 FOLLOW-UP: I3/I4 thin CI wrapper.
