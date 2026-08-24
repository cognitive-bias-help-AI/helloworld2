import type { DecisionAction, HoldingState, ResponseState, ReviewIntake, TimeHorizon } from "./intake.ts";

export type WorkerMessage =
  | { kind: "start"; intake: ReviewIntake }
  | { kind: "resume"; value: unknown };
export type { ReviewIntake } from "./intake.ts";

export type HitlPayload =
  | { candidates: Array<{ selected_code: string; display_name: string; market?: string }> }
  | { schema_version?: string; questions: Array<{ ask_id: string; slot_id?: number; question: string }> };

export type SourceTrace = "survey" | "chat_explicit" | "user_confirmed" | "llm_extraction" | "system_inference" | "market_data" | "unknown";
export type { ResponseState } from "./intake.ts";
export type ClaimVerdict = "support" | "partial_support" | "unsupported" | "contradicted" | "unverifiable";
export type EvidenceStance = "support" | "oppose" | "neutral" | "unknown";
export type Citation = { evidenceId: string; span: string };

export type NumericCheck = {
  metric: string; claimed: string; observed: number | null; unit: string | null; period: string | null;
  result: "consistent" | "inconsistent" | "not_comparable" | "no_data";
  evidenceId: string; computedBy: "rule";
};

export type ClaimEvaluationView = {
  claimEvaluationId: string; claimId: string; verdict: ClaimVerdict;
  supportEvidenceIds: string[]; opposeEvidenceIds: string[]; neutralEvidenceIds: string[]; unknownEvidenceIds: string[];
  citations: Citation[]; numericChecks: NumericCheck[]; missingDimensions: number[]; uncertaintyCodes: string[]; createdAt: string;
};

export type ClaimView = {
  claimId: string; slotId: number; proposition: string; verifiable: boolean; origin: SourceTrace;
  supersededBy: string | null; evaluation: ClaimEvaluationView | null;
};

export type JudgmentSlotView = {
  slotId: number; status: "RESOLVED" | "ABSENT" | "CONFLICT" | "AMBIGUOUS"; responseState: ResponseState;
  observationIds: string[]; values: string[]; issueIds: string[]; sources: SourceTrace[];
};

export type EvidenceView = {
  evidenceId: string; sourceType: "dart" | "news" | "quote"; sourceRef: string; publisher: string | null;
  publishedAt: string | null; sourceUrl: string | null; rawSpan: string;
  spanScope: "headline_snippet" | "full_text" | "structured_field";
  relatedQueryIds: string[]; relatedClaimIds: string[]; roles: Array<"PRIMARY" | "CORROBORATIVE">;
  stances: Array<{ claimId: string; stance: EvidenceStance; stanceSource: "llm" | "rule"; queryId: string | null }>;
  source: string; excerpt: string; url: string | null;
};

export type FindingView = {
  findingId: string; slotId: number; kind: "mismatch" | "missing" | "unverified" | "conflict";
  citations: Citation[]; claimEvaluationId: string | null; createdAt: string;
};

export type OpposingSearchView = {
  status: "verified" | "unverified"; count: number | null; queries: string[] | null; reason: string | null;
};

export type ProviderCollectionView = {
  source: "dart" | "news" | "quote"; status: "OK" | "PARTIAL" | "MISSING"; reasonCode: string | null;
  itemsFetched: number; itemsAdopted: number; itemsDeduped: number; queriesRun: number;
};

export type ReportView = {
  schemaVersion: "s0.v1";
  renderedSlots: Array<{ slotNo: number; text: string; citations: Citation[] }>;
  banners: string[];
  theoryNotes: Array<{
    theory_id: string; trigger: [number, "absent" | "partial"]; name: string; definition: string;
    observable_pattern: string; non_diagnostic_warning: string; source_refs: string[];
  }>;
  citations: Array<Citation & { sourceUrl: string | null; publisher: string | null }>;
  createdAt: string;
};

export type ReviewResult = {
  stock: { code: string | null; name: string | null; market: string | null };
  judgmentSlots: JudgmentSlotView[]; claims: ClaimView[]; evidence: EvidenceView[]; findings: FindingView[];
  opposingSearch: OpposingSearchView | null; providerCollections: Record<string, ProviderCollectionView>; report: ReportView;
  finalSummary: string; banners: string[]; degraded: boolean;
  judgmentContext: { decisionAction?: DecisionAction; holdingState?: HoldingState; timeHorizon?: TimeHorizon; primaryReasons?: string; expectedOutcome?: string };
};

export type ReviewResponse =
  | { kind: "hitl"; payload: HitlPayload }
  | { kind: "result"; result: ReviewResult }
  | { kind: "terminal"; reasonCode: string; message: string }
  | { kind: "error"; code: string; message: string };
