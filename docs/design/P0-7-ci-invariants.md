# P0-7 CI Invariants Fact Map + Phase-Aware CI 설계

## 1. 현재 상태

- 기준 브랜치/HEAD: `main` / `bffe5a2`
- P0-1~P0-6: 완료, P0-4~P0-6 STRICT CLOSED
- 현재 카드: P0-7 G2/G3 승인 완료, G4 구현 완료, P0 PHASE CLOSED
- fresh baseline: `uv run pytest -q -p no:cacheprovider`는 `267 passed`, `uv run ruff check .`는 통과했다.
- `PYTHONUTF8=1`에서 `uv run python -m ci.invariants`는 I1~I10을 미구현으로 출력하고 I11만 PASS했지만 exit code 0이었다.
- Windows 기본 cp949에서는 첫 상태 기호 출력 시 `UnicodeEncodeError`로 exit 1이 발생했다. 이는 invariant 판정 실패가 아니라 runner 자체 출력 인코딩 결함이다.
- 사용자 로컬 파일 `.claude/settings.local.json`은 변경하지 않는다. 이 문서 외 구현·테스트·계약 파일 변경과 commit은 하지 않는다.

## 2. P0-7 비개발자 설명

P0-7은 이미 만든 안전장치를 새로 복제하는 작업이 아니다. 기존 테스트와 측정 도구를 CI의 한 진입점에서 호출하고, 아직 제품 경로가 없는 검사는 노란색 PENDING으로 정직하게 표시하는 작업이다. 현재 단계에서 존재하지 않는 그래프·DB·체크포인터를 가짜로 만들어 11/11처럼 보이게 하지 않는다.

## 3. I1~I11 정본 표

| I | 정본 의미 | 근거 |
|---|---|---|
| I1 | 실제 체크포인트 blob `< 5KB` | D-23 |
| I2 | 리듀서 순서 독립성: 셔플 5회 결과 1종 | D-15 |
| I3 | 8개 LLM 노드의 `ctx_chars <= budget` | D-28 |
| I4 | 8개 View의 `model_fields`에 금지 필드 부재 | D-28 |
| I5 | Evidence 중복 방지 `UNIQUE(run_id, content_sha256)` | F4, D-14 |
| I6 | 루프 종료 6개 방어와 `total_llm_calls <= 4C+9` | D-13, F2 |
| I7 | `CitationRef.span`이 해당 `Evidence.raw_span`에 포함 | F5 |
| I8 | canonical 4종을 prompts/nodes의 `output_schema=`로 사용 금지 | v2.2 S-9 |
| I9 | Adapter `source_type == PROVIDER_SOURCE_TYPE[provider]` | v2.2 S-7 |
| I10 | State 참조 채널 6개 모두 Store 접근 경로 보유 | v2.2 §3 |
| I11 | C=4/6/8 대표 State 직렬화 크기 `<= 5120B` | v2.2 §5.1 |

## 4. Evidence Matrix

| I | 현재 증거 | 수준 | P0-7 역할 | 후속/활성화 |
|---|---|---|---|---|
| I1 | 실제 Saver/Checkpointer와 save 경로 없음 | `PENDING_ARTIFACT` | 명시적 PENDING | S0의 실제 checkpointer 도입 시 |
| I2 | seeded shuffle 5회 결과 1종을 검증하는 기존 reducer test | `PASS` | exact pytest node W1 | P0 REQUIRED |
| I3 | `NODE_BUDGETS`, `ctx_chars/items`, truncate, synthetic View 테스트; 실제 8 node 없음 | `PARTIAL` | static 근거는 보존하되 전체 I3는 PENDING | S0 node 연결 시 runtime 검증 |
| I4 | `tests/contexts/test_views.py`의 8개 View exact field-set/금지 필드 검사 | `PASS` | 정확한 pytest node W1 | P0 REQUIRED |
| I5 | MemoryEvidenceStore run-scoped dedup 및 mutation 방어; PostgreSQL DDL 없음 | `PARTIAL` | reference 방어는 기존 pytest로 유지, 물리 UNIQUE를 PASS로 오인 금지 | T2-C/T2-G DB migration 및 integration |
| I6 | DDR의 상한·라우팅 계약만 존재; graph/router/node 없음 | `PENDING_ARTIFACT` | reason/activation 고정 | S0 mock graph, 이후 production graph |
| I7 | assembler는 ID allowlist만 검사하며 Evidence 본문을 받지 않음 | `CONTRACT_GAP` | CI tautology 금지, owner 승인 요청 | S0 전 deterministic validator |
| I8 | native alias-aware AST scanner 구현; production Python artifact 없음 | `PENDING_ARTIFACT` | 빈 scope를 PENDING으로 처리 | S0 실제 source 등장 시 REQUIRED |
| I9 | P0-6 공통 contract `test_source_type_matches_provider` | `PASS` | exact pytest node W1 | P0 REQUIRED |
| I10 | native 6채널↔Store read-path mapping 검사 | `PASS` | 얇은 architecture check | P0 REQUIRED |
| I11 | `tools/measure_state.py --assert-under 5120` 재사용 | `PASS` | 기존 측정 도구 유지 | P0 REQUIRED |

## 5. I1 vs I11 차이

I11은 대표 Python State를 정적으로 직렬화해 채널 증가 회귀를 조기에 잡는다. fresh 값은 C=4 `3016B`, C=6 `3248B`, C=8 `3480B`다. I1은 실제 LangGraph 실행 후 Saver가 저장하는 blob을 측정해야 한다. 현재 checkpointer가 없으므로 I11을 다시 호출해 I1 PASS로 표시하는 것은 금지한다. I1은 S0 또는 실제 checkpointer 도입 카드부터 REQUIRED다.

## 6. I3 static vs runtime 차이

P0-3은 8개 node budget 상수, 문자/항목 계측, truncate, synthetic View 상한을 잘 증명한다. 그러나 `n1,n3,n4,n7,n8,n9,n10,n11` 구현이 없어 실제 node가 만든 View는 측정할 수 없다. 같은 I3 안에서 현재 증거를 `static evidence`로 기록하되 최종 상태는 PENDING으로 둔다. S0에서 각 실제 node의 assembly 직후 `ctx_chars(view) <= NODE_BUDGETS[node]`를 기존 helper로 검증해야 PASS가 된다.

## 7. I5 reference vs DB 차이

MemoryEvidenceStore는 `(run_id, content_sha256)` 의미적 유일성과 run 분리를 구현하며 기존 store/gateway 테스트와 mutation이 이를 보호한다. 이는 reference semantics의 VERIFIED이지 PostgreSQL의 물리 `UNIQUE` constraint 증거가 아니다. 현재 migration/DDL이 없으므로 전체 I5는 PARTIAL이다. 같은 I5 아래 P0 reference, T2 DB DDL, integration concurrent persistence의 defense-in-depth 근거를 누적하되, 물리 DB 단계 전에는 strict PASS로 올리지 않는다.

## 8. I6 현재 검증 가능 여부

정본에서 복원되는 종료 방어는 다음 여섯 계열이다.

1. `hitl_reask <= 2` 및 소진/`TIMEOUT_HITL` 시 n5 진행
2. `graph_recollect <= 1` 및 소진 시 n10 진행
3. n10 rewrite `<= 2`, 잔존 위반 시 n12 차단
4. `total_external_calls <= 25`
5. `total_llm_calls <= 4C+9`(C=8이면 41)
6. `BUDGET_EXCEEDED`, `CONTEXT_OVERFLOW`, `CONTRACT_VIOLATION`, `TIMEOUT_MACHINE`의 n12 직행

현재는 이를 집행할 graph/router/node가 없다. State counter나 문서 상수만 검사해 정지성을 PASS로 선언할 수 없으므로 I6은 PENDING_ARTIFACT다. S0의 실제 순수 routing과 mock graph가 최악 경로를 통과할 때 활성화한다.

## 9. I7 enforcement gap

P0-5 assembler는 citation의 `evidence_id`가 evaluation allowlist에 있는지는 검사하지만 `Evidence.raw_span`을 입력받지 않으므로 substring 포함 관계를 판정할 수 없다. render/report 실행 경로도 아직 없다. 권장 owner는 n11 report assembly 직전의 별도 deterministic citation resolver/validator다. 이 계층이 EvidenceStore에서 인용 대상을 읽고 모든 span을 검증한 뒤에만 canonical report 저장/렌더를 허용해야 한다. P0-7에서 synthetic 문자열 두 개를 비교하는 테스트는 제품 enforcement를 증명하지 않으므로 만들지 않는다. 이 owner와 S0 전 foundation 카드 분리를 사용자 승인 사항으로 둔다.

## 10. I8 AST/vacuous pass

MockModelGateway의 output allowlist는 보조 runtime defense이며 I8 AST invariant를 대체하지 않는다. native checker는 Python AST에서 `output_schema=` keyword의 값이 `Evidence`, `ClaimEvidence`, `ClaimEvaluation`, `Finding` 중 하나인지 탐지해야 한다. 현재 `app/prompts/`에는 실행 Python 파일이 없고 `app/orchestration/nodes/`도 실 node가 없다. 따라서 “위반 0건”만으로 PASS하지 않고, manifest가 요구하는 최소 scan artifact가 없으면 `PENDING_ARTIFACT`를 반환한다. 실제 node/prompt Python 파일 등장 시 scope 감지가 자동으로 I8을 REQUIRED로 승격시키는 대신, 예측 불가능한 자동 phase 변경은 피하고 S0 manifest 승격과 artifact-presence assertion을 함께 둔다.

## 11. I9 P0-6 재사용

`tests/adapters/test_contract.py::TestProviderContract::test_source_type_matches_provider`가 frozen `PROVIDER_SOURCE_TYPE`을 직접 사용해 모든 adapter fixture를 검사한다. CI에 provider별 분기나 mapping을 다시 쓰지 않고 이 exact pytest node를 W1로 실행한다.

## 12. I10 State/Store mapping

| State 채널 | Store | write/read 경로 |
|---|---|---|
| `input_id` | ReviewStore | `put_input` / `get_input` |
| `claim_ids` | ReviewStore | `put_claims` / `get_claims` |
| `query_ids` | EvidenceStore | `put_queries` / `get_queries` |
| `claim_evaluation_ids` | ReviewStore | `put_claim_evaluations` / `get_claim_evaluations` |
| `finding_ids` | ReviewStore | `put_findings` / `get_findings` |
| `report_id` | ReviewStore | `put_report` / `get_report` |

YAGNI 기준 최소 검사는 (a) ReviewState annotation에 정확한 6개 채널이 존재하고, (b) 정적 mapping table이 가리키는 Protocol에 put/get method가 callable로 존재하며, (c) ID channel의 scalar/list 형태와 put/get의 scalar/list 반환·입력 형태가 호환되는지만 본다. Store 동작이나 데이터 변환을 재구현하지 않는다. 현재 protocol signature 테스트는 양쪽을 따로 증명하므로 I10은 PARTIAL이며 이 native cross-map check가 닫는다.

## 13. 현재 ci.invariants exit-code 문제

현재 `_todo()`는 `None`을 반환하고 main은 TODO를 경고만 한 뒤 0을 반환한다. 따라서 UTF-8 환경의 CI는 “미구현 10/11”을 출력해도 job을 GREEN으로 인식한다. 이는 “미구현을 조용히 통과시키지 않는다”는 파일 설명과 exit semantics가 충돌하는 false-green이다. 별도로 부모 stdout을 UTF-8로 보장하지 않아 cp949 콘솔에서는 판정 전 crash한다. G4에서는 결과 의미 수정과 출력 portability를 함께 다루되, crash를 invariant FAIL로 오인하지 않는 회귀 테스트가 필요하다.

## 14. 접근안 A/B/C

| 접근 | 장점 | 결정적 문제 | 판정 |
|---|---|---|---|
| A. 지금 11개 모두 PASS | 표면상 단순 | fake runtime, vacuous AST, I7 tautology, Memory=DB 오인 | 기각 |
| B. Phase-aware CI | 현재 계약 즉시 잠금, 미래 artifact를 정직하게 표시 | manifest/exit 규칙 필요 | 추천 |
| C. S0 뒤로 P0-7 이동 | 최종 11/11 의미 단순 | S0 동안 foundation 회귀 보호가 늦음 | 기각 |

## 15. 추천안

Approach B를 채택한다. P0에서 I2·I4·I9·I10·I11을 REQUIRED로 잠근다. I1·I3·I6·I8은 S0, I5 물리 invariant는 T2에서 REQUIRED로 승격한다. I7은 owner가 승인되고 실제 validator가 추가되는 S0 gate에서 REQUIRED로 만든다. PENDING은 노란색이며 PASS 개수에 포함하지 않는다.

## 16. phase manifest 설계

외부 YAML은 사용하지 않고 코드의 작은 `InvariantSpec` 목록(M2)을 추천한다.

```python
@dataclass(frozen=True)
class InvariantSpec:
    name: str
    label: str
    check: Callable[[], CheckResult] | None
    required_from: Phase
    pending_kind: PendingKind | None = None
    pending_detail: str = ""
```

phase 순서는 `p0 < s0 < t2`로 고정한다. P0 REQUIRED는 `{I2,I4,I9,I10,I11}`, S0 추가 REQUIRED는 `{I1,I3,I6,I7,I8}`, T2 추가 REQUIRED는 `{I5}`다. `check is None`이면서 현재 required이면 runner가 즉시 FAIL한다. 미래 phase라면 manifest에 사유가 있는 경우에만 PENDING이다. I3·I5처럼 선행 partial evidence가 있는 항목은 detail에 기록하며 상태를 PASS로 승격하지 않는다.

## 17. PASS/FAIL/PENDING result 설계

`tuple[bool, str] | None`을 dependency 없는 Enum+dataclass로 바꾼다.

```python
class CheckStatus(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"

@dataclass(frozen=True)
class CheckResult:
    status: CheckStatus
    detail: str
```

`PENDING_ARTIFACT`, `PARTIAL`, `CONTRACT_GAP`은 별도 pass 상태가 아니라 PENDING의 reason kind다. CONTRACT_GAP은 더 눈에 띄는 빨간 표식을 쓸 수 있지만 exit 판단은 manifest와 required phase가 결정한다. 승인되지 않은 PENDING은 항상 실패다.

## 18. --phase / --strict / --only semantics

- 기본 실행은 `--phase p0`와 동일하다.
- `--phase p0`: 선택 범위에서 현재 REQUIRED가 모두 PASS하고, 나머지가 manifest에 승인된 future PENDING일 때만 exit 0.
- `--strict`: I1~I11 중 하나라도 FAIL/PENDING이면 exit 1. `--phase`와 상호 배타적으로 둔다.
- `--only I2,I4`: 먼저 이름을 검증하고 선택 범위를 줄인다. 중복은 제거하되 요청 순서는 유지한다.
- `--phase p0 --only I2,I4`: 선택된 두 항목의 p0 의미만 판정한다.
- `--strict --only I1,I2`: 선택된 두 항목 모두 실제 PASS여야 exit 0.
- unknown/empty `--only`, required checker 누락, 예상 밖 PENDING, checker exception은 exit 1이다.

## 19. thin-wrapper W1/W2/W3 비교

| 방식 | 장점 | 단점 | 사용 원칙 |
|---|---|---|---|
| W1 exact pytest subprocess | 가장 surgical, 기존 test가 source of truth | process 비용, node rename 취약 | I2·I4·I9 기본 선택 |
| W2 pure helper 공유 | 빠르고 구조적 | helper 추출 때문에 기존 코드 변경 | 이미 안정적 helper가 있거나 W1 비용이 실제 문제가 될 때만 |
| W3 CI 재구현 | 독립 실행 | 중복·drift | 금지 |

I11은 기존 measure tool 직접 호출을 유지한다. G4에서 대규모 helper 리팩터링은 하지 않으며 W1/W2 혼합을 허용한다.

## 20. P0-7 native checker 대상

- I8: AST source invariant의 정본 owner. import alias/attribute 형태를 포함하되, 문자열 검색으로 대체하지 않는다. root/artifact 부재는 PASS가 아니다.
- I10: ReviewState 6채널과 Store Protocol method의 얇은 정적 cross-map 검사.
- runner 자체: phase selection, 3상태 출력, exit code, `--only`, strict, expected/unexpected pending.

I2·I4·I9 validation과 I11 측정 로직은 native로 복제하지 않는다.

## 21. S0에서 activate할 invariant

- I1: 실제 graph 실행 후 checkpointer/Saver가 저장한 blob 측정
- I3: 8개 실제 LLM node가 만든 View의 runtime budget 검사
- I6: 실제 순수 router와 mock graph의 두 cycle/상한/비상 종료 검사
- I7: 승인된 deterministic citation validator의 실제 reject path
- I8: 실제 prompts/nodes artifact에 대한 AST scan

S0 시작 시 한꺼번에 가짜 PASS시키지 않고 artifact가 도입되는 변경과 같은 PR에서 해당 spec을 REQUIRED로 승격한다. production graph 도입 후 I6 target은 mock-only에서 production routing target으로 교체한다.

## 22. G4 mutation 계획

1. runner 상태/phase 테스트를 먼저 RED로 작성한다: TODO false-green, required missing, expected future pending, unexpected pending, strict pending, `--only` 조합, unknown name, UTF-8 출력.
2. I2 테스트를 deterministic seeded shuffle 5회/semantic result 1종으로 최소 보강하고, reducer mutation 시 RED를 확인한다.
3. I4 exact field test와 I9 common contract를 W1로 연결하고 각 기존 mutation 성격(금지 필드 추가, provider mapping 불일치)이 runner RED로 전파되는지 확인한다.
4. I10에서 채널 또는 mapped protocol method 하나를 제거/변경하면 RED인지 확인한다.
5. I8 forbidden `output_schema` fixture는 RED, 안전 draft schema는 PASS, scan artifact 부재는 PENDING인지 확인한다.
6. I11 threshold/State 팽창 mutation이 기존 측정 경로를 통해 RED인지 확인한다.
7. I1·I3·I5·I6·I7의 future/partial 상태가 PASS로 출력되지 않으며 `--strict`에서 RED인지 확인한다.
8. fresh `pytest`, `ruff`, phase p0, strict(expected RED while pending) 결과를 분리 보고한다.

## 23. P0-7 완료 판정 문구

G4의 올바른 close-out은 다음처럼 표현한다.

```text
P0 phase invariants: PASS (I2, I4, I9, I10, I11)
Future/Pending invariants: I1, I3, I5, I6, I7, I8
All-11 strict: NOT YET (expected exit 1)
```

“P0-7 11/11 PASS” 또는 “all invariants green”이라고 쓰지 않는다. P0-7 완료는 phase-aware runner와 현재 foundation gate가 닫혔다는 뜻이지, 아직 없는 runtime/DB 계약까지 검증했다는 뜻이 아니다.

## 24. 사용자 승인 필요 항목

1. Approach B phase-aware CI 채택
2. P0 REQUIRED를 I2·I4·I9·I10·I11로 확정
3. S0 REQUIRED 승격을 I1·I3·I6·I7·I8, T2를 I5로 확정
4. PASS/FAIL/PENDING 및 pending reason model 채택
5. `--phase`/`--strict` 상호 배타, `--only` 조합 semantics 승인
6. 기존 검증은 W1 우선, 안정 helper가 필요할 때만 W2, W3 금지
7. I8에서 빈 scan scope를 PENDING으로 처리하고 S0 artifact presence를 요구
8. I7 owner를 n11 직전 deterministic citation resolver/validator로 확정하고 S0 전 별도 foundation 작업 승인
9. I5를 Memory reference/T2 DB/integration의 동일 invariant 다층 방어로 관리
10. G4가 코드·테스트를 구현하되 graph/node/checkpointer/DB/I7 validator/S0 자체는 만들지 않는 범위 승인

### G4 close-out evidence

- `CheckStatus`: PASS/FAIL/PARTIAL/PENDING/CONTRACT_GAP
- default command는 `--phase p0`와 동일하며 P0 REQUIRED 5/5 PASS, exit 0
- strict는 모든 I가 PASS여야 하므로 현재 의도대로 exit 1
- I2/I3/I4/I5/I9는 기존 pytest/tool을 thin wrapper로 재사용
- I8은 alias-aware AST 검사와 vacuous-pass 방지, I10은 native State/Store mapping 검사
- CI unit tests 28 passed, 전체 pytest 295 passed, Ruff PASS
- mutation M1~M11 `TOTAL 11/11`

Activation backlog:

- S0: I1 runtime checkpoint, I3 runtime View budget, I6 loop termination, I7 citation containment enforcement, I8 production nodes/prompts AST
- T2: I5 PostgreSQL physical `UNIQUE(run_id, content_sha256)`

**P0-7 G4 CLOSED — P0 phase required invariants 5/5 GREEN. All I1~I11 strict status is NOT YET GREEN.**
