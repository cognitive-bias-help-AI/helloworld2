import assert from "node:assert/strict";
import test from "node:test";

import { applyReviewResponse, beginReview, initialReviewPageState, questionResumeValue, reviewFormControls, reviewPageText, selectedCodeResumeValue } from "./review-page-state.ts";

const result = {
  stock: { code: "005930", name: "삼성전자", market: "KOSPI" },
  judgmentSlots: [], claims: [], evidence: [], findings: [], opposingSearch: null, providerCollections: {},
  report: { schemaVersion: "s0.v1" as const, renderedSlots: [], banners: [], theoryNotes: [], citations: [], createdAt: "2026-08-24T00:00:00+00:00" },
  finalSummary: "done", banners: [], degraded: false,
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
    [
      { ask_id: "ask-1", slot_id: 4, question: "근거는?" },
      { ask_id: "ask-2", slot_id: 5, question: "기대는?" },
      { ask_id: "ask-3", slot_id: 7, question: "우려는?" },
      { ask_id: "ask-4", slot_id: 8, question: "조건은?" },
    ],
    {
      "ask-1": { responseState: "answered", answer: "  실적  " },
      "ask-2": { responseState: "unknown" },
      "ask-3": { responseState: "undecided" },
      "ask-4": { responseState: "user_declined" },
    },
  ), { answers: [
    { ask_id: "ask-1", response_state: "answered", answer: "실적" },
    { ask_id: "ask-2", response_state: "unknown" },
    { ask_id: "ask-3", response_state: "undecided" },
    { ask_id: "ask-4", response_state: "user_declined" },
  ] });
});

test("question resume rejects blank ANSWERED but accepts non-answer states without text", () => {
  const questions = [{ ask_id: "ask-1", question: "근거는?" }];
  assert.throws(() => questionResumeValue(questions, { "ask-1": { responseState: "answered", answer: "  " } }));
  assert.deepEqual(questionResumeValue(questions, { "ask-1": { responseState: "unknown" } }), {
    answers: [{ ask_id: "ask-1", response_state: "unknown" }],
  });
});

test("page state preserves a safe terminal reason category", () => {
  const terminal = applyReviewResponse(initialReviewPageState, {
    kind: "terminal",
    reasonCode: "prompt_injection",
    message: "검토를 종료했습니다.",
  });

  assert.equal(terminal.view, "error");
  assert.equal(terminal.terminalReasonCode, "prompt_injection");
  assert.equal(terminal.error, "검토를 종료했습니다.");
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
