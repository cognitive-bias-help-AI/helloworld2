# Minimal Review UI Design

## Goal

기존 `compose_local_runtime()`과 LangGraph를 그대로 사용하면서, 한 번의 투자 판단 입력·기존 HITL 재개·최종 근거 보고서 확인이 가능한 로컬 Next.js UI를 제공한다.

## Scope

- 한 페이지 Next.js App Router UI
- 브라우저에서 시작 요청과 HITL 응답 전송
- Next.js 서버에서 review별 Python worker 수명 관리
- Python worker에서 기존 local runtime과 graph 호출
- 실제 Store의 Stock, Claim, ClaimEvaluation, Evidence, ReportArtifact를 얇은 UI view로 변환
- idle, loading, HITL, success, degraded, error 상태

다음은 범위 밖이다: 인증, DB, 사용자 기록, streaming progress, FastAPI, production service, 새 HITL 정책, graph/prompt/model/provider 계약 변경.

## Invariants

1. UI는 canonical 결과를 표현할 뿐 새로운 판단을 만들지 않는다.
2. 하나의 review session에는 동시에 하나의 worker request만 존재한다.
3. `result`, `error`, timeout, protocol failure, process exit는 모두 terminal이며 child process와 session을 제거한다.
4. UI 편의를 위해 기존 Store, Graph, Provider, Model, HITL contract를 변경하지 않는다.

## Architecture

```text
Browser
  -> POST /api/reviews
  -> Next.js server-only ReviewWorkerSession
  -> uv run python -m app.ui_bridge
  -> compose_local_runtime()
  -> existing LangGraph
  -> JSON Lines: hitl | result | error
  -> UI render
```

Next.js 서버는 review마다 Python child process 하나를 유지한다. Python process는 runtime과 checkpointer를 process lifetime 동안 소유하므로 기존 `Command(resume=...)` HITL 계약을 바꾸지 않는다. 세션은 로컬 개발 서버 메모리에만 존재하며 서버 재시작 시 사라진다.

각 `ReviewWorkerSession`은 in-flight 요청 flag를 하나만 가지며, 응답을 기다리는 동안 두 번째 `send()`를 거부한다. `result`, `error`, timeout, 잘못된 JSON/protocol, 예상하지 않은 child exit는 같은 terminal cleanup 경로를 호출해 process를 종료하고 session map에서 제거한다.

## Worker Protocol

Node에서 Python으로 보내는 한 줄 JSON:

```json
{"kind":"start","text":"삼성전자 살까?"}
{"kind":"resume","value":{"selected_code":"005930"}}
{"kind":"resume","value":{"answers":[{"ask_id":"...","answer":"2025년"}]}}
```

Python에서 Node로 보내는 한 줄 JSON:

```json
{"kind":"hitl","payload":{"candidates":[]}}
{"kind":"hitl","payload":{"schema_version":"intake_review_hitl/v1","questions":[]}}
{"kind":"result","result":{"stock":{},"claims":[],"finalSummary":"...","degraded":false}}
{"kind":"error","message":"점검을 완료하지 못했습니다."}
```

stdout은 protocol JSON 전용이며 진단은 stderr로 보낸다. API key, DSN, raw environment는 response나 UI에 포함하지 않는다.

## Result View

UI가 받는 모델은 렌더링에 필요한 최소 필드만 갖는다.

```ts
type ReviewStatus = "verified" | "partial" | "needs_more_information" | "unverified";
type EvidenceView = {
  id: string;
  sourceType?: string;
  title: string;
  publishedAt?: string;
  url?: string;
};
type ClaimReviewView = {
  id: string;
  claim: string;
  summary: string;
  status: ReviewStatus;
  evidence: EvidenceView[];
};
type ReviewResultView = {
  stock?: { code?: string; name?: string };
  claims: ClaimReviewView[];
  finalSummary: string;
  degraded: boolean;
};
```

Claim status는 canonical `ClaimEvaluation.verdict`만 표현 계층에서 변환한다. frontend는 EvidenceNeed, stance, score, financial calculation을 수행하지 않는다. Evidence URL과 publisher는 backend provenance만 사용한다. `finalSummary`는 저장된 `ReportArtifact.rendered_slots` 텍스트를 사용한다.

## HITL

worker는 graph interrupt payload를 변경하지 않고 Node에 전달한다.

- `candidates` payload: radio 선택 후 `{selected_code}` resume
- `intake_review_hitl/v1`: 모든 질문에 답한 후 `{answers:[{ask_id,answer}]}` resume

UI는 새로운 질문을 만들지 않으며 retrieval parameter HITL을 추가하지 않는다.

## UI

- 제목: `투자 판단 점검`과 `Beta`
- textarea와 `판단 근거 점검하기` 버튼
- running 동안 중복 submit 금지 및 비권위 로딩 문구만 표시
- stock, claim cards, evidence, final summary 렌더링
- degraded banner와 fatal error card 분리
- `매수·매도 추천이 아닌 판단 근거 점검 결과입니다.` 상시 표시
- 모바일 대응, keyboard/focus, symbol+text status

## Error Handling

- Python exception 세부 내용은 stderr에만 기록
- 브라우저에는 고정된 안전 메시지 제공
- worker 종료·timeout·invalid protocol은 Next Route Handler에서 일반 오류로 변환
- fatal error에서는 해당 process session을 제거
- degraded backend result는 success response로 유지

## Verification

- Python bridge serializer와 HITL/result protocol pytest
- frontend `npm run lint` 및 `npm run build`
- 브라우저 수동 확인: idle, loading, stock HITL, question HITL, success, degraded, error
- backend adapter 파일이 추가되므로 전체 pytest, Ruff, invariants 재실행

## Known Limitation

이 구조는 local live-E2E validation 전용이다. Next server 재시작과 serverless instance 이동을 견디는 persistent session은 지원하지 않는다.
