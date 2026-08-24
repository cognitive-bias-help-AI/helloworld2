export const INTAKE_MODES = ["SURVEY_FIRST", "CHAT_FIRST", "HYBRID"] as const;
export const RESPONSE_STATES = ["answered", "unknown", "undecided", "user_declined"] as const;
export const DECISION_ACTIONS = ["CONSIDER_ENTRY", "HOLD", "CONSIDER_EXIT", "WAIT"] as const;
export const HOLDING_STATES = ["HOLDING", "NOT_HOLDING"] as const;
export const TIME_HORIZONS = ["SHORT", "MEDIUM", "LONG", "UNDECIDED"] as const;
export const INFORMATION_CATEGORIES = [
  "FINANCIALS", "DISCLOSURE", "NEWS", "PRICE_CHART", "INDUSTRY", "OTHER", "NONE_CHECKED",
] as const;

export type IntakeMode = (typeof INTAKE_MODES)[number];
export type ResponseState = (typeof RESPONSE_STATES)[number];
export type DecisionAction = (typeof DECISION_ACTIONS)[number];
export type HoldingState = (typeof HOLDING_STATES)[number];
export type TimeHorizon = (typeof TIME_HORIZONS)[number];
export type InformationCategory = (typeof INFORMATION_CATEGORIES)[number];

export type IntakeTarget = { selectedCode: string; market?: "KOSPI" | "KOSDAQ" } | { name: string };
export type StructuredResponse =
  | { slotId: number; responseState: "answered"; value: string | string[] }
  | { slotId: number; responseState: Exclude<ResponseState, "answered"> };

type IntakeBase = { target?: IntakeTarget };
export type SurveyFirstIntake = IntakeBase & { mode: "SURVEY_FIRST"; structured?: StructuredResponse[] };
export type ChatFirstIntake = IntakeBase & { mode: "CHAT_FIRST"; freeText: string[] };
export type HybridReviewIntake = IntakeBase & { mode: "HYBRID"; structured?: StructuredResponse[]; freeText?: string[] };
export type ReviewIntake = SurveyFirstIntake | ChatFirstIntake | HybridReviewIntake;

const KRX_CODE = /^[0-9]{4}[0-9A-Z]{2}$/;
const TOP_LEVEL = new Set(["mode", "target", "structured", "freeText"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function enumValue<T extends string>(value: unknown, values: readonly T[], field: string): T {
  if (typeof value !== "string" || !values.includes(value as T)) throw new Error(`${field} is invalid`);
  return value as T;
}

function nonBlank(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${field} is required`);
  return value.trim();
}

function parseTarget(value: unknown): IntakeTarget {
  if (!isRecord(value)) throw new Error("target must be an object");
  const keys = Object.keys(value);
  if ("name" in value) {
    if (keys.length !== 1) throw new Error("name target contains undeclared fields");
    return { name: nonBlank(value.name, "target.name") };
  }
  if (!keys.every((key) => ["selectedCode", "market"].includes(key)) || !("selectedCode" in value)) {
    throw new Error("selected target is invalid");
  }
  const selectedCode = nonBlank(value.selectedCode, "target.selectedCode");
  if (!KRX_CODE.test(selectedCode)) throw new Error("target.selectedCode is invalid");
  const market = value.market === undefined ? undefined : enumValue(value.market, ["KOSPI", "KOSDAQ"] as const, "target.market");
  return market ? { selectedCode, market } : { selectedCode };
}

function validateAnsweredValue(slotId: number, value: unknown): string | string[] {
  if (slotId === 6) {
    if (!Array.isArray(value) || !value.length) throw new Error("slot 6 answer must be a list");
    const categories = value.map((item) => enumValue(item, INFORMATION_CATEGORIES, "slot 6 value"));
    if (new Set(categories).size !== categories.length) throw new Error("slot 6 values must not repeat");
    if (categories.includes("NONE_CHECKED") && categories.length !== 1) throw new Error("NONE_CHECKED must be selected alone");
    return categories;
  }
  const text = nonBlank(value, `slot ${slotId} value`);
  if (slotId === 1) return enumValue(text, DECISION_ACTIONS, "slot 1 value");
  if (slotId === 2) return enumValue(text, HOLDING_STATES, "slot 2 value");
  if (slotId === 3) return enumValue(text, TIME_HORIZONS, "slot 3 value");
  return text;
}

function parseStructured(value: unknown): StructuredResponse[] {
  if (!Array.isArray(value)) throw new Error("structured must be a list");
  const responses = value.map((item): StructuredResponse => {
    if (!isRecord(item) || Object.keys(item).some((key) => !["slotId", "responseState", "value"].includes(key))) {
      throw new Error("structured response is invalid");
    }
    if (!Number.isInteger(item.slotId) || Number(item.slotId) < 1 || Number(item.slotId) > 8) throw new Error("slotId is invalid");
    const slotId = Number(item.slotId);
    const responseState = enumValue(item.responseState, RESPONSE_STATES, "responseState");
    if (responseState === "answered") return { slotId, responseState, value: validateAnsweredValue(slotId, item.value) };
    if ("value" in item) throw new Error(`${responseState} must not carry value`);
    return { slotId, responseState };
  });
  if (new Set(responses.map((item) => item.slotId)).size !== responses.length) throw new Error("duplicate slotId");
  return responses;
}

function parseFreeText(value: unknown): string[] {
  if (!Array.isArray(value) || !value.length) throw new Error("freeText must contain text");
  return value.map((item) => nonBlank(item, "freeText"));
}

export function parseReviewIntake(value: unknown): ReviewIntake {
  if (!isRecord(value)) throw new Error("intake must be an object");
  if (Object.keys(value).some((key) => !TOP_LEVEL.has(key))) throw new Error("unexpected intake field");
  const mode = enumValue(value.mode, INTAKE_MODES, "mode");
  if (value.target === null || value.structured === null || value.freeText === null) throw new Error("optional fields must be omitted");
  const target = value.target === undefined ? undefined : parseTarget(value.target);
  const structured = value.structured === undefined ? undefined : parseStructured(value.structured);
  const freeText = value.freeText === undefined ? undefined : parseFreeText(value.freeText);
  if (mode === "SURVEY_FIRST" && freeText !== undefined) throw new Error("SURVEY_FIRST must not carry freeText");
  if (mode === "CHAT_FIRST" && (structured !== undefined || freeText === undefined)) throw new Error("CHAT_FIRST requires only freeText");
  if (mode === "SURVEY_FIRST") return { mode, ...(target ? { target } : {}), ...(structured === undefined ? {} : { structured }) };
  if (mode === "CHAT_FIRST") return { mode, ...(target ? { target } : {}), freeText: freeText! };
  return { mode, ...(target ? { target } : {}), ...(structured === undefined ? {} : { structured }), ...(freeText === undefined ? {} : { freeText }) };
}

export function parseStartReviewBody(value: unknown): ReviewIntake {
  if (!isRecord(value) || Object.keys(value).length !== 1 || !("intake" in value)) throw new Error("request body must contain intake only");
  return parseReviewIntake(value.intake);
}

export function targetFromStockInput(value: string): IntakeTarget | undefined {
  const input = value.trim();
  if (!input) return undefined;
  return KRX_CODE.test(input) ? { selectedCode: input } : { name: input };
}

export function toggleInformationChecked(selected: InformationCategory[], category: InformationCategory): InformationCategory[] {
  if (category === "NONE_CHECKED") return selected.includes(category) ? [] : [category];
  const withoutNone = selected.filter((item) => item !== "NONE_CHECKED");
  return withoutNone.includes(category) ? withoutNone.filter((item) => item !== category) : [...withoutNone, category];
}
