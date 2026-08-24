import assert from "node:assert/strict";
import test from "node:test";

import { createReviewPostHandler } from "../app/api/reviews/route.ts";

const intake = {
  mode: "SURVEY_FIRST" as const,
  target: { name: "삼성전자" },
  structured: [
    { slotId: 1, responseState: "answered" as const, value: "CONSIDER_ENTRY" },
    { slotId: 2, responseState: "answered" as const, value: "NOT_HOLDING" },
    { slotId: 3, responseState: "answered" as const, value: "LONG" },
    { slotId: 4, responseState: "answered" as const, value: "AI 수요와 실적 개선을 기대합니다." },
  ],
};

function request(body: unknown) {
  return new Request("http://localhost/api/reviews", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

test("review route accepts exactly the structured intake body", async () => {
  let received: unknown;
  const post = createReviewPostHandler(async (value) => {
    received = value;
    return { sessionId: "s1", kind: "result", result: { stock: { code: null, name: null }, claims: [], evidence: [], finalSummary: "done", banners: [], degraded: false, judgmentContext: {} } };
  });

  const response = await post(request({ intake }));

  assert.equal(response.status, 200);
  assert.deepEqual(received, intake);
  assert.equal((await response.json()).sessionId, "s1");
});

test("review route rejects invalid structured intake with 400", async () => {
  const post = createReviewPostHandler(async () => { throw new Error("must not start"); });
  const response = await post(request({ intake: { ...intake, mode: "INVALID" } }));

  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { message: "입력 내용을 다시 확인해주세요." });
});

test("review route exposes a fixed public 500 when the worker fails", async () => {
  const post = createReviewPostHandler(async () => { throw new Error("secret detail"); });
  const response = await post(request({ intake }));

  assert.equal(response.status, 500);
  assert.deepEqual(await response.json(), { kind: "error", code: "REVIEW_FAILED", message: "검토를 시작하지 못했습니다." });
});

test("the same static review route dispatches resume by session id", async () => {
  let resumed: unknown;
  const post = createReviewPostHandler(
    async () => { throw new Error("must not start"); },
    async (sessionId, value) => {
      resumed = { sessionId, value };
      return { kind: "hitl", payload: { questions: [] } };
    },
  );

  const response = await post(request({ sessionId: "s1", value: { selected_code: "005930" } }));

  assert.equal(response.status, 200);
  assert.deepEqual(resumed, { sessionId: "s1", value: { selected_code: "005930" } });
  assert.equal((await response.json()).kind, "hitl");
});

test("unknown resume session returns a defined JSON error instead of Next HTML", async () => {
  const post = createReviewPostHandler(
    async () => { throw new Error("must not start"); },
    async () => { throw new Error("review session not found"); },
  );

  const response = await post(request({ sessionId: "missing", value: {} }));

  assert.equal(response.headers.get("content-type")?.includes("application/json"), true);
  assert.deepEqual(await response.json(), {
    kind: "error",
    code: "REVIEW_FAILED",
    message: "검토를 계속하지 못했습니다.",
  });
});

test("malformed resume is rejected before the worker is called", async () => {
  let calls = 0;
  const post = createReviewPostHandler(
    async () => { throw new Error("must not start"); },
    async () => { calls += 1; },
  );

  const response = await post(request({ sessionId: "s1" }));

  assert.equal(response.status, 400);
  assert.equal(calls, 0);
  assert.deepEqual(await response.json(), { message: "재개 요청을 다시 확인해주세요." });
});
