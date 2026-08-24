import type { HitlPayload, ReviewResponse, ReviewResult } from "./types.ts";
import type { DecisionAction, HoldingState, TimeHorizon } from "./intake.ts";
import type { ResponseState } from "./intake.ts";

export const reviewPageText = {
  title: "투자 판단 점검",
  loading: "판단 근거를 점검하고 있습니다...",
} as const;

export const reviewFormControls = {
  stockInput: { required: true },
  primaryReasons: { required: true },
  decisionAction: {
    CONSIDER_ENTRY: "신규 매수를 고려하고 있어요",
    HOLD: "현재 보유를 유지하고 있어요",
    CONSIDER_EXIT: "매도를 고려하고 있어요",
    WAIT: "아직 관망하고 있어요",
  } satisfies Record<DecisionAction, string>,
  holdingState: {
    HOLDING: "보유하고 있어요",
    NOT_HOLDING: "보유하지 않고 있어요",
  } satisfies Record<HoldingState, string>,
  timeHorizon: {
    SHORT: "단기",
    MEDIUM: "중기",
    LONG: "중장기",
    UNDECIDED: "아직 정하지 않았어요",
  } satisfies Record<TimeHorizon, string>,
} as const;

export type ReviewPageState = {
  view: "idle" | "loading" | "hitl" | "success" | "error";
  sessionId: string;
  hitl: HitlPayload | null;
  result: ReviewResult | null;
  error: string;
  terminalReasonCode: string;
};

export const initialReviewPageState: ReviewPageState = {
  view: "idle",
  sessionId: "",
  hitl: null,
  result: null,
  error: "",
  terminalReasonCode: "",
};

export function beginReview(state: ReviewPageState): ReviewPageState {
  return state.view === "loading" ? state : { ...state, view: "loading", error: "", terminalReasonCode: "" };
}

export function applyReviewResponse(
  state: ReviewPageState,
  response: ReviewResponse & { sessionId?: string },
): ReviewPageState {
  const sessionId = response.sessionId || state.sessionId;
  if (response.kind === "hitl") return { ...state, sessionId, hitl: response.payload, view: "hitl" };
  if (response.kind === "result") return { ...state, sessionId, result: response.result, view: "success" };
  if (response.kind === "terminal") {
    return { ...state, sessionId, terminalReasonCode: response.reasonCode, error: response.message, view: "error" };
  }
  return { ...state, sessionId, error: response.message, view: "error" };
}

export function selectedCodeResumeValue(selectedCode: string) {
  return { selected_code: selectedCode };
}

export function questionResumeValue(
  questions: Array<{ ask_id: string; slot_id?: number; question?: string }>,
  answers: Record<string, { responseState: ResponseState; answer?: string }>,
) {
  return {
    answers: questions.map((question) => {
      const response = answers[question.ask_id];
      if (!response) throw new Error("every question requires a response state");
      if (response.responseState === "answered") {
        const answer = response.answer?.trim();
        if (!answer) throw new Error("ANSWERED requires a nonblank answer");
        return { ask_id: question.ask_id, response_state: response.responseState, answer };
      }
      return { ask_id: question.ask_id, response_state: response.responseState };
    }),
  };
}
