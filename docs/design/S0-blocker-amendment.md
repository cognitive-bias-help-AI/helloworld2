# S0 Blocker Amendment G2/G3

> **CLOSED 2026-08-14.** A1~A3와 P0-3A는 구현됐고, 최종 Amendment E는
> `ReviewRequestContext(raw_text)` + LangGraph `context_schema`로 닫혔다. raw_text는
> n0만 읽으며 n0가 마스킹 후 `put_input`을 소유한다. thread_id는
> `RunnableConfig.configurable`로 분리되고 ReviewState 19 channels는 변경하지 않았다.

## 1. 현재 상태

- 기준 HEAD/branch: `5b0d5da` / `main`
- S0 G4는 네 Hard Blocker로 중단 상태이며 production graph와 `tests/s0`는 없다.
- fresh baseline: pytest `295 passed`, Ruff PASS, P0 invariant 5/5 PASS(exit 0).
- 기존 untracked `.claude/settings.local.json`과 `docs/design/S0-mock-vertical-slice.md`를 보존했다.
- 이 문서는 계약 설계만 확정한다. production/test/CI/frozen/DDR 수정과 commit은 하지 않는다.

## 2. 네 Hard Blocker 재검증

| Blocker | 재검증 사실 | 원인 |
|---|---|---|
| STOCK_RESOLUTION_SOURCE | frozen `StockCandidate`는 있으나 StockResolver/StockMaster/fixture source 없음 | n2가 후보를 재계산할 port 부재 |
| RENDER_REWRITE_FEEDBACK_CONTRACT | RenderView exact fields 4개, RenderDraft slots, GuardVerdictDraft violations뿐 | 다음 GENERATE에 feedback 전달 불가 |
| REPORT_ASSEMBLY_CONTRACT | frozen Report 없음, ReviewStore는 body dict | 저장 body shape와 system ownership 미고정 |
| SECOND_COVERAGE_EDGE | 재시도 1회와 PARTIAL/COVERAGE_TRUNCATED까지만 고정 | fallback canonical/edge가 미고정 |

## 3. A1 Stock Resolution 접근안 A/B/C

| 접근 | 장점 | 문제 | 판정 |
|---|---|---|---|
| A local dict in n2 | 가장 작음 | fixture와 business node 결합, 교체 경계 없음 | 기각 |
| B StockResolver port + fixture | deterministic, 실제 resolver 교체 가능 | 작은 port 필요 | 추천 |
| C input에 candidates 포함 | 구현 단순 | 사용자 입력을 system resolution source로 신뢰 | 기각 |

## 4. 추천 StockResolver contract

기존 frozen `StockCandidate`를 그대로 사용하며 새 candidate domain type은 만들지 않는다. candidate ID는 KRX code 자체다.

```python
class StockResolver(Protocol):
    def resolve(self, text: str, limit: int = 5) -> list[StockCandidate]: ...
```

- port: `app/domain/protocols.py`
- S0 deterministic implementation: `tests/s0/fakes.py::FixtureStockResolver`
- 향후 real implementation: `app/domain/stock_master.py::StockMaster`, 같은 port 준수
- 반환 정렬과 동점 규칙은 T1-B 정본대로 score 내림차순/code 오름차순이다.
- n2는 ReviewStore에서 masked input을 hydrate해 resolver에 전달하고 State에는 선택된 stock projection만 쓴다.

## 5. n2 0/1/N flow

```text
candidates = resolver.resolve(masked_input, limit=5)
0 → STOCK_UNRESOLVED → n12
1 → 해당 StockCandidate를 State stock으로 projection → n3
N → interrupt(StockChoiceRequest) → resume 후 n2 재실행
```

`StockChoiceRequest`는 JSON-serializable local orchestration model이며 fields는
`query: str`과 `candidates: list[StockChoiceOption]`이다. Option exact fields는
`candidate_id: str`(code), `display_name: str`(name), `market: Literal["KOSPI","KOSDAQ"]`다.
`StockChoiceResume` exact field는 `selected_candidate_id: str` 하나다. 둘은
`app/orchestration/hitl.py`에 extra-forbid/frozen Pydantic model로 둔다. frozen.py는 바꾸지 않는다.

## 6. Stock HITL resume membership

resume 후 n2는 State의 후보 body를 읽지 않는다. 같은 input_id를 hydrate하고 동일 resolver와 limit로 후보를 재계산한다. `selected_candidate_id`를 code-keyed 결과 map에서 찾는다.

- 존재: recomputed canonical StockCandidate를 State stock으로 projection
- 부재: `CONTRACT_VIOLATION → n12`
- duplicate code: resolver contract violation으로 동일하게 n12
- resume payload의 name/market/score 입력은 받지 않으므로 임의 stock injection이 불가능하다.

## 7. A2 rewrite feedback 접근안

| 접근 | 문제 | 판정 |
|---|---|---|
| prompt_version encoding | prompt identity와 runtime data 혼합 | 기각 |
| scratch를 ModelGateway가 직접 읽기 | semantic View 우회, hidden dependency | 기각 |
| RenderView formal amendment | typed semantic input, budget/I4로 회귀 보호 | 추천 |

exact amendment는 기존 RenderView에 다음 한 field를 추가하는 것이다.

```python
guard_feedback: list[Violation] = Field(default_factory=list)
```

Violation은 frozen existing type이다. 첫 GENERATE에는 빈 list, rewrite GENERATE에는 직전 n10 결과만 deterministic sort해 넣는다. 모든 과거 violation을 누적하지 않아 budget과 prompt drift를 막는다.

## 8. P0-3 RenderView amendment 필요 여부

`P0-3A RenderView Guard Feedback Amendment`가 필요하다. 이는 S0 내부 편의 변경이 아니다.

- `app/contexts/views.py` RenderView formal field 추가
- `tests/contexts/test_views.py` exact allowlist에 `guard_feedback` 추가
- construction/extra/frozen 회귀 유지
- `tests/contexts/test_budget.py`에서 빈 feedback과 최대 8 Violation을 포함한 3500자 상한 검증
- P0-7 I4는 기존 exact pytest target을 그대로 재사용하므로 target 변경 없이 amended contract를 보호
- P0-3/P0-7 설계 문서와 Task Card에 amendment 이유 기록

I4를 약화하거나 generic subset 검사로 바꾸지 않는다.

## 9. 수정된 Render/Guard lifecycle

```text
n9
→ n11 GENERATE(RenderView.guard_feedback=[] 또는 직전 violations)
→ RenderCandidateStore.put(run_id, RenderDraft, generation)
→ I7 exact citation validation
→ n10 GUARD(candidate slots를 GuardInput으로 projection)
PASS → candidate approved → n11 PUBLISH → n12
FAIL and rewrite_count<2 → feedback 저장 → n11 GENERATE
FAIL at rewrite_count=2 → n12, report 없음
```

RenderCandidateStore exact state는 `candidate: RenderDraft`, `guard_feedback: tuple[Violation,...]`, `rewrite_count: int`, `approved: bool`이다. run_id가 key이며 ReviewState에 candidate body/reference를 추가하지 않는다.

## 10. Report schema 부재 분석

현재 ReviewStore의 `put_report(run_id, body: dict) -> str`는 report ID를 Store가 만들고 run linkage도 method argument로 받는다. 따라서 Evidence처럼 frozen domain canonical model을 새로 만들 이유가 없다. 필요한 것은 arbitrary dict를 없애는 application output artifact와 deterministic serialization boundary다.

## 11. Report 접근안 R-A/B/C

| 접근 | 영향 | 판정 |
|---|---|---|
| R-A frozen Report 추가 | freeze/DDL/3인 승인 필요 | 기각 |
| R-B local typed ReportArtifact | shape 고정, frozen 무변경 | 추천 |
| R-C arbitrary dict | shape drift, system ownership 불명확 | 기각 |

## 12. 추천 ReportArtifact exact fields

`app/orchestration/reporting.py`에 extra-forbid/frozen Pydantic model을 둔다.

```python
class ReportArtifact(BaseModel):
    schema_version: Literal["s0.v1"] = "s0.v1"
    rendered_slots: list[RenderedSlotDraft]
    banners: list[NonBlankStr]
    theory_notes: list[TheoryNote]
    citations: list[RenderCitationView]
    created_at: AwareDatetime
```

- LLM 소유: RenderDraft의 `rendered_slots` semantic content만
- deterministic n11 PUBLISH 소유: schema_version, banners, theory_notes, citations, created_at
- report_id는 `put_report` 반환값이며 artifact body에 중복하지 않는다.
- run_id는 Store method와 index가 소유하므로 body에 중복하지 않는다.
- Evidence/Finding/ClaimEvaluation 본문은 embed하지 않는다.
- publish는 `artifact.model_dump(mode="json")`만 Store에 전달한다.

## 13. ReviewStore 영향

Protocol signature는 바꾸지 않는다. `body: dict`는 persistence boundary를 유지하고 application layer가 ReportArtifact serialization을 강제한다. MemoryReviewStore와 미래 PostgreSQL Store도 변경 불필요하다. 대신 S0 test는 arbitrary dict를 직접 publish하는 production path가 없고 round-trip body가 ReportArtifact로 재검증되는지 확인한다. 장래 Store 자체를 typed로 바꾸는 것은 별도 protocol amendment다.

## 14. A3 second coverage 접근안

정본 재검색 결과 COV-C fail-closed는 선택할 수 없다. Task Card는 `COVERAGE_TRUNCATED`를 품질저하로 분류해 “리포트는 나가되 배너”라고 명시하고, n7/n8 diagram도 truncation 후 canonical write/다음 node 진행을 가리킨다.

추천하는 최소 정책은 deterministic fallback canonicalization이다.

- 공통: 두 번째 coverage mismatch Draft는 저장하지 않는다. NodeResult는 `PARTIAL/COVERAGE_TRUNCATED/retry_count=1`, banner source는 runtime scratch의 reason set으로 기록하고 다음 semantic node로 진행한다.
- n7: valid Draft에 존재하는 stance는 보존하고 누락 packet evidence는 `ClaimEvidence(stance="unknown", stance_source="rule", confidence=None)`로 채운다. LLM이 봤다고 위장하지 않는다.
- n8: incomplete Draft의 verdict/citations를 canonicalize하지 않는다. packet 전체를 `unknown_evidence_ids`에 넣고 `verdict="unverifiable"`, citations/support/oppose/neutral은 empty, numeric checks는 rule 결과를 보존하며 `uncertainty_codes`에 `COVERAGE_TRUNCATED`를 넣는 deterministic fallback ClaimEvaluation을 만든다.
- n9 assembler coverage 성격은 evaluation 단위와 다르므로 unknown citation retry가 두 번 실패하면 해당 invalid FindingDraft만 폐기하고 `COVERAGE_TRUNCATED` banner reason을 기록한다. valid FindingDraft와 missing Finding은 계속 canonicalize한다.

fallback helper는 node/orchestration 소유이며 기존 P0-5 assembler의 strict equality를 약화하거나 우회하지 않는다.

## 15. fail-closed 방식의 장단점

장점은 invalid canonical을 만들지 않고 policy가 작다는 것이다. 그러나 정본의 품질저하 경로, 배너와 report publish 요구에 정면 충돌하며 S0가 정상 degraded behavior를 차단 경로로 바꾼다. 따라서 programmer/caller contract violation에는 fail-closed를 유지하지만, 두 번째 LLM coverage mismatch에는 사용하지 않는다.

## 16. previous canonical refresh case 분리

S0는 first-run만 확정한다. refresh/re-analysis에서 이전 canonical이 있을 경우 보존, supersede, 무효화 중 무엇을 택할지는 `NODE_ORCHESTRATION_REFRESH_FOLLOW_UP`으로 남긴다. S0 fallback은 기존 canonical을 조회하거나 삭제하지 않으며 first-run test fixture로만 gate를 닫는다.

## 17. Frozen/authority 영향표

| Amendment | frozen.py | P0 design 변경 | Store Protocol | CI 변경 |
|---|---:|---:|---:|---:|
| A1 Stock resolver | 없음, StockCandidate 재사용 | Task Card/S0 amendment | 없음 | S0 runtime target 추가 |
| A2 Rewrite feedback | 없음, Violation 재사용 | **P0-3A 및 I4 exact allowlist amendment** | 없음 | I3/I4 backing test 유지·확장 |
| A2 Report artifact | 없음 | S0 reporting boundary 추가 | 없음, dict persistence 유지 | S0 publish target 추가 |
| A3 Coverage edge | 없음, NodeResult/ReasonCode 재사용 | P0-5 orchestration follow-up 일부 폐쇄 | 없음 | S0 retry/degraded targets 추가 |

## 18. 기존 테스트 영향표

| Existing/new target | 변경 원인 | 처리 |
|---|---|---|
| `tests/contexts/test_views.py` exact 8 View fields | RenderView feedback formal amendment | field 추가, exactness 유지 |
| `tests/contexts/test_budget.py` | I3 runtime/static budget | feedback 포함 3500자 경계 추가 |
| P0-7 I4 wrapper | 동일 exact target 사용 | node ID 변경 없음 |
| `tests/protocols/test_protocols.py` | Store signature | 변경 없음 |
| `tests/store/test_memory_review_store.py` | dict persistence | 변경 없음; S0에서 artifact round-trip 추가 |
| P0-5 assembler tests | strict coverage source of truth | 삭제/완화/변경 없음 |
| 신규 stock HITL tests | A1 | 0/1/N, recomputation, invalid membership |
| 신규 render lifecycle tests | A2 | feedback, no pre-guard report, approved publish |
| 신규 coverage tests | A3 | n7/n8 fallback, n9 invalid-only drop, banner/report continuation |

## 19. amendment별 승인 필요 항목

1. A1-B StockResolver Protocol과 frozen StockCandidate/code-as-ID 재사용
2. FixtureStockResolver를 tests/s0에 두고 production hardcoded candidate를 금지
3. n2 resume 시 input hydrate+resolver 재계산 membership 검증
4. `StockChoiceRequest(query,candidates)`와 `StockChoiceResume(selected_candidate_id)` exact local schema
5. P0-3A를 공식 재개해 RenderView에 `guard_feedback: list[Violation]=[]` 추가
6. feedback은 직전 violation만 deterministic sort하여 전달
7. local ReportArtifact의 §12 exact fields와 frozen Report 미추가
8. ReviewStore Protocol은 유지하고 PUBLISH가 typed artifact만 JSON dict로 직렬화
9. A3 fail-closed 기각 및 deterministic degraded continuation 채택
10. n7 missing evidence를 rule-owned unknown ClaimEvidence로 보충
11. n8 incomplete Draft를 버리고 system-owned unverifiable fallback evaluation 생성
12. n9는 두 번 invalid인 FindingDraft만 폐기하고 valid/missing findings는 계속 저장
13. first-run만 확정하고 refresh policy는 별도 follow-up 유지

## 20. 최종 blocker 상태

| Amendment | 설계 상태 | 사용자 승인 후 |
|---|---|---|
| A1 Stock resolution | RESOLVED | 구현 가능 |
| A2 rewrite feedback | RESOLVED, P0-3A 필요 | 구현 가능 |
| A2 report artifact | RESOLVED | 구현 가능 |
| A3 second coverage | RESOLVED | 구현 가능 |

네 계약은 상호 모순 없이 최소 implementation contract까지 내려왔다. 현재는 사용자 승인 전이므로 구현 gate가 닫혀 있다. §19 전체가 승인되면 `S0 G4 RESUMABLE`이다.

### G4 resume preflight에서 발견된 신규 blocker

승인 후 Phase 1~4를 구현한 다음 Full Graph input boundary에서
`INITIAL_INPUT_CONTRACT_BLOCKER`가 확인됐다. ReviewState 19채널에는 `raw_text` 또는 request
reference가 없고, n0 실행 전에는 `input_id`도 없다. 따라서 실제 `graph.invoke()`가 n0에
사용자 원문을 전달할 승인 경로가 없다. 임의 raw_text State channel은 I1/reference-only
계약을 깨고, module/global scratch는 RuntimeDeps/재현성 계약을 깨며, n0 밖에서 미리
put_input하면 “n0가 PII masking과 put_input을 소유”한다는 정본을 바꾼다.

재개에는 다음 중 하나의 formal amendment 승인이 필요하다.

1. 권장: graph input용 별도 `ReviewRequest` transport model을 정의하고 StateGraph의 input
   schema와 checkpointed ReviewState를 분리한다. n0만 raw_text를 소비해 masked body를
   Store에 쓰고 이후 State에는 input_id만 남긴다.
2. 대안: API/service boundary가 masking+put_input을 선행하고 n0는 initialization만 수행한다.
   이는 n0 ownership 정본 변경이므로 더 무겁다.

현재 코드는 P0-3A, A1 contract, A2 typed artifact/I7 primitive, A3 fallback까지만 완료됐다.
Full graph, runtime invariant activation, mutation, S0 close-out은 시작하지 않았다.

**S0 Blocker Amendment G3 완료. 네 계약이 모두 사용자 승인되기 전에는 S0 G4 구현을 재개하지 않습니다.**
