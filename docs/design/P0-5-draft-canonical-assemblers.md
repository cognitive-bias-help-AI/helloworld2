# P0-5 · Draft → Canonical 조립기 G2/G3

> **상태: G2 완료 / G3 Amendment 완료 / G4 STRICT CLOSED (2026-08-13)**
> 권위: DDR v2.2 → `app/schemas/frozen.py` → P0-3/P0-4 승인 설계 → 다이어그램·생명주기·카드.
> 승인된 설계를 구현했으며 frozen/DDR, Node, Store, retry orchestration은 변경하지 않았다.

## G4 구현 결과

- 폐쇄·불변 output schema 6종과 nested item을 `app/orchestration/drafts.py`에 구현했다.
- `AssemblyError`와 pure n7/n8/n9 assembler를 `app/assemblers/`에 구현했다.
- 8종 Draft allowlist와 `budget.ctx_chars` Usage를 쓰는 `app/models/mock.py`를 구현했다.
- 계약 테스트 28건을 추가했고 전체 `216 passed`, Ruff 통과를 확인했다.
- 승인된 mutation을 각각 적용→RED→원복→GREEN으로 독립 실행해 24/24 탐지했다.
- `app/schemas/frozen.py`와 `docs/DDR_v2_2_FINAL_FROZEN.md` diff는 0건이다.

## 1. 한 문장 정의

LLM이 만든 제한된 의미 초안을 packet의 allowlist와 규칙 계산 결과에 대조한 뒤, 시스템 소유
계보·ID·시각만 deterministic code가 주입해 canonical 판단 객체로 승격한다.

```text
LLM → Draft → deterministic validation/assembly → Canonical → Node가 ReviewStore 저장
```

## 2. G2 팩트맵: 조립기 4종

DDR §8의 “4종”은 P0-5 신규 4종이 아니라 전체 시스템 합계다.

| # | 경계 | 조립기 | 상태 | 핵심 불변식 |
|---:|---|---|---|---|
| 1 | EvidenceDraft → Evidence | `assemble_evidence` | P0-4 완료 | provider/source, hash, run dedup, link |
| 2 | ClaimStanceDraft → ClaimEvidence[] | `assemble_claim_evidence` | P0-5 대상 | stance ID union == packet ID set |
| 3 | ClaimEvaluationDraft → ClaimEvaluation | `assemble_claim_evaluation` | P0-5 대상 | 4 bucket union == packet, NumericCheck 주입 |
| 4 | FindingDraft[] → Finding[] | `assemble_findings` | P0-5 대상 | citation ⊆ 해당 evaluation evidence |

따라서 P0-5 카드의 “조립기 3종”과 DDR의 “조립기 4종”은 충돌하지 않는다.

## 3. 권한 경계

| 값 | LLM Draft | deterministic assembler/Node |
|---|---:|---:|
| stance, confidence | 생성 | allowlist·coverage 검증 |
| verdict, missing_dimensions, uncertainty_codes | 생성 | packet 및 규칙 결과와 정합 검증 |
| Finding kind와 의미 연결 | 생성 | 허용 claim/evaluation/evidence 검증 |
| citation span과 evidence 선택 | 생성 | packet/evaluation 부분집합 검증 |
| NumericCheck | 금지 | 규칙이 계산하고 assembler가 주입 |
| claim/evaluation/evidence lineage | 금지 또는 제한된 참조만 | 정본 입력과 대조·주입 |
| canonical ID, created_at | 금지 | caller가 명시 주입, assembler가 사용 |
| stance_source | 금지 | n7 경로에서 `"llm"` 주입 |
| 저장, retry, Usage/counter | 금지 | Node/Orchestrator 소유 |

`confidence`는 frozen이 LLM Draft와 canonical 양쪽에 허용하므로 그대로 전달한다. 이는 모델의
자기보고 confidence일 뿐 시스템 판정 certainty나 투자 확률로 승격하지 않는다.

## 4. n7 조립 계약

정본 흐름:

```text
EvidencePacket → ModelGateway(SMALL, ClaimStanceDraft)
→ assemble_claim_evidence → ReviewStore.put_claim_evidence
```

DDR signature를 유지한다.

```python
def assemble_claim_evidence(
    draft: ClaimStanceDraft,
    claim_id: str,
    packet_evidence_ids: list[str],
    query_id_by_evidence: dict[str, str],
) -> list[ClaimEvidence]: ...
```

검증 순서:

1. packet ID와 mapping key의 중복·집합 정합을 확인한다.
2. Draft ID가 packet 밖이면 `UNKNOWN_REFERENCE`.
3. 누락이면 `COVERAGE_MISMATCH`.
4. 중복은 `ClaimStanceDraft` frozen validator가 schema 단계에서 거부한다.
5. canonical은 packet ID 오름차순으로 만들고 `claim_id`, `query_id`,
   `stance_source="llm"`을 주입하며 confidence를 전달한다.

| 입력 오류 | 처리 |
|---|---|
| E1 누락 | retryable typed failure |
| E1 두 번 | schema ValidationError; Node는 LLM 출력 오류로 1회 재시도 |
| E9 추가 | retryable unknown-reference failure |
| 동일 ID가 두 bucket | n7에는 bucket 구조가 없고 중복 validator가 거부 |

## 5. n8 조립 계약

```text
VerifyPacket → ModelGateway(LARGE, ClaimEvaluationDraft)
→ assemble_claim_evaluation → ReviewStore.put_claim_evaluations(upsert)
```

DDR signature는 canonical ID와 시각의 생성자를 설명하지 못한다. `datetime.now()`와 숨은 ULID
생성을 피하기 위해 다음 explicit injection correction을 제안한다.

```python
def assemble_claim_evaluation(
    draft: ClaimEvaluationDraft,
    claim_id: str,
    packet_evidence_ids: list[str],
    numeric_checks: list[NumericCheck],
    claim_evaluation_id: str,
    created_at: datetime,
) -> ClaimEvaluation: ...
```

검증:

- 4 bucket은 frozen에서 서로 배타적이며 citation은 선언 bucket의 부분집합이다.
- assembler는 4 bucket union이 packet ID set과 정확히 같은지 추가 확인한다.
- NumericCheck evidence ID도 packet allowlist 안이어야 한다.
- `numeric_checks`는 Draft extra field로 넣으면 frozen schema 단계에서 거부된다.
- caller 주입 ID/aware 시각과 claim lineage를 canonical에 주입한다.
- ID list와 citation은 evidence ID 기준 deterministic ordering을 적용한다.

## 6. NumericCheck와 verdict

확정 사실:

- NumericCheck는 deterministic rule 소유다.
- Draft에는 필드가 없고 canonical에만 존재한다.
- frozen canonical validator는 support 계열에 support bucket 또는 consistent NumericCheck,
  contradicted에는 oppose bucket 또는 inconsistent NumericCheck를 backing으로 인정한다.
- frozen은 `LLM support + inconsistent NumericCheck`의 동시 존재를 금지하거나 verdict를 자동
  변경하지 않는다. 복합 Claim에는 지지 Evidence와 불일치 수치 Evidence가 함께 존재할 수 있다.

**P0-5에서는 새 verdict reconciliation rule을 추가하지 않는다.** assembler는 Draft와 규칙 소유
NumericCheck를 정확히 결합하되, 수치 하나로 verdict를 덮어쓰거나 retry를 결정하지 않는다.
별도 `VERDICT_NUMERIC_RECONCILIATION` 후속 카드에서만 다룬다.

## 7. n9 / FindingDraft 계약

정본에는 `FindingDraft (팀원3 정의)`라고만 있고 실제 클래스와 exact field는 없다.
frozen `Finding`과 Draft/canonical 표가 확정한 최소 차이는 `finding_id`, `created_at`뿐이다.
따라서 다음 non-frozen, extra-forbid Draft를 제안한다.

```python
class FindingDraft(BaseModel):
    slot_id: SlotId
    kind: Literal["mismatch", "missing", "unverified", "conflict"]
    citations: list[CitationRef]
    claim_evaluation_id: ULID | None = None
```

위치는 P0-5의 다른 팀원3 output schema와 함께 `app/orchestration/drafts.py`를 추천한다.
frozen.py는 수정하지 않는다.

명시 주입 signature 후보:

```python
def assemble_findings(
    drafts: list[FindingDraft],
    evaluations: list[ClaimEvaluation],
    finding_ids: list[str],
    created_at: datetime,
) -> list[Finding]: ...
```

처리 순서는 Draft validation → evaluation reference allowlist → citation allowlist → frozen required
citation → semantic duplicate 검사 → semantic key 정렬 → 동일 순서의 `finding_ids` 결합 → aware
`created_at` 주입이다. semantic key는 frozen Finding의 의미 필드 전부인
`(slot_id, kind, claim_evaluation_id or "", sorted((evidence_id, span)))`이다. 같은 key가 두 번이면
first/last wins가 아니라 `duplicate_reference` contract violation이다.

- `claim_evaluation_id`가 있으면 정확히 해당 evaluation만 citation authority다.
- `claim_evaluation_id=None`은 IntegrationView의 결손 슬롯에서 만드는 `kind="missing"`에만 허용하는
  방향을 제안한다. 그 외 kind는 특정 evaluation lineage를 요구한다.
- mismatch는 frozen에서도 citation 최소 1개다.
- 모든 citation evidence ID는 해당 evaluation의 4 bucket union과 NumericCheck evidence union 안이어야 한다.
- packet 밖 참조는 retry가 아니라 해당 Finding 폐기 + CONTRACT_VIOLATION 배너가 다이어그램의 확정 흐름이다.
- caller는 canonical sort 후 Draft 개수와 같은 ID 목록 및 aware created_at을 주입한다.

## 8. Citation / reference allowlist

현재 frozen:

- ClaimEvaluation Draft/canonical citation은 4 bucket에 선언된 evidence만 참조한다.
- citation이 비어 있어도 support/oppose bucket과 verdict는 통과할 수 있다.
- Finding mismatch만 citation 최소 1개다.

강화 후보는 “verdict에 쓰인 support/oppose evidence 중 최소 1개를 citation으로 bind”하는 것이다.
이는 frozen amendment candidate이며 P0-5 assembler에서 적용하면 실질적 계약 강화다.

**추천:** G4에서는 현재 frozen 수준과 packet allowlist만 강제하고, stronger binding은 별도
`FREEZE_CORRECTION_CANDIDATE: VERDICT_CITATION_BINDING`으로 남긴다.

## 9. Retry ownership과 Usage

Assembler는 ModelGateway, prompt, View를 갖지 않는 순수 함수다.

```text
Node: invoke → Draft → assembler
                    ↓ typed retryable failure
Node: invoke 1회 재시도 → assembler
```

- retry 정책은 Node/Orchestrator 소유다.
- 첫 호출과 retry 각각의 Usage를 관측/비용 계층에 기록한다.
- `total_llm_calls`에는 실제 두 호출을 모두 더한다.
- counter 상한 `4C+9`와 BudgetExceeded→n12 흐름은 유지한다.
- assembler는 Usage를 만들거나 counter를 변경하지 않는다.

## 10. retry 1회 실패 후 orchestration 경계

DDR은 n7/n8 coverage 불일치에 “재시도 1회 → COVERAGE_TRUNCATED + 배너”를 확정했지만,
그 뒤 누락 ID를 어떻게 canonical 객체로 만들거나 다음 edge로 진행할지는 정의하지 않았다.

P0-5 assembler는 `coverage_mismatch`의 retryable AssemblyError까지만 책임진다. Node 계약은
첫 실패에 동일 node LLM 1회 retry, 두 번째 실패에
`NodeResult(PARTIAL, COVERAGE_TRUNCATED, retry_count=1)` 기록까지다. 이전 canonical 유지,
첫 run skip, 다음 edge, 배너 channel은 `NODE_ORCHESTRATION_FOLLOW_UP`으로 이동한다.

n9의 unknown citation은 다이어그램상 retry가 아니라 해당 Finding 폐기 + 배너다.

## 11. Typed assembly failure

ReasonCode에는 coverage/contract 구분이 이미 있지만 unknown/duplicate 세부 코드는 없다.
frozen enum을 늘리지 않고 non-frozen 단일 exception을 제안한다.

```python
class AssemblyError(ValueError):
    kind: Literal[
        "coverage_mismatch", "unknown_reference", "duplicate_reference",
        "contract_violation"
    ]
    reason_code: ReasonCode
    retryable: bool
```

매핑:

| kind | ReasonCode | retryable |
|---|---|---:|
| coverage_mismatch | COVERAGE_TRUNCATED | true |
| unknown_reference | CONTRACT_VIOLATION | true (LLM Draft), false (caller 입력) |
| duplicate_reference | SCHEMA_INVALID | true (LLM Draft) |
| contract_violation | CONTRACT_VIOLATION | false |

같은 kind라도 오류 주체가 LLM 출력인지 programmer/caller 입력인지에 따라 retryability가 달라진다.

## 12. Store write ownership

| 접근 | 조립기 | 저장 | retry | 평가 |
|---|---|---|---|---|
| A Pure | canonical 반환 | Node | Node | DDR signature/다이어그램과 일치, 테스트 용이 |
| B Store-writing | 검증+저장 | 조립기 | Node | P0-4와 표면상 유사, partial batch atomicity 추가 |
| C Agentic | 검증+저장+invoke | 조립기 | 조립기 | prompt/Store/orchestration 혼합 |

**추천: A.** P0-4는 dedup 조회·link가 조립 자체의 정본 불변식이라 Store DI가 필요했다.
P0-5 canonical 검증은 저장 없이 완결되고, DDR/다이어그램도 assemble 다음에 별도 put을 둔다.

## 13. Deterministic ordering / duplicate policy

- packet input은 먼저 ID 중복을 거부하고 set으로 의미 비교한다.
- canonical ClaimEvidence는 evidence_id sort.
- evaluation 4 bucket과 citations는 evidence_id sort; 동일 citation ID+span 완전 중복만 collapse할지
  아니면 거부할지는 frozen이 정하지 않았다. **추천은 중복 거부**다.
- FindingDraft는 `(slot_id, kind, claim_evaluation_id or "", citation semantic key)`로 sort한 뒤 ID와 결합한다.
- 같은 semantic Finding의 반복은 first/last wins하지 않고 duplicate failure로 처리한다.
- LLM 출력 순서는 canonical 의미나 ID 배정 순서가 아니다.

## 14. Cross-card gaps

1. `FindingDraft`와 n1/n3/n4/n10/n11 output schema가 아직 실제 코드에 없다.
2. canonical ClaimEvaluation/Finding ID와 created_at 생성 helper가 없다. P0-4의 hash 기반 ID는
   Evidence identity 전용이므로 재사용 근거가 없다.
3. retry 실패 배너를 저장할 State channel이 없다. `node_results` 문자열에서 report가 배너를
   도출하는 계약도 없다.
4. coverage 두 번째 실패 이후 저장 유지/교체 정책과 다음 edge가 Node 카드에 없다.
5. ModelGateway Protocol은 `output_schema: type[BaseModel]`만 허용하지만 runtime에서 View-only와
   canonical output 금지를 강제하지 않는다. Mock과 I8이 별도로 막아야 한다.
6. `FindingDraft.claim_evaluation_id=None`일 때 허용되는 kind/lineage 규칙이 미정이다.

P0-5 G4에서 임의로 다른 카드나 frozen/Protocol을 수정하지 않는다.

## 14A. 팀원3 LLM Output Schema pack

모든 모델은 `ConfigDict(extra="forbid", frozen=True, validate_default=True)`를 사용한다.
아래 nested item도 같은 폐쇄 계약을 적용한다.

| Schema | Node | exact 최소 fields | 금지되는 system-owned fields | Consumer |
|---|---|---|---|---|
| `GuardScanResult` | n1 | `reason_code: ReasonCode | None` | Alert, NodeResult, 상태·시각 | n1 router; None이면 통과 |
| `SlotExtractionDraft` | n3 | `claims: list[ExtractedClaimDraft]` | claim_id, origin, created_at, superseded_by | n3 deterministic Claim assembler |
| `AskBackDraft` | n4 | `questions: list[AskBackQuestionDraft]` | question ID, user_action, State | n4 interrupt payload builder |
| `FindingDraft` | n9 | `slot_id`, `kind`, `citations`, `claim_evaluation_id` | finding_id, created_at | `assemble_findings` |
| `GuardVerdictDraft` | n10 | `violations: list[Violation]` | Alert, retry count, NodeResult | n10 rewrite/block router |
| `RenderDraft` | n11 | `slots: list[RenderedSlotDraft]` | report_id, Finding/Evaluation mutation | deterministic markdown/report assembler |

### GuardScanResult

```python
class GuardScanResult(OutputModel):
    reason_code: Literal[
        ReasonCode.SELF_HARM_SIGNAL,
        ReasonCode.ILLEGAL_REQUEST,
        ReasonCode.PII_DETECTED,
        ReasonCode.OUT_OF_SCOPE,
        ReasonCode.PROMPT_INJECTION,
        ReasonCode.INPUT_INSUFFICIENT,
    ] | None = None
```

정본 흐름은 “판정→여섯 ReasonCode 또는 통과”뿐이다. 별도 `blocked` boolean은 reason과 중복이며,
Alert와 State update는 n12 소유다.

### SlotExtractionDraft

```python
class ExtractedClaimDraft(OutputModel):
    slot_id: SlotId
    user_text_span: NonBlankStr
    span_offset: tuple[int, int]
    normalized_proposition: NonBlankStr
    verifiable: bool

class SlotExtractionDraft(OutputModel):
    claims: list[ExtractedClaimDraft]
```

n3 consumer는 span을 원문과 대조한 뒤 claim_id, `origin=LLM_EXTRACTION`, created_at을 주입하고,
슬롯 status/conflict를 규칙으로 유도한다. confidence, source_trace, version은 downstream이 읽지 않는다.
n3b는 DDR상 LLM 0콜이므로 이 output schema의 실제 producer가 아니다.

### AskBackDraft

```python
class AskBackQuestionDraft(OutputModel):
    slot_id: SlotId
    question: NonBlankStr

class AskBackDraft(OutputModel):
    questions: list[AskBackQuestionDraft]
```

AskBackContext가 이미 결손 이유와 최대 2슬롯을 제공한다. priority/reason을 다시 출력할 필요가 없다.
n4는 슬롯 번호가 붙은 질문을 interrupt payload로 바꾸며 user 답변은 `user_action`에 별도 기록한다.

### FindingDraft

```python
class FindingDraft(OutputModel):
    slot_id: SlotId
    kind: Literal["mismatch", "missing", "unverified", "conflict"]
    citations: list[CitationRef]
    claim_evaluation_id: ULID | None = None
```

`claim_evaluation_id=None`은 `kind="missing"`에만 허용하는 proposed validator를 둔다. missing은
IntegrationView.missing_slots에서 나오므로 특정 ClaimEvaluation이 없을 수 있다. mismatch,
unverified, conflict는 어떤 정식 평가에서 유도됐는지 lineage가 필요하다.

### GuardVerdictDraft

```python
class GuardVerdictDraft(OutputModel):
    violations: list[Violation]
```

`Violation`은 이미 slot_no, rule_id, kind, matched, span_offset을 담는다. pass/block boolean이나
ReasonCode를 중복 출력하지 않고, n10이 빈 목록이면 통과, 존재하면 종류에 따라 재작성/차단한다.
deterministic lexicon 검사 결과와 LLM 결과의 합집합·중복 제거는 n10 code 소유다.

### RenderDraft

```python
class RenderedSlotDraft(OutputModel):
    slot_no: SlotId
    text: NonBlankStr
    citations: list[CitationRef]

class RenderDraft(OutputModel):
    slots: list[RenderedSlotDraft]
```

n11은 RenderView의 확정 내용만 한국어로 서술한다. banners와 TheoryNote는 입력 정본을 deterministic
report assembler가 붙이므로 LLM이 다시 출력하지 않는다. citation은 RenderView allowlist에
대조하며 report_id와 저장 본문은 Node/ReviewStore 소유다.

## 14B. Schema 위치 A/B/C

| 안 | 장점 | 단점 |
|---|---|---|
| A `app/orchestration/drafts.py` | node 의미와 assembler가 한 소유권, frozen과 분리 | Mock이 orchestration을 import |
| B `app/models/drafts.py` | MockModelGateway import가 자연스러움 | 모델 provider 계층이 node 의미를 소유 |
| C node별 파일 | 지역성 | 6개 작은 타입이 분산되고 allowlist import가 복잡 |

**추천: A.** 이 타입들은 provider 응답 일반형이 아니라 n1/n3/n4/n9/n10/n11의 orchestration
output contract다. 팀원3 소유 경계 안이고 현재 크기에서는 단일 파일이 가장 작다.

## 14C. MockModelGateway contract correction

허용 output schema는 정확히 다음 8종이다.

```text
GuardScanResult, SlotExtractionDraft, AskBackDraft, ClaimStanceDraft,
ClaimEvaluationDraft, FindingDraft, GuardVerdictDraft, RenderDraft
```

allowlist 밖은 모두 거부하므로 Evidence, ClaimEvidence, ClaimEvaluation, Finding canonical 4종도
자동 거부된다. blacklist를 임의 확장하지 않는다. Usage는 frozen 필드에 맞춰 다음처럼 만든다.

```python
Usage(
    model_slot=slot,
    prompt_tokens=0,
    cached_input_tokens=0,
    cache_write_tokens=0,
    output_tokens=0,
    ctx_chars=ctx_chars(input_view),
)
```

`input_view.ctx_chars()`는 존재하지 않으며 `app.contexts.budget.ctx_chars`가 P0-3 정본이다.

## 15. G4 테스트 및 mutation 계획

파일 후보:

```text
app/orchestration/drafts.py
app/orchestration/assemble.py
app/models/gateway.py              # MockModelGateway only
tests/orchestration/test_assemble.py
tests/models/test_mock_gateway.py
```

핵심 테스트:

- n7 완전 coverage, 누락, unknown, schema duplicate, packet shuffle 동일 의미.
- n8 Draft+NumericCheck, Draft numeric_checks schema 거부, bucket/allowlist, exact 결합.
- n9 valid conversion, mismatch citation, unknown evaluation/evidence, duplicate, shuffle.
- explicit canonical ID/aware created_at 주입, current time/random 부재.
- MockModelGateway 허용 Draft schema와 canonical 4종 거부, Usage ctx_chars.

| mutation | 탐지 테스트 |
|---|---|
| coverage 검사 제거 | n7/n8 누락 |
| unknown reference 검사 제거 | 각 assembler packet 밖 ID |
| duplicate 검사 제거 | stance/citation/Finding duplicate |
| citation union 검사 제거 | n8 citation 및 n9 evaluation 밖 citation |
| Draft numeric 값을 신뢰 | numeric_checks extra 거부 + 규칙 주입 동일성 |
| canonical ID를 Draft에 허용 | extra-forbid/Mock canonical output 거부 |
| retryability 구분 제거 | AssemblyError mapping table test |
| LLM order 유지 | shuffle metamorphic test |

## 16. G4 실행 계획

1. Draft/Result schema pack tests RED.
2. schema pack 최소 구현.
3. schema pack GREEN.
4. AssemblyError tests RED.
5. minimal typed failure 구현.
6. AssemblyError GREEN.
7. n7 assembler tests RED.
8. n7 최소 구현.
9. n7 GREEN.
10. n8 assembler tests RED.
11. n8 최소 구현.
12. n8 GREEN.
13. n9 assembler tests RED.
14. n9 최소 구현.
15. n9 GREEN.
16. MockModelGateway allowlist + `ctx_chars` tests RED.
17. MockModelGateway 최소 구현.
18. MockModelGateway GREEN.
19. 각 계약 mutation 및 원복.
20. 전체 pytest.
21. Ruff.
22. frozen diff 없음 확인.
23. 승인된 docs/status 동기화.
24. 원자 커밋.

LangGraph Node retry, Store write orchestration, State banner channel은 구현하지 않는다.

## 17. 접근안과 추천

- **A Pure assembler:** 추천. 저장·retry·Usage는 Node, 조립기는 검증과 canonical 반환만.
- **B Store-writing assembler:** P0-4 형태와 비슷하지만 ReviewStore side effect가 검증 테스트를 무겁게 함.
- **C Agentic assembler:** ModelGateway/prompt/retry까지 섞어 책임 경계를 깨므로 제외.

추천 조합은 `A + explicit ID/time injection + Node-owned retry contract + typed AssemblyError`다.

## 18. USER_APPROVAL_REQUIRED

DDR/frozen에서 아직 확정되지 않아 승인 후에만 G4에 반영할 항목:

1. §14A의 여섯 output schema exact fields와 nested item fields.
2. schema pack 위치 `app/orchestration/drafts.py`.
3. n8 signature의 `claim_evaluation_id`, `created_at` explicit injection.
4. n9 signature의 semantic-sort 이후 `finding_ids`, `created_at` explicit injection.
5. Pure assembler; Store write와 LLM retry는 Node 소유.
6. AssemblyError 최소 4 kind와 오류 주체별 retryable 구분.
7. MockModelGateway 8종 allowlist와 `budget.ctx_chars(input_view)` Usage.
8. NumericCheck/verdict reconciliation을 P0-5에서 하지 않음.
9. coverage 두 번째 실패 이후 orchestration을 후속 카드로 이동.
10. canonical deterministic ordering과 semantic duplicate 거부.

### DECIDED_FROM_DDR

- n7/n8 packet coverage exact equality와 1회 retry 방향.
- NumericCheck는 rule 소유이며 Draft가 생성하지 않는다.
- canonical 4종을 LLM output schema로 쓰지 않는다.
- P0-5 citation 검증은 현 frozen contract 수준까지만 적용한다.
- assembler 4종 중 Evidence assembler는 P0-4 완료, P0-5 신규는 3종이다.

## 19. 가장 확신이 낮은 결정

`FindingDraft.claim_evaluation_id=None`을 missing에만 허용하는 결정이다. missing slot은 evaluation이
없을 수 있다는 근거가 있지만, unverified/conflict가 integration 수준에서 evaluation 없이 생성될
가능성을 DDR이 명시적으로 닫지는 않았다. 사용자 승인 뒤 이 경계를 contract test로 고정해야 한다.

## 20. 기존 backlog reference

- T2-D: ReplayCache fetched_at provenance.
- FREEZE_CORRECTION_CANDIDATE: HASH_SERIALIZATION_AMBIGUITY.
- FREEZE_CORRECTION_CANDIDATE: VERDICT_CITATION_BINDING.
- VERDICT_NUMERIC_RECONCILIATION: P0-5 이후 별도 rule 카드.
- NODE_ORCHESTRATION_FOLLOW_UP: coverage 두 번째 실패 이후 저장/skip/edge/banner.
- P0-7: I3/I4 thin wrapper.
