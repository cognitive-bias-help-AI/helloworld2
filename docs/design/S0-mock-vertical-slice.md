# S0 Mock Vertical Slice / Runtime Invariant Activation 설계

> **S0 CLOSED 2026-08-14.** Amendment E, 14 runtime vertices, happy/degraded/HITL,
> render→I7→guard→publish lifecycle, I1/I3/I6/I7/I8와 28/28 mutation이 구현·검증됐다.
> 최종 운용 경계와 intentional partial은 `docs/FINAL_SKELETON_HANDOFF.md` 및
> `docs/SKELETON_FREEZE_MANIFEST.md`가 정본이다.

> **APPROVED IMPLEMENTATION AMENDMENT — S0 G4**
>
> A. Stock ambiguity는 n4/n3b가 아니라 n2가 소유한다. n2가
> `StockChoiceRequest`로 interrupt하고 `StockChoiceResume`을 받아 원래 후보 집합에
> 포함되는지 deterministic 검증한 뒤 stock을 확정하고 n3로 간다.
>
> B. Render/guard lifecycle은 `n11 GENERATE → I7 → n10 GUARD → n11 PUBLISH`다.
> violation이 있고 rewrite 횟수가 2 미만이면 n11 GENERATE로 되돌아가며, 2회 후에도
> 잔존하면 report를 저장하지 않고 n12로 간다.
>
> C. RenderDraft/report body는 ReviewState에 넣지 않는다. `run_id`로 접근하는
> memory-only `RenderCandidateStore`를 RuntimeDeps에 주입한다. 운영 durability는
> `RENDER_CANDIDATE_DURABILITY_FOLLOW_UP`으로 남긴다.
>
> D. I1은 actual LangGraph saver가 받은 checkpoint 전체를 saver의 실제 serializer로
> 직렬화한 payload bytes로 측정한다. ReviewState/channel_values만 별도로 JSON dump한
> 값은 I1 증거로 인정하지 않는다.

## S0 G4 implementation preflight blocker record

2026-08-13 fresh repository inspection에서 다음 Hard Blocker가 확인되어 G4 production/test
구현을 시작하지 않았다.

1. `STOCK_RESOLUTION_SOURCE_BLOCKER`: `app/domain/stock_master.py`와 주입 가능한 stock
   resolution protocol/fixture가 없다. 정본 Task Card는 T1-B 미래 구현만 정의하며 현재
   `app/domain/__init__.py`만 존재한다. 후보를 deterministic recomputation할 source 없이
   StockChoice membership을 검증할 수 없다.
2. `RENDER_REWRITE_FEEDBACK_CONTRACT_BLOCKER`: 현재 `RenderView`는
   `{slots,banners,theory_notes,citations}`뿐이고 `RenderDraft`는 `slots`뿐이며
   `GuardVerdictDraft`는 `violations`뿐이다. violation/rewrite feedback을 다음 n11 GENERATE에
   전달할 승인 계약이 없다.
3. `REPORT_ASSEMBLY_CONTRACT_BLOCKER`: frozen schema에 canonical `Report`가 없고
   ReviewStore는 `body: dict`만 받는다. 문서는 deterministic markdown/report assembler를
   후속 경계로 지칭하지만 exact body/schema와 system-owned field assembly가 구현·고정되어
   있지 않다.
4. `SECOND_COVERAGE_EDGE_BLOCKER`: P0-5 설계와 status가 두 번째 coverage 실패 이후의
   저장/skip/edge/banner 정책을 명시적으로 follow-up으로 남겼다. Full graph node가 이
   실패를 만났을 때의 정본 edge가 없다.
5. `INITIAL_INPUT_CONTRACT_BLOCKER` (G4 resume에서 추가 발견): checkpoint State에 raw input
   channel이 없고 n0 전 input_id도 없어 actual graph input에서 n0로 원문을 전달할 transport
   contract가 없다. State body나 global scratch로 우회하지 않는다.

이 기록은 승인 amendment를 취소하지 않는다. 네 계약이 별도 amendment/card에서 닫힌 뒤
아래 G4 설계를 그대로 재개한다. 현재 상태는 `S0 G4 BLOCKED`이며 S0 invariant를 PASS로
승격하거나 close-out 문서를 작성하지 않는다.

## 1. 현재 상태

- 기준 브랜치/HEAD: `main` / `5b0d5da`
- worktree: 사용자 로컬 `.claude/settings.local.json`만 untracked, 그 외 clean
- P0-1~P0-7 완료, P0 REQUIRED I2/I4/I9/I10/I11 5/5 GREEN
- fresh: 전체 pytest `295 passed`, Ruff PASS, `--phase p0` exit 0, `--strict` exit 1
- S0 상태: G2 완료, G3 승인 대기, G4 미착수
- 이 문서 외 graph/node/checkpointer/validator/test/CI 코드는 생성·변경하지 않으며 commit하지 않는다.

## 2. S0 비개발자 설명

P0가 부품별 안전검사였다면 S0는 실제 조립 시험이다. 외부 금융 서비스와 운영 DB 대신 Mock과 Memory Store를 쓰지만, 실행 엔진은 실제 LangGraph이고 입력부터 최종 report 조회까지 production topology를 그대로 지난다. 이때 P0에서 대상이 없어 보류했던 checkpoint 크기, 실제 View 예산, loop 종료, citation 포함 관계, node AST를 실제 경로로 증명한다.

## 3. n0~n12 Fact Map

| Node | 정확한 역할 | Input | View | LLM | Output/Delta | Store access | 다음 edge |
|---|---|---|---|:---:|---|---|---|
| n0 | 실행 초기화·PII 마스킹 | thread_id, raw text | — | 아니오 | run_id, as_of, snapshot_version, input_id, started_at | Review `put_input` | n1 |
| n1 | 입력 가드 | input_id hydrate | GuardScanView | SMALL | NodeResult, total_llm_calls+1, block reason | Review `get_input` | OK→n2, blocked→n12 |
| n2 | 종목 해소 규칙 | input_id hydrate | — | 아니오 | stock | Review `get_input` | 확정→n3, 모호→n4, unresolved→n12 |
| n3 | 슬롯·Claim 추출 | input_id, stock | SlotContext | SMALL | slots, claim_ids, conflicts, C, llm+1 | Review `get_input`, `put_claims` | 충분→n5, 결손/충돌→n4 |
| n3b | 되묻기 응답 병합 규칙 | user_action, slots, claims | — | 아니오 | 갱신 slots/claim_ids, C 증가 | Review `get_claims`, `put_claims` | n5 |
| n4 | 되묻기 interrupt | slots, conflicts | AskBackContext | SMALL | user_action, hitl_reask+1, llm+1 | 없음 | resume→n3b, timeout/소진→n5 |
| n5 | 쿼리 설계 규칙 | claim_ids, stock | — | 아니오 | query_ids | Review `get_claims`; Evidence `put_queries` | n6 |
| n6 | Gateway 수집 | query_ids, run/as_of | — | 아니오 | collections, external_calls | Evidence `get_queries`; MockAdapter→`assemble_evidence`→put/link | n7 |
| n7 | Claim별 stance | claims, linked Evidence | EvidencePacket | SMALL×C | ClaimEvidence, llm calls | Review `get_claims`/`put_claim_evidence`; Evidence ID/link/get | n8 |
| n8 | Claim별 검증 | claims, ClaimEvidence, Evidence | VerifyPacket | LARGE×C | claim_evaluation_ids, llm calls | Review get claim/evidence + put evaluations; Evidence get | n9 |
| n9 | typed reduction | evaluations, slots, conflicts, collections | IntegrationView | LARGE | finding_ids, oppose, llm+1, 필요 시 recollect+1 | Review `get_claim_evaluations`, `put_findings` | 부족+여유→n5, 아니면 n10 |
| n10 | 출력 가드·재작성 | findings, slots | GuardBatchEnvelope (`GuardInput[]`) | LARGE≤2 | llm calls; Violation은 State 밖 | Review `get_findings` | 통과→n11, 재작성 여유→n10, 잔존→n12 |
| n11 | 렌더 | findings, slots, oppose, stock, citations | RenderView | MID | report_id, llm+1 | Review `get_findings`/`put_report`; Evidence `get_many` | n12 |
| n12 | 정상 종료·차단 처리 | 전체 State | — | 아니오 | snapshot_version+1, terminal NodeResult | S0에서는 terminal observation만; 운영 Alert/StateChange persistence는 후속 | END |

`n0~n12`는 번호가 붙은 13개 노드다. `n3b`는 n4 resume 결과를 병합하는 별도 rule graph node이므로 실행 vertex는 총 14개다. 파일 수를 늘리지 않기 위해 `n3.py`가 `n3`와 `n3b` factory를 함께 소유한다.

## 4. 정확한 Graph topology

```text
START → n0 → n1
n1 blocked → n12 → END
n1 OK → n2
n2 STOCK_UNRESOLVED → n12
n2 ambiguous → n4
n2 resolved → n3
n3 missing/conflict and hitl_reask<2 → n4
n3 sufficient → n5
n4 interrupt → resume → n3b → n5
n4 TIMEOUT_HITL or reask exhausted → n5
n5 → n6 → n7 → n8 → n9
n9 EVIDENCE_INSUFFICIENT and graph_recollect<1 → n5
n9 sufficient or recollect exhausted → n10
n10 violation and rewrite<2 → n10
n10 forbidden expression after 2 rewrites → n12
n10 pass → n11 → n12 → END
any node fatal budget/context/contract/machine timeout → n12
```

Assembler의 retryable failure는 graph edge가 아니라 해당 n7/n8/n9 node 내부의 bounded invoke→assemble 재시도다. 두 번째 실패는 `CONTRACT_VIOLATION` terminal delta로 바뀌어 n12로 간다. partial canonical object는 저장하지 않는다.

정본 topology를 그대로 복원하면 G4 전에 닫아야 할 두 공백이 드러난다.

1. `n2 ambiguous→n4`인데 `AskBackContext`는 missing slot만 담고, n3b는 slot/claim만 병합한 뒤 n5로 간다. 선택된 stock을 담거나 n2/n3로 복귀하는 계약이 없다. S0 Happy는 resolved stock만 사용하되, ambiguity edge를 실행 가능한 것으로 주장하지 않는다.
2. n10은 `finding_ids + 슬롯 텍스트`를 입력으로 요구하지만 State의 slot에는 status만 있고 Finding에도 text가 없다. 더구나 `GuardVerdictDraft`는 violation만 반환해 “재작성된 text”를 운반할 계약이 없다. n10 self-loop를 실제 rewrite loop라고 부르려면 guard 대상 text의 생성/보관과 rewrite output schema가 필요하다.

따라서 §4의 edge 등록 자체는 정본이지만, 위 두 경로는 사용자 승인 없이 임의 구현하지 않는다. G4 착수 조건은 아래 최소 amendment 중 하나를 선택하는 것이다.

## 5. Full / Partial / Chain 접근안 비교

| 접근 | 정본 topology | I1/I6/I8 | 구현량 | drift | 판정 |
|---|---:|---:|---:|---:|---|
| A Full thin graph | 전체 | 모두 활성화 가능 | 중간 | 낮음 | 추천 |
| B Partial graph | 일부 | I6·I8 불완전 | 낮음 | 높음 | 기각 |
| C Python chain | 불일치 | checkpoint/router 검증 불가 | 최저 | 매우 높음 | 기각 |

## 6. 추천안

Approach A를 조건부 채택한다. production path에 13개 numbered module을 두고 `n3b`는 `n3.py`에 함께 둔다. 각 node는 hydration, 기존 View 생성, 기존 Gateway/Assembler 호출, Store write, reducer-compatible delta만 담당한다. 단, n2 ambiguity와 n10 rewrite 계약 공백이 승인된 최소 amendment로 닫히기 전에는 Full graph G4를 시작하지 않는다.

## 7. RuntimeDeps 설계

```python
@dataclass(frozen=True)
class RuntimeDeps:
    review_store: ReviewStore
    evidence_store: EvidenceStore
    model_gateway: ModelGateway
    adapters: Mapping[str, ProviderAdapter]
    clock: Callable[[], datetime]
    id_factory: Callable[[str], str]
```

D2 node factory closure를 추천한다: `build_graph(deps, checkpointer)`가 factory로 만든 node를 등록한다. LangGraph runtime config는 `thread_id`·resume 같은 실행별 값만 운반한다. 전역 singleton은 병렬 테스트와 replay를 오염시키므로 금지한다. `clock`은 n0·Gateway fetched_at·canonical assembler created_at, `id_factory`는 run/claim/query/provider-call/evaluation/finding ID에만 사용한다. deterministic assembler가 이미 명시적으로 받는 시간/ID 경계를 유지한다.

## 8. State ↔ Store hydration 흐름

```text
ReviewState reference ID
→ 해당 Protocol get method
→ canonical object hydrate
→ 허용된 semantic View projection
→ Model/Adapter/Assembler
→ canonical object를 Store에 write
→ ID와 작은 routing value만 State delta로 반환
```

Evidence·Claim·Evaluation·Finding·Report 본문은 State에 넣지 않는다. n6의 Evidence는 link table로 유도하고 n7/n8/n11이 필요할 때 조회한다. 이 경계가 I10의 runtime 사용이며 동시에 I1 5KB의 원인 계약이다.

## 9. Scenario H Happy

- 고정 aware clock과 prefix별 deterministic ID factory를 주입한다.
- 입력 guard 통과, stock 단일 확정, slots 충분으로 HITL을 건너뛴다.
- n5가 최소 Query를 만들고 n6가 실제 MockAdapter→`assemble_evidence`→MemoryEvidenceStore를 실행한다.
- n7/n8/n9는 valid Draft를 기존 assembler로 canonicalize하고 MemoryReviewStore에 쓴다.
- n11은 citation validation 후 RenderView→RenderDraft를 호출하고 `RenderDraft.model_dump(mode="json")` body를 `put_report`한다.
- n12와 END에 도달하고 report_id로 report를 다시 읽는다.

## 10. Scenario R Retry

S0 test 전용 `ScriptedModelGateway`가 output-schema별 queue를 소비한다. n7 첫 응답은 schema-valid지만 coverage mismatch인 ClaimStanceDraft, 두 번째는 valid Draft다. 검증은 invoke 2회, 첫 실패 시 Store write 0, 두 번째 뒤 canonical write 1, `total_llm_calls` 정확히 +2, 최종 END다. production MockModelGateway는 수정하지 않는다. retry는 `AssemblyError.retryable=True`만 1회 허용하고 두 번째 실패 또는 non-retryable은 n12 계약 위반으로 보낸다.

## 11. Loop-limit scenario

하나의 거대 E2E 대신 실제 compiled graph/router를 공유하는 작은 integration test로 나눈다.

- HITL: 1회 전에는 n4, 2회 소진 시 n5
- recollect: 0회에는 n5, 1회 소진 시 n10
- rewrite: 첫 violation은 n10, 2회 후 잔존 시 n12
- external/LLM budget: limit 직전 허용, 다음 increment는 BUDGET_EXCEEDED→n12
- fatal reason 네 종류는 발생 node와 무관하게 n12
- C=8 worst path는 41 LLM calls를 넘기 전에 END/n12

## 12. HITL 처리

H1+H3 혼합을 추천한다. Happy는 HITL이 없는 경로로 빠르게 관통한다. 별도 integration test 하나는 LangGraph `interrupt()`와 `Command(resume=...)`를 실제 InMemorySaver와 사용해 **slot 결손 경로의** n4→중단→n3b→n5를 증명한다. timeout·2회 소진 분기는 pure router integration test로 검증한다. n2 stock ambiguity는 위 계약 공백이 닫히기 전까지 실행 성공으로 주장하지 않는다. UI나 장시간 대기는 만들지 않고 scripted resume payload만 쓴다.

## 13. I1 Checkpointer / blob 측정

설치된 LangGraph는 `1.2.11`; `MemorySaver`는 `langgraph.checkpoint.memory.InMemorySaver` alias이며 기본 serde는 `JsonPlusSerializer`다. saver는 checkpoint header/metadata와 versioned channel blob을 물리적으로 분리하므로 저장 dict의 임의 bytes 총합은 ReviewState 5KB 계약과 다르다.

S0는 `InMemorySaver`를 감싼 recording subclass를 사용한다. 실제 `put()`에 전달된 checkpoint의 `channel_values`를 동일 `saver.serde.dumps_typed()`로 직렬화하고 payload bytes 길이를 기록한 뒤 `super().put()`한다. run 전체의 `max(recorded_state_blob_bytes) < 5120`를 I1로 판정한다. 실제 graph runtime과 실제 saver serializer를 거치지만 metadata·parent pointer는 State 예산에서 제외한다. I11의 synthetic JSON 측정과 별개다.

## 14. I3 runtime View budget

대상은 n1/n3/n4/n7/n8/n9/n10/n11의 모든 호출이며 retry도 포함한다. S0-specific observing gateway가 실제 MockModelGateway를 감싸 `(prompt_version, slot, type(input_view), usage.ctx_chars)`를 기록한다. 값은 P0-5 MockModelGateway가 기존 `ctx_chars(input_view)`로 만든 Usage를 source of truth로 사용한다. test는 prompt_version→node mapping과 `NODE_BUDGETS[node]`를 대조한다. node에서 별도 문자 계산을 복제하지 않는다. 초과 입력의 축소는 P0-3 `truncate()`와 9+3 정책만 사용하며 새로운 truncation 정책은 만들지 않는다.

## 15. I6 6개 termination rule

| Rule | Limit | Owner | Increment | 종료/진행 edge | State field |
|---|---:|---|---|---|---|
| HITL reask | 2 | n4/node orchestration | 실제 LLM invoke 직전 성공적으로 예약 | 소진/timeout→n5 | `counters.hitl_reask` |
| graph recollect | 1 | n9 | n5 복귀 delta와 함께 | 소진→n10 | `counters.graph_recollect` |
| n10 rewrite | 2 | n10 | 각 guard invoke | 잔존→n12 | `counters`의 명시적 `guard_rewrite` 키 |
| external calls | 25 | n6/orchestrator | adapter call별 | 초과→n12 | `counters.total_external_calls` |
| total LLM calls | `4C+9`, C≤8 | 각 LLM node 공통 invoke wrapper | invoke 직전 | 초과→n12 | `counters.total_llm_calls`; C=`verifiable_claims` |
| fatal machine/budget family | 즉시 | 공통 node boundary/router | reason 발생 시 | n12 직행 | terminal NodeResult reason |

C는 전체 claim_id 길이가 아니라 n3/n3b가 확정한 `counters.verifiable_claims`다. generic LangGraph recursion limit은 마지막 안전망일 뿐 PASS 근거가 아니다. `guard_rewrite`는 현재 State schema가 dict counter를 허용하므로 새 top-level channel 없이 둔다. 정확한 key 명칭은 G4 test에서 이 이름으로 고정한다.

## 16. I7 enforcement gap 해결안

C2의 위치와 C3의 형태를 결합한다. 별도 pure helper `validate_citation_containment(citations, evidence_by_id)`를 만들고 n11이 RenderView를 만들기 직전에 호출한다. n11은 Finding/ClaimEvaluation citation ID를 모아 EvidenceStore.get_many로 hydrate하고, dangling evidence ID와 `citation.span not in evidence.raw_span`을 모두 거부한다. n8/n9 assembler signature와 순수성을 확대하지 않는다. I7 CI는 이 helper 단위 테스트뿐 아니라 n11 invalid-citation integration path가 publish를 막는 test를 실행해야 PASS다.

## 17. I7 failure policy

invalid/dangling citation은 deterministic `CONTRACT_VIOLATION`으로 변환하고 n12로 직행하며 report를 저장·publish하지 않는다. LLM retry, Finding drop, PARTIAL report로 자동 전환하지 않는다. 이는 DDR의 `CONTRACT_VIOLATION → n12`와 일치한다. containment는 Python의 exact substring(`citation.span in evidence.raw_span`)만 사용한다. whitespace/Unicode normalization 계약이 정본에 없으므로 추가하지 않는다. 이 두 결정은 사용자 승인 대상이다.

## 18. I8 AST activation

S0 production node `.py` 파일이 생기면 기존 I8 scanner가 두 roots를 실제 스캔한다. root 존재만으로는 부족하며 모든 source parse 성공과 canonical 4종 `output_schema=` 위반 0건이어야 PASS다. 모든 LLM node는 Guard/Slot/AskBack/ClaimStance/ClaimEvaluationDraft/FindingDraft/GuardVerdictDraft/RenderDraft만 사용한다. S0 G4에서 I8 spec의 check는 그대로 scanner를 쓰되 artifact가 생겨 PASS로 전환된다.

## 19. MockAdapter/Evidence Gateway 연결

n6가 정본 owner다. `query_ids → get_queries → adapter.build_request/acall/parse_response → ProviderCall → assemble_evidence(..., store=deps.evidence_store)` 순서로 실행한다. source validation, hash, batch/run dedup, canonical injection, query link는 기존 P0-4 함수를 그대로 사용한다. S0에서는 MemoryEvidenceStore와 MockAdapter만 주입하고 ReplayCache·network를 추가하지 않는다.

## 20. MockModelGateway scriptability

M2를 추천한다. Happy는 production MockModelGateway를 그대로 쓰고, retry/실패 scenario는 `tests/s0/fakes.py`의 Protocol-compatible ScriptedModelGateway를 쓴다. queue key는 `(prompt_version, output_schema)`로 하여 같은 schema를 쓰는 node와 call order를 구분한다. monkeypatch는 쓰지 않으며 P0-5 strict-closed MockModelGateway도 변경하지 않는다.

## 21. Final Report 성공 조건

Happy test의 observable은 다음 전부다.

- compiled graph가 n12를 거쳐 END 도달
- final State에 non-null report_id
- MemoryReviewStore.get_report(report_id)가 RenderDraft 기반 body 반환
- Finding 최소 1개, ClaimEvaluation 최소 1개, Evidence 최소 1개가 각 Store에서 조회됨
- report의 모든 citation이 실제 Evidence raw_span에 exact 포함
- terminal state에는 본문 객체가 없고 checkpoint 최대 payload가 5120B 미만

frozen에는 Report canonical model이 없고 ReviewStore contract가 dict body를 받으므로 S0에서 새 Report schema를 만들지 않는다. UI/FastAPI/browser도 범위 밖이다.

## 22. S0 test matrix

| 파일 | 직접 증명 |
|---|---|
| `tests/s0/test_vertical_slice.py` | Happy graph→END→report/store observables |
| `tests/s0/test_retry.py` | retryable assembler 1회 retry, 두 번째 실패 block/no write |
| `tests/s0/test_loop_limits.py` | I6 여섯 rule의 boundary와 worst C=8 |
| `tests/s0/test_runtime_invariants.py` | I1 max runtime blob, I3 모든 invoke budget, I7 n11 enforcement, I8 scanner |
| `tests/s0/test_hitl.py` | 실제 interrupt/checkpoint/resume와 n3b |

fixture와 scripted double은 `tests/s0/fakes.py`, 공통 deterministic scenario builder는 `tests/s0/conftest.py`에 한정한다.

## 23. `ci.invariants --phase s0` 활성화 계획

- I1: actual compiled graph + recording InMemorySaver test target PASS
- I3: observing gateway의 모든 runtime invocation budget test PASS
- I6: compiled routing/loop boundary suite PASS
- I7: pure validator와 n11 publish-block integration test PASS
- I8: production node roots AST scan PASS

`required_from=s0`는 이미 있으므로 각 check를 exact pytest node/file thin wrapper로 교체한다. `--phase s0`는 I1~I4, I6~I11이 PASS이고 I5 PARTIAL(T2 future)이면 exit 0이다. `--strict`는 I5 때문에 계속 exit 1이 정상이다.

## 24. Cross-card follow-up 중 S0 blocker

| Follow-up | S0 판정 | 최소 결정 |
|---|---|---|
| NODE_ORCHESTRATION_FOLLOW_UP | blocker | retryable assembly 1회; 두 번째 실패 CONTRACT_VIOLATION; partial write 금지; n12 |
| I7 CONTRACT_GAP | blocker | n11 pre-render pure validator, exact containment, invalid 시 publish block |
| n2 ambiguity HITL contract | blocker | stock selection payload와 resume destination이 정본에 없음 |
| n10 guarded-text/rewrite contract | blocker | guard input text source와 rewritten text output schema가 없음 |
| VERDICT_CITATION_BINDING | 비-blocker | 현재 allowlist+I7만 유지, stronger binding은 후속 |
| VERDICT_NUMERIC_RECONCILIATION | 비-blocker | 기존 assembler/frozen만 사용 |
| T2-D fetched_at provenance | 비-blocker | fixed clock 주입, ReplayCache는 T2-D |
| HASH_SERIALIZATION_AMBIGUITY | 비-blocker | 기존 assemble hash 그대로 사용 |
| TIMEOUT_NORMALIZATION_BOUNDARY | 비-blocker | S0 Mock은 network timeout 없음; machine/HITL reason route만 시험 |
| OWNERSHIP_FOLLOW_UP | 비-blocker | S0 factory DI 내부 ownership만 고정, 운영 팀 ownership은 변경 안 함 |

## 25. G4 예상 파일 구조

```text
app/orchestration/runtime.py             RuntimeDeps, deterministic invoke boundary
app/orchestration/graph.py               topology, routers, compile
app/orchestration/nodes/n0.py ... n12.py 13 numbered modules; n3b는 n3.py
app/orchestration/validators/citations.py
tests/s0/conftest.py
tests/s0/fakes.py
tests/s0/test_vertical_slice.py
tests/s0/test_retry.py
tests/s0/test_loop_limits.py
tests/s0/test_runtime_invariants.py
tests/s0/test_hitl.py
ci/invariants.py                         I1/I3/I6/I7/I8 activation only
docs/design/S0-mock-vertical-slice.md
docs/00-status.md
docs/TASK_CARDS_v2_2.md
```

공통 helper가 충분하면 node module 수는 합치지 않는다. 반대로 빈 wrapper만 생기면 동일 책임 node를 한 파일에 모으되 graph node 이름은 정본 그대로 유지한다. 승인 후 G4에서 테스트가 요구하는 최소 구조로 최종 확정한다.

## 26. G4 TDD 순서

1. citation pure helper와 n11 block test RED→최소 I7 구현→GREEN
2. RuntimeDeps/fixed clock-ID 및 thin node delta 단위 test RED→GREEN
3. exact topology/router·I6 boundary test RED→graph/routers GREEN
4. Happy vertical slice RED→Memory dependencies와 기존 gateway/assemblers 연결→GREEN
5. actual interrupt/resume RED→InMemorySaver compile/config→GREEN
6. recording saver I1 RED→actual max payload 측정→GREEN
7. observing gateway I3 RED→8 node/retry capture→GREEN
8. production node artifact에 대한 I8 RED/PASS 확인
9. retry scenario RED→test-only ScriptedModelGateway와 bounded node retry→GREEN
10. CI I1/I3/I6/I7/I8 thin wrapper 전환 후 `--phase s0` GREEN, strict expected RED
11. mutation, full pytest, Ruff, diff, frozen/DDR 무변경 확인 후 문서 동기화

## 27. 사용자 승인 필요 항목

1. Approach A Full thin graph와 n3b 포함 14 executable vertex
2. §4의 exact topology 및 assembler retry를 node-internal bounded path로 처리
3. D2 node factory closure RuntimeDeps, runtime config는 thread/resume만 사용
4. fixed aware clock과 prefix-aware deterministic ID factory 범위
5. Happy + Retry + 분리된 loop-limit scenario set
6. HITL은 실제 interrupt/resume 1건 + router boundary test 조합
7. LangGraph 1.2.11 InMemorySaver recording subclass와 `channel_values` serde payload 최대값을 I1 측정점으로 확정
8. MockModelGateway를 감싼 observing gateway의 Usage.ctx_chars를 I3 source of truth로 사용
9. §15의 I6 여섯 rule과 `guard_rewrite` counter key
10. I7 owner를 n11 pre-render pure citation validator로 확정
11. invalid citation은 CONTRACT_VIOLATION→n12, report 미저장
12. I7은 normalization 없는 exact substring containment
13. retry에는 production Mock 변경 없이 test-only ScriptedModelGateway 사용
14. final success observable을 END/report/store objects/I1/I7 전부로 확정
15. S0 CI는 exact S0 pytest targets로 I1/I3/I6/I7을 재사용하고 I8 native scanner를 활성화
16. retryable assembly는 1회만 재시도, 두 번째 실패는 no partial write+CONTRACT_VIOLATION으로 NODE_ORCHESTRATION_FOLLOW_UP 최소 폐쇄
17. n2 ambiguity 최소 amendment: 권장안은 stock 전용 deterministic selection payload를 n4 resume 값에 넣고 n2가 확정한 뒤 n3로 진행; n3b slot merger와 혼합하지 않음
18. n10 최소 amendment: 권장안은 n11의 RenderDraft 생성과 report publish를 분리해 draft text를 n10이 guard/rewrite한 뒤 publish하게 만드는 것. 현재 n10→n11 순서를 유지해야 한다면 별도 pre-render text contract/output schema를 먼저 승인

**S0 G3 설계안 작성 완료. 사용자 승인 전에는 S0 G4 구현을 시작하지 않습니다.**
