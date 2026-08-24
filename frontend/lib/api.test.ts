import assert from "node:assert/strict";
import test from "node:test";

import { resumeReview, submitReview } from "./api.ts";

test("submitReview posts the structured intake body", async () => {
  const originalFetch = globalThis.fetch;
  let request: Request | undefined;
  globalThis.fetch = async (input, init) => {
    request = new Request(typeof input === "string" ? `http://localhost${input}` : input, init);
    return Response.json({
      kind: "result",
      result: { stock: { code: null, name: null }, claims: [], evidence: [], finalSummary: "done", banners: [], degraded: false },
    });
  };

  try {
    await submitReview({
      mode: "HYBRID",
      target: { name: "삼성전자" },
      structured: [{ slotId: 4, responseState: "answered", value: "AI 수요와 실적 개선을 기대합니다." }],
      freeText: ["추가로 HBM 공급 부족을 우려합니다."],
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.ok(request);
  assert.equal(request.method, "POST");
  assert.deepEqual(await request.json(), {
    intake: {
      mode: "HYBRID",
      target: { name: "삼성전자" },
      structured: [{ slotId: 4, responseState: "answered", value: "AI 수요와 실적 개선을 기대합니다." }],
      freeText: ["추가로 HBM 공급 부족을 우려합니다."],
    },
  });
});

test("resumeReview posts session and value to the static review route", async () => {
  const originalFetch = globalThis.fetch;
  let request: Request | undefined;
  globalThis.fetch = async (input, init) => {
    request = new Request(typeof input === "string" ? `http://localhost${input}` : input, init);
    return Response.json({ kind: "hitl", payload: { questions: [] } });
  };

  try {
    await resumeReview("s1", { selected_code: "005930" });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.ok(request);
  assert.equal(request.url, "http://localhost/api/reviews");
  assert.deepEqual(await request.json(), {
    sessionId: "s1",
    value: { selected_code: "005930" },
  });
});
