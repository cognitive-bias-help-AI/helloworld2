import assert from "node:assert/strict";
import test from "node:test";

import { parseReviewIntake, parseStartReviewBody, toggleInformationChecked } from "./intake.ts";

const validIntake = {
  stockInput: "삼성전자",
  decisionAction: "CONSIDER_ENTRY",
  holdingState: "NOT_HOLDING",
  timeHorizon: "LONG",
  primaryReasons: "AI 수요와 실적 개선을 기대합니다.",
};

test("parseReviewIntake accepts canonical required fields and omits blank optionals", () => {
  const intake = parseReviewIntake({
    ...validIntake,
    expectedOutcome: "  ",
    informationChecked: [],
    counterEvidenceConcerns: "",
    changeConditions: "영업이익 증가세가 꺾이면 재검토합니다.",
  });

  assert.deepEqual(intake, {
    ...validIntake,
    changeConditions: "영업이익 증가세가 꺾이면 재검토합니다.",
  });
  assert.equal("expectedOutcome" in intake, false);
  assert.equal("informationChecked" in intake, false);
  assert.equal("counterEvidenceConcerns" in intake, false);
});

test("parseReviewIntake rejects missing, blank, noncanonical, duplicated, and extra fields", () => {
  for (const payload of [
    { ...validIntake, stockInput: "  " },
    { ...validIntake, decisionAction: "BUY_NOW" },
    { ...validIntake, informationChecked: ["NEWS", "NEWS"] },
    { ...validIntake, unexpected: true },
    { ...validIntake, expectedOutcome: null },
    Object.fromEntries(Object.entries(validIntake).filter(([key]) => key !== "primaryReasons")),
  ]) {
    assert.throws(() => parseReviewIntake(payload));
  }
});

test("NONE_CHECKED selection is mutually exclusive in the intake control", () => {
  assert.deepEqual(toggleInformationChecked(["FINANCIALS", "NEWS"], "NONE_CHECKED"), ["NONE_CHECKED"]);
  assert.deepEqual(toggleInformationChecked(["NONE_CHECKED"], "DISCLOSURE"), ["DISCLOSURE"]);
  assert.deepEqual(toggleInformationChecked(["NEWS"], "NEWS"), []);
});

test("parseStartReviewBody accepts only an intake envelope", () => {
  assert.deepEqual(parseStartReviewBody({ intake: validIntake }), validIntake);
  assert.throws(() => parseStartReviewBody({ intake: validIntake, text: "ignored" }));
});
