import assert from "node:assert/strict";
import test from "node:test";

import { parseReviewIntake, parseStartReviewBody, toggleInformationChecked } from "./intake.ts";

const eightSlots = [
  { slotId: 1, responseState: "answered", value: "CONSIDER_ENTRY" },
  { slotId: 2, responseState: "answered", value: "NOT_HOLDING" },
  { slotId: 3, responseState: "answered", value: "LONG" },
  { slotId: 4, responseState: "answered", value: "AI 수요와 실적 개선" },
  { slotId: 5, responseState: "unknown" },
  { slotId: 6, responseState: "answered", value: ["FINANCIALS", "NEWS"] },
  { slotId: 7, responseState: "user_declined" },
  { slotId: 8, responseState: "undecided" },
] as const;

test("all three intake modes remain distinct in the closed transport", () => {
  for (const mode of ["SURVEY_FIRST", "CHAT_FIRST", "HYBRID"] as const) {
    const value = parseReviewIntake({
      mode,
      ...(mode === "CHAT_FIRST" ? { freeText: ["삼성전자 살까 고민 중입니다."] } : { structured: eightSlots }),
    });
    assert.equal(value.mode, mode);
  }
});

test("CHAT_FIRST carries free text without synthetic structured answers", () => {
  const intake = parseReviewIntake({
    mode: "CHAT_FIRST",
    target: { name: "삼성전자" },
    freeText: ["삼성전자 살까 고민 중인데 AI 수요 때문에 실적이 좋아질 것 같아."],
  });
  assert.deepEqual(intake, {
    mode: "CHAT_FIRST",
    target: { name: "삼성전자" },
    freeText: ["삼성전자 살까 고민 중인데 AI 수요 때문에 실적이 좋아질 것 같아."],
  });
  assert.equal("structured" in intake, false);
});

test("SURVEY_FIRST accepts all eight slots, canonical response states, and omitted optionals", () => {
  const complete = parseReviewIntake({ mode: "SURVEY_FIRST", structured: eightSlots });
  assert.equal(complete.mode, "SURVEY_FIRST");
  assert.deepEqual(complete.structured, eightSlots);
  const minimal = parseReviewIntake({ mode: "SURVEY_FIRST", target: { name: "삼성전자" }, structured: eightSlots.slice(0, 4) });
  assert.equal(minimal.mode, "SURVEY_FIRST");
  assert.equal(minimal.structured?.length, 4);
});

test("non-ANSWERED survey states reject values while ANSWERED requires one", () => {
  for (const responseState of ["unknown", "undecided", "user_declined"] as const) {
    assert.deepEqual(parseReviewIntake({ mode: "SURVEY_FIRST", structured: [{ slotId: 5, responseState }] }), { mode: "SURVEY_FIRST", structured: [{ slotId: 5, responseState }] });
    assert.throws(() => parseReviewIntake({ mode: "SURVEY_FIRST", structured: [{ slotId: 5, responseState, value: "fake" }] }));
  }
  assert.throws(() => parseReviewIntake({ mode: "SURVEY_FIRST", structured: [{ slotId: 5, responseState: "answered" }] }));
});

test("HYBRID preserves structured answers and free text as separate fields", () => {
  const intake = parseReviewIntake({ mode: "HYBRID", structured: [eightSlots[0]], freeText: ["추가로 HBM 공급 부족도 우려합니다."] });
  assert.equal(intake.mode, "HYBRID");
  assert.deepEqual(intake.structured, [eightSlots[0]]);
  assert.deepEqual(intake.freeText, ["추가로 HBM 공급 부족도 우려합니다."]);
});

test("target names remain names and exact numeric or alphanumeric KRX codes are accepted", () => {
  assert.deepEqual(parseReviewIntake({ mode: "SURVEY_FIRST", target: { name: "삼성전자" } }).target, { name: "삼성전자" });
  assert.deepEqual(parseReviewIntake({ mode: "SURVEY_FIRST", target: { selectedCode: "005930" } }).target, { selectedCode: "005930" });
  assert.deepEqual(parseReviewIntake({ mode: "SURVEY_FIRST", target: { selectedCode: "0126Z0" } }).target, { selectedCode: "0126Z0" });
  assert.throws(() => parseReviewIntake({ mode: "SURVEY_FIRST", target: { selectedCode: "5930" } }));
});

test("parseStartReviewBody accepts only the canonical intake envelope", () => {
  const intake = { mode: "CHAT_FIRST", freeText: ["삼성전자 판단을 점검해줘."] };
  assert.deepEqual(parseStartReviewBody({ intake }), intake);
  assert.throws(() => parseStartReviewBody({ intake, text: "ignored" }));
});

test("intake rejects duplicates, invalid information categories, nulls, and extra fields", () => {
  for (const payload of [
    { mode: "SURVEY_FIRST", structured: [eightSlots[0], eightSlots[0]] },
    { mode: "SURVEY_FIRST", structured: [{ slotId: 6, responseState: "answered", value: ["NEWS", "NEWS"] }] },
    { mode: "SURVEY_FIRST", structured: [{ slotId: 6, responseState: "answered", value: ["NEWS", "NONE_CHECKED"] }] },
    { mode: "CHAT_FIRST", freeText: [" "] },
    { mode: "HYBRID", target: null },
    { mode: "HYBRID", unexpected: true },
  ]) assert.throws(() => parseReviewIntake(payload));
});

test("NONE_CHECKED remains mutually exclusive in the survey control", () => {
  assert.deepEqual(toggleInformationChecked(["FINANCIALS", "NEWS"], "NONE_CHECKED"), ["NONE_CHECKED"]);
  assert.deepEqual(toggleInformationChecked(["NONE_CHECKED"], "DISCLOSURE"), ["DISCLOSURE"]);
  assert.deepEqual(toggleInformationChecked(["NEWS"], "NEWS"), []);
});
