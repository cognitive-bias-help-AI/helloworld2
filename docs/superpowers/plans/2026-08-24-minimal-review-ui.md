# Minimal Review UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 Python review runtime을 그대로 호출해 입력, 기존 HITL, 실제 결과를 브라우저에서 확인하는 최소 Next.js UI를 만든다.

**Architecture:** Next.js Route Handler가 review별 Python JSON-Lines worker를 유지한다. Python worker는 `compose_local_runtime()`과 LangGraph checkpointer를 process lifetime 동안 소유하며, Store의 canonical 결과를 작은 UI view로 변환한다.

**Tech Stack:** Python 3.12+, LangGraph, Next.js App Router, React, TypeScript, CSS, native fetch, Node child_process

**Spec:** `docs/superpowers/specs/2026-08-24-minimal-review-ui-design.md`

## Global Constraints

- Branch는 `feat/production-gateway-and-entrypoint`를 유지한다.
- 기존 미커밋 defect-closeout 변경을 보존한다.
- commit, push, PR 작업을 하지 않는다.
- `.claude/settings.local.json`, frozen schema, graph, prompt, model registry, provider contract를 수정하지 않는다.
- secret은 Python/Next server process 안에만 두고 브라우저 bundle이나 `NEXT_PUBLIC_*`에 넣지 않는다.
- 새 backend retry, persistence, HTTP service, HITL policy를 만들지 않는다.
- UI는 canonical 결과를 표시할 뿐 새 판단을 만들지 않는다.
- review session 하나에는 동시에 하나의 worker request만 허용한다.
- result/error/timeout/protocol failure/process exit는 모두 terminal cleanup을 수행한다.
- 기존 Store/Graph/Provider/Model/HITL contract는 변경하지 않는다.

---

### Task 1: Python UI bridge result projection

**Files:**
- Create: `app/ui_bridge.py`
- Create: `tests/runtime/test_ui_bridge.py`

**Interfaces:**
- Consumes: `compose_local_runtime`, `initial_state`, `ReviewRequestContext`, `Command(resume=...)`, ReviewStore/EvidenceStore 조회 메서드
- Produces: `async def run_worker(stdin, stdout) -> None`, `async def build_result_view(runtime, state) -> dict`

- [ ] **Step 1: Write failing projection tests**

```python
@pytest.mark.asyncio
async def test_result_view_uses_canonical_claim_evaluation_evidence_and_report():
    view = await build_result_view(runtime, completed_state)
    assert view["stock"] == {"code": "005930", "name": "삼성전자"}
    assert view["claims"][0]["status"] == "verified"
    assert view["claims"][0]["evidence"][0]["url"] == "https://example.com"
    assert view["finalSummary"] == "저장된 보고서 문장"

def test_public_error_does_not_include_exception_or_secret():
    assert public_error(RuntimeError("sk-secret")) == {
        "kind": "error",
        "message": "점검을 완료하지 못했습니다. API 연결 또는 필수 설정을 확인해주세요.",
    }
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest -q tests/runtime/test_ui_bridge.py`

Expected: import failure because `app.ui_bridge` does not exist.

- [ ] **Step 3: Implement the minimal projection**

```python
_VERDICT_STATUS = {
    "support": "verified",
    "partial_support": "partial",
    "contradicted": "partial",
    "unsupported": "unverified",
    "unverifiable": "unverified",
}

async def build_result_view(runtime, state):
    report = await runtime.deps.review_store.get_report(state["report_id"])
    claims = await runtime.deps.review_store.get_claims(state["claim_ids"])
    evaluations = await runtime.deps.review_store.get_claim_evaluations(
        state["claim_evaluation_ids"]
    )
    # Join by canonical claim_id/evidence_id and emit only render fields.
```

The implementation must use backend-provided `source_url`, `publisher`, `published_at`, `raw_span`, and stored report text. It must not invent URLs or financial conclusions.

- [ ] **Step 4: Run projection GREEN**

Run: `uv run pytest -q tests/runtime/test_ui_bridge.py`

Expected: all tests pass.

---

### Task 2: Python JSON-Lines worker and HITL

**Files:**
- Modify: `app/ui_bridge.py`
- Modify: `tests/runtime/test_ui_bridge.py`

**Interfaces:**
- Consumes: one `start` line, then zero or more `resume` lines
- Produces: exactly one `hitl`, `result`, or `error` line per input line

- [ ] **Step 1: Write failing worker protocol tests**

```python
@pytest.mark.asyncio
async def test_worker_emits_existing_interrupt_payload_and_resumes():
    output = await drive_worker([
        {"kind": "start", "text": "삼성전자 살까?"},
        {"kind": "resume", "value": {"selected_code": "005930"}},
    ])
    assert output[0]["kind"] == "hitl"
    assert output[0]["payload"]["candidates"][0]["selected_code"] == "005930"
    assert output[1]["kind"] in {"hitl", "result"}
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest -q tests/runtime/test_ui_bridge.py -k worker`

Expected: protocol handler is absent.

- [ ] **Step 3: Implement runtime loop**

```python
async with compose_local_runtime() as runtime:
    state = initial_state(run_id, run_id)
    config = {"configurable": {"thread_id": run_id}}
    result = await runtime.graph.ainvoke(
        state, config, context=ReviewRequestContext(raw_text=text)
    )
    while result.get("__interrupt__"):
        emit({"kind": "hitl", "payload": result["__interrupt__"][0].value})
        resume = await read_json_line()
        result = await runtime.graph.ainvoke(Command(resume=resume["value"]), config)
    emit({"kind": "result", "result": await build_result_view(runtime, result)})
```

Load `.env` before composition, keep stdout JSON-only, and send diagnostics to stderr.

- [ ] **Step 4: Run worker GREEN and Python surrounding tests**

Run: `uv run pytest -q tests/runtime/test_ui_bridge.py tests/runtime/test_local.py tests/s0/test_intake_review_graph.py`

Expected: all tests pass.

---

### Task 3: Next.js server process boundary

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/next.config.ts`
- Create: `frontend/eslint.config.mjs`
- Create: `frontend/app/api/reviews/route.ts`
- Create: `frontend/app/api/reviews/[sessionId]/route.ts`
- Create: `frontend/lib/review-worker.ts`
- Create: `frontend/lib/types.ts`

**Interfaces:**
- `startReview(text: string): Promise<ReviewResponse & {sessionId: string}>`
- `resumeReview(sessionId: string, value: unknown): Promise<ReviewResponse>`
- `POST /api/reviews` body `{text}`
- `POST /api/reviews/:sessionId` body `{value}`

- [ ] **Step 1: Scaffold only required dependencies**

```json
{
  "scripts": {
    "dev": "next dev",
    "lint": "eslint .",
    "build": "next build"
  },
  "dependencies": {
    "next": "latest",
    "react": "latest",
    "react-dom": "latest"
  },
  "devDependencies": {
    "@eslint/eslintrc": "latest",
    "@types/node": "latest",
    "@types/react": "latest",
    "@types/react-dom": "latest",
    "eslint": "latest",
    "eslint-config-next": "latest",
    "typescript": "latest"
  }
}
```

- [ ] **Step 2: Implement server-only worker manager**

```ts
import "server-only";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";

const sessions = new Map<string, ReviewWorkerSession>();

export async function startReview(text: string) {
  const session = ReviewWorkerSession.spawn();
  sessions.set(session.id, session);
  return { sessionId: session.id, ...(await session.send({ kind: "start", text })) };
}
```

The worker must use repository root as cwd, buffer complete newline-delimited JSON, timeout stalled calls, reject a second `send()` while one is in flight, and never return stderr to the browser. `result`, `error`, timeout, invalid JSON/protocol, and child exit must call one idempotent terminal cleanup that kills the child and removes the session.

- [ ] **Step 3: Implement thin Route Handlers**

```ts
export async function POST(request: Request) {
  const body = await request.json();
  if (typeof body.text !== "string" || !body.text.trim()) {
    return Response.json({ message: "판단 내용을 입력해주세요." }, { status: 400 });
  }
  return Response.json(await startReview(body.text.trim()));
}
```

- [ ] **Step 4: Install and type/build-check the server boundary**

Run: `npm install`

Run: `npm run lint`

Run: `npm run build`

Expected: all commands exit 0.

---

### Task 4: One-page UI states

**Files:**
- Create: `frontend/app/layout.tsx`
- Create: `frontend/app/page.tsx`
- Create: `frontend/app/globals.css`
- Create: `frontend/components/ReviewInput.tsx`
- Create: `frontend/components/HitlPrompt.tsx`
- Create: `frontend/components/ReviewResult.tsx`
- Create: `frontend/components/ErrorCard.tsx`
- Create: `frontend/lib/api.ts`

**Interfaces:**
- Browser consumes only `/api/reviews` and `/api/reviews/:sessionId`
- UI state union: `idle | loading | hitl | success | error`

- [ ] **Step 1: Implement typed browser API client**

```ts
export async function submitReview(text: string): Promise<ReviewResponse> {
  const response = await fetch("/api/reviews", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) throw new Error("review request failed");
  return response.json();
}
```

- [ ] **Step 2: Implement idle/loading and duplicate-submit prevention**

Use a labelled textarea, disabled button while loading, `aria-live="polite"`, and only the non-authoritative text `판단 근거를 점검하고 있습니다...`.

- [ ] **Step 3: Implement existing HITL payload rendering**

```tsx
if ("candidates" in payload) {
  return candidates.map(candidate => (
    <label key={candidate.selected_code}>
      <input type="radio" name="stock" value={candidate.selected_code} />
      {candidate.display_name} {candidate.selected_code}
    </label>
  ));
}
```

Question HITL sends every backend `ask_id` with the user's nonblank answer. It does not generate questions.

- [ ] **Step 4: Implement actual result/degraded/error rendering**

Render stock, claims, backend evidence links, backend report summary, symbol+text status, degraded banner, readable fatal error, and the fixed disclaimer. Never render BUY/SELL, scores, targets, or diagnoses.

- [ ] **Step 5: Add restrained responsive CSS**

Use a centered max-width layout, light neutral background, cards, visible focus rings, responsive spacing, and status text plus symbols. Do not add animation packages or a design system.

- [ ] **Step 6: Run frontend checks**

Run: `npm run lint`

Run: `npm run build`

Expected: both commands exit 0.

---

### Task 5: End-to-end verification

**Files:**
- Modify only files from Tasks 1-4 if verification reveals an in-scope defect.

**Interfaces:**
- Final proof covers Browser -> Next server -> Python worker -> existing runtime -> HITL/result.

- [ ] **Step 1: Start the UI locally**

Run from `frontend`: `npm run dev`

- [ ] **Step 2: Manually verify required states**

Verify idle input/button, duplicate-submit prevention, loading text, stock HITL, question HITL, success stock/claims/evidence/summary, degraded banner, readable error, focus/keyboard, and mobile width. Do not claim live provider evidence unless credentials and corp-code cache are present and observed.

- [ ] **Step 3: Run final frontend verification**

Run: `npm run lint`

Run: `npm run build`

- [ ] **Step 4: Run final backend regression**

Run: `uv sync`

Run: `uv run pytest -q`

Run: `uv run ruff check .`

Run: `uv run python -m ci.invariants`

- [ ] **Step 5: Audit scope and secrets**

Run: `git status --short`

Run: `git diff --stat`

Run: `git diff --check`

Run: `rg -n "ANTHROPIC_API_KEY|DART_API_KEY|NAVER_CLIENT_SECRET|KIWOOM_APP_SECRET|DATABASE_URL" frontend`

Expected: no secret values or `NEXT_PUBLIC_*` secret references, no unrelated backend changes, no commit or push.
