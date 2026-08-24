import assert from "node:assert/strict";
import test from "node:test";

import { applyReviewResponse, beginReview, initialReviewPageState, questionResumeValue, reviewFormControls, reviewPageText, selectedCodeResumeValue } from "./review-page-state.ts";

const result = {
  stock: { code: "005930", name: "삼성전자" }, claims: [], evidence: [], finalSummary: "done", banners: [], degraded: false,
  judgmentContext: { decisionAction: "CONSIDER_ENTRY" as const },
};

test("page state prevents duplicate submission while loading and reaches result, degraded, and fatal states", () => {
  const loading = beginReview(initialReviewPageState);
  assert.equal(loading.view, "loading");
  assert.strictEqual(beginReview(loading), loading);

  const success = applyReviewResponse(loading, { kind: "result", result });
  assert.equal(success.view, "success");
  assert.equal(success.result?.stock.code, "005930");

  const degraded = applyReviewResponse(loading, { kind: "result", result: { ...result, degraded: true } });
  assert.equal(degraded.result?.degraded, true);

  const failure = applyReviewResponse(loading, { kind: "error", code: "REVIEW_FAILED", message: "fixed error" });
  assert.deepEqual(failure, { ...initialReviewPageState, view: "error", error: "fixed error" });
});

test("page state preserves backend HITL payload and constructs only required resume values", () => {
  const hitl = applyReviewResponse(initialReviewPageState, {
    kind: "hitl", payload: { candidates: [{ selected_code: "005930", display_name: "삼성전자" }] },
  });
  assert.equal(hitl.view, "hitl");
  assert.deepEqual(selectedCodeResumeValue("005930"), { selected_code: "005930" });
  assert.deepEqual(questionResumeValue(
    [{ ask_id: "ask-1", question: "근거는?" }],
    { "ask-1": "  실적  " },
  ), { answers: [{ ask_id: "ask-1", answer: "실적" }] });
});

test("page contract exposes the structured required controls and canonical option values", () => {
  assert.equal(reviewPageText.title, "투자 판단 점검");
  assert.equal(reviewPageText.loading, "판단 근거를 점검하고 있습니다...");
  assert.equal(reviewFormControls.stockInput.required, true);
  assert.equal(reviewFormControls.primaryReasons.required, true);
  assert.deepEqual(Object.keys(reviewFormControls.decisionAction), ["CONSIDER_ENTRY", "HOLD", "CONSIDER_EXIT", "WAIT"]);
  assert.deepEqual(Object.keys(reviewFormControls.holdingState), ["HOLDING", "NOT_HOLDING"]);
  assert.deepEqual(Object.keys(reviewFormControls.timeHorizon), ["SHORT", "MEDIUM", "LONG", "UNDECIDED"]);
});
