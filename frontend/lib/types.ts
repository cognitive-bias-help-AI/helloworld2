export type WorkerMessage =
  | { kind: "start"; text: string }
  | { kind: "resume"; value: unknown };

export type HitlPayload =
  | { candidates: Array<{ selected_code: string; display_name: string; market?: string }> }
  | { schema_version?: string; questions: Array<{ ask_id: string; question: string }> };

export type ReviewResult = {
  stock: { code: string | null; name: string | null };
  claims: Array<{ text: string; status: "verified" | "partial" | "unverified"; summary: string }>;
  evidence: Array<{ source: string; excerpt: string; url: string | null; publishedAt: string | null }>;
  finalSummary: string;
  banners: string[];
  degraded: boolean;
};

export type ReviewResponse =
  | { kind: "hitl"; payload: HitlPayload }
  | { kind: "result"; result: ReviewResult }
  | { kind: "error"; code: string; message: string };
