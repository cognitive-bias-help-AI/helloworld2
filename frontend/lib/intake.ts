export const DECISION_ACTIONS = ["CONSIDER_ENTRY", "HOLD", "CONSIDER_EXIT", "WAIT"] as const;
export const HOLDING_STATES = ["HOLDING", "NOT_HOLDING"] as const;
export const TIME_HORIZONS = ["SHORT", "MEDIUM", "LONG", "UNDECIDED"] as const;
export const INFORMATION_CATEGORIES = [
  "FINANCIALS",
  "DISCLOSURE",
  "NEWS",
  "PRICE_CHART",
  "INDUSTRY",
  "OTHER",
  "NONE_CHECKED",
] as const;

export type DecisionAction = (typeof DECISION_ACTIONS)[number];
export type HoldingState = (typeof HOLDING_STATES)[number];
export type TimeHorizon = (typeof TIME_HORIZONS)[number];
export type InformationCategory = (typeof INFORMATION_CATEGORIES)[number];

export type ReviewIntake = {
  stockInput: string;
  decisionAction: DecisionAction;
  holdingState: HoldingState;
  timeHorizon: TimeHorizon;
  primaryReasons: string;
  expectedOutcome?: string;
  informationChecked?: InformationCategory[];
  counterEvidenceConcerns?: string;
  changeConditions?: string;
};

const REQUIRED_FIELDS = ["stockInput", "decisionAction", "holdingState", "timeHorizon", "primaryReasons"] as const;
const OPTIONAL_FIELDS = ["expectedOutcome", "informationChecked", "counterEvidenceConcerns", "changeConditions"] as const;
const ALL_FIELDS = new Set<string>([...REQUIRED_FIELDS, ...OPTIONAL_FIELDS]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nonBlank(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${field} is required`);
  return value.trim();
}

function enumValue<T extends string>(value: unknown, values: readonly T[], field: string): T {
  if (typeof value !== "string" || !values.includes(value as T)) throw new Error(`${field} is invalid`);
  return value as T;
}

function optionalText(value: unknown, field: string): string | undefined {
  if (value === undefined) return undefined;
  if (value === null) throw new Error(`${field} must be omitted instead of null`);
  if (typeof value !== "string") throw new Error(`${field} must be text`);
  return value.trim() || undefined;
}

function optionalInformation(value: unknown): InformationCategory[] | undefined {
  if (value === undefined) return undefined;
  if (value === null) throw new Error("informationChecked must be omitted instead of null");
  if (!Array.isArray(value)) throw new Error("informationChecked must be a list");
  if (!value.length) return undefined;
  const categories = value.map((item) => enumValue(item, INFORMATION_CATEGORIES, "informationChecked"));
  if (new Set(categories).size !== categories.length) throw new Error("informationChecked must not repeat values");
  if (categories.includes("NONE_CHECKED") && categories.length !== 1) {
    throw new Error("NONE_CHECKED must be selected alone");
  }
  return categories;
}

export function parseReviewIntake(value: unknown): ReviewIntake {
  if (!isRecord(value)) throw new Error("intake must be an object");
  for (const key of Object.keys(value)) {
    if (!ALL_FIELDS.has(key)) throw new Error(`unexpected intake field: ${key}`);
  }

  const intake: ReviewIntake = {
    stockInput: nonBlank(value.stockInput, "stockInput"),
    decisionAction: enumValue(value.decisionAction, DECISION_ACTIONS, "decisionAction"),
    holdingState: enumValue(value.holdingState, HOLDING_STATES, "holdingState"),
    timeHorizon: enumValue(value.timeHorizon, TIME_HORIZONS, "timeHorizon"),
    primaryReasons: nonBlank(value.primaryReasons, "primaryReasons"),
  };
  const expectedOutcome = optionalText(value.expectedOutcome, "expectedOutcome");
  const informationChecked = optionalInformation(value.informationChecked);
  const counterEvidenceConcerns = optionalText(value.counterEvidenceConcerns, "counterEvidenceConcerns");
  const changeConditions = optionalText(value.changeConditions, "changeConditions");
  if (expectedOutcome) intake.expectedOutcome = expectedOutcome;
  if (informationChecked) intake.informationChecked = informationChecked;
  if (counterEvidenceConcerns) intake.counterEvidenceConcerns = counterEvidenceConcerns;
  if (changeConditions) intake.changeConditions = changeConditions;
  return intake;
}

export function parseStartReviewBody(value: unknown): ReviewIntake {
  if (!isRecord(value) || Object.keys(value).length !== 1 || !("intake" in value)) {
    throw new Error("request body must contain intake only");
  }
  return parseReviewIntake(value.intake);
}

export function toggleInformationChecked(
  selected: InformationCategory[],
  category: InformationCategory,
): InformationCategory[] {
  if (category === "NONE_CHECKED") return selected.includes(category) ? [] : [category];
  const withoutNone = selected.filter((item) => item !== "NONE_CHECKED");
  return withoutNone.includes(category)
    ? withoutNone.filter((item) => item !== category)
    : [...withoutNone, category];
}
