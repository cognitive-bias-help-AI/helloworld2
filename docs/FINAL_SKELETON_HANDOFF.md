# 금융프젝 2 Skeleton 최종 인수인계

## Executive Summary

이 저장소는 사용자의 투자 주장을 근거와 대조하고, 출처가 연결된 검토 보고서를 만드는 시스템의 구현 가능한 뼈대다. 실제 금융 API·OpenAI·PostgreSQL·FastAPI·Frontend는 아직 없지만, 교체 경계인 Protocol과 frozen contract, 실제 LangGraph 실행 순서, Memory/Mock 기준 end-to-end 경로, 안전 invariant가 완성돼 있다.

구조의 핵심은 본문과 제어 정보를 분리하는 것이다. State는 ID와 상태만 들고, canonical 본문은 Store가 소유한다. 최초 원문만 Runtime Context로 n0에 들어가 마스킹된 뒤 Store의 input_id로 바뀐다. LLM은 Draft 제안만 하며 ID·시간·hash·provenance·검증·fallback·최종 저장은 결정론적 코드가 소유한다.

## 전체 흐름

```text
사용자 원문 → ReviewRequestContext → n0 마스킹/put_input
→ ReviewState(reference/control) + ReviewStore/EvidenceStore(canonical body)
→ n1 → n2 → n3 → [n4 interrupt → n3b] → n5 → n6 → n7 → n8 → n9
→ n11 GENERATE → I7 citation → n10 GUARD → n11 PUBLISH → n12 → END
→ ReportArtifact
```

## Node table

| Node | 역할 | LLM/RULE | Input | Output | Store |
|---|---|---|---|---|---|
| n0 | 원문 검증·마스킹 | RULE | Runtime Context raw_text | input_id | Review put_input |
| n1 | 입력 guard | LLM SMALL | GuardScanView | block/ok | Review get_input |
| n2 | 종목 해소·선택 HITL | RULE | input_id | stock 또는 block | Review get_input |
| n3 | 슬롯/Claim 추출·정본화 | LLM SMALL | SlotContext | slots, claim_ids | Review get_input/put_claims |
| n4 | 결손 질문·interrupt | LLM SMALL | AskBackContext | user_action | checkpoint |
| n3b | resume 답변 병합 | RULE | user_action | USER_CONFIRMED Claim | Review put_claims |
| n5 | 검증 Query 설계 | RULE | claim_ids, stock | query_ids | Evidence put_queries |
| n6 | Mock provider 수집 | RULE | query_ids | collections | Evidence get/put/link |
| n7 | evidence stance | LLM SMALL | EvidencePacket | ClaimEvidence | 양 Store |
| n8 | Claim 검증 | LLM LARGE | VerifyPacket | ClaimEvaluation | 양 Store |
| n9 | finding 통합 | LLM LARGE | IntegrationView | finding_ids, oppose | Review get/put |
| n11 GENERATE | 보고서 초안 생성/재작성 | LLM MID | RenderView | RenderCandidate | candidate store |
| I7 | citation ID·span 검증 | RULE | RenderDraft, Evidence | pass/contract violation | Evidence get_many |
| n10 | 출력 guard | LLM LARGE | GuardBatchEnvelope | pass/rewrite/block | candidate store |
| n11 PUBLISH | final artifact 조립 | RULE | approved candidate | report_id | Review put_report |
| n12 | 차단·종료 | RULE | control State | END | 없음 |

## Contract Map과 authority

정본 순서는 `DDR → app/schemas/frozen.py → 승인 amendment 문서 → lifecycle/diagram → task card → implementation`이다. Frozen schema는 canonical 데이터 모양, ReviewState는 19개 reference/control channel, Views는 LLM 최소권한 입력, Draft는 LLM 제안, Assemblers는 canonical 승격, Store Protocol은 본문 소유권, ProviderAdapter/StockResolver는 외부 교체 port, RuntimeDeps는 run별 주입, ReportArtifact는 final 저장 shape, CI invariant는 이 관계의 실행 증거다.

소유권은 다음과 같다.

- `State = reference/control plane`: ID, compact status, counter만 보유한다.
- `Store = canonical body`: input, Claim, Evidence, Evaluation, Finding, Report를 보유한다.
- `Runtime Context = initial raw input transport`: raw_text는 n0만 읽고 State/checkpoint에 저장하지 않는다.

## LLM boundary

LLM은 8개 승인 node에서만 Draft semantic proposal을 반환한다. 결정론적 코드는 canonical ID, timestamp, hash, provenance, reference coverage, citation containment, fallback, Store write를 책임진다. `Evidence`, `ClaimEvidence`, `ClaimEvaluation`, `Finding`은 ModelGateway output_schema로 사용할 수 없으며 I8 AST가 이를 막는다.

## 팀별 교체 경계

### Provider

`ProviderAdapter`를 구현하고 `tests/adapters/cases.py`에 contract case와 fixture를 등록한다. DART·Kiwoom·Naver의 네트워크 구현은 이 port 밖의 graph/assembler 의미를 바꾸지 않는다.

### Model

`ModelGateway`를 구현한다. 8개 승인 output schema만 사용하고 canonical schema 출력은 금지하며, 기존 View와 `NODE_BUDGETS`를 지킨다. prompt_version별 model/effort 정본은 registry를 따른다.

### DB

`ReviewStore`, `EvidenceStore` Protocol 구현체를 교체한다. T2에서 PostgreSQL 물리 제약 `UNIQUE(run_id, content_sha256)`을 적용해 I5를 PASS로 승격한다.

### API / Frontend

아직 구현하지 않았다. API와 Frontend는 Graph/Store/Runtime 경계를 호출하는 consumer이며 business contract를 다시 정의하는 owner가 아니다.

권장 교체 순서는 `DB → Provider → Real ModelGateway → API → Frontend → integration/E2E`다.

## Invariant table

| I | 의미 | 현재 | 후속 책임 |
|---|---|---|---|
| I1 | actual checkpoint ≤ 5120B | PASS | Graph/DB |
| I2 | reducer 순서 독립 | PASS | Graph |
| I3 | 8개 LLM View budget | PASS | Model |
| I4 | View allowlist | PASS | Model/Graph |
| I5 | Evidence 물리 unique | PARTIAL | DB, T2 |
| I6 | loop/call termination | PASS | Graph |
| I7 | citation exact containment | PASS | Graph |
| I8 | canonical output 금지 | PASS | Model/CI |
| I9 | provider/source type | PASS | Provider |
| I10 | State→Store read path | PASS | Store/Graph |
| I11 | representative State size | PASS | Graph |

## Remaining follow-ups

- `I5 PostgreSQL physical UNIQUE`: Memory reference는 검증됐고 물리 DB가 생기는 T2에 활성화한다.
- `RENDER_CANDIDATE_DURABILITY_FOLLOW_UP`: S0는 run-local memory candidate이며 운영 DB 도입 시 지속화한다.
- `NODE_ORCHESTRATION_REFRESH_FOLLOW_UP`: 실제 provider/model 특성이 정해진 통합 단계에서 orchestration timing을 재검증한다.
- `VERDICT_CITATION_BINDING`, `VERDICT_NUMERIC_RECONCILIATION`: frozen 의미 변경이 필요한 후속 계약 검토로 남긴다.
- `T2-D fetched_at provenance`, `HASH_SERIALIZATION_AMBIGUITY`: 실제 DB/provider 직렬화 단계에서 닫는다.
- `TIMEOUT_NORMALIZATION_BOUNDARY`, `OWNERSHIP_FOLLOW_UP`: 실제 network/runtime owner가 생기는 단계에서 정한다.

## 현재 의도적 범위 밖

실 DART/Kiwoom/Naver API, 실 OpenAI, PostgreSQL, production FastAPI, Frontend, 인증, 배포, production network retry는 구현하지 않았다.

