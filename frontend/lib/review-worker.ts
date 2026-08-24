import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "node:path";

import { DECISION_ACTIONS, HOLDING_STATES, TIME_HORIZONS } from "./intake.ts";
import type { ReviewIntake, ReviewResponse, WorkerMessage } from "./types.ts";

type ChildLike = {
  stdin: Pick<NodeJS.WritableStream, "write">;
  stdout: Pick<NodeJS.ReadableStream, "setEncoding" | "on">;
  stderr: Pick<NodeJS.ReadableStream, "resume" | "setEncoding" | "on">;
  kill(): boolean;
  on(event: "exit" | "error", listener: () => void): unknown;
};

const sessions = new Map<string, ReviewWorkerSession>();
const REQUEST_TIMEOUT_MS = 120_000;
const DEBUG_LOGS = ["1", "true", "yes", "on"].includes((process.env.REVIEW_DEBUG_LOGS ?? "").toLowerCase());

function workerLog(event: string, session: string, detail?: string) {
  if (DEBUG_LOGS) console.error(`[worker] ${event} session=${session}${detail ? ` ${detail}` : ""}`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isNumberArray(value: unknown): value is number[] {
  return Array.isArray(value) && value.every((item) => Number.isInteger(item));
}

function isCitation(value: unknown): boolean {
  return isRecord(value) && typeof value.evidenceId === "string" && typeof value.span === "string";
}

function isNumericCheck(value: unknown): boolean {
  return isRecord(value) && typeof value.metric === "string" && typeof value.claimed === "string" &&
    (value.observed === null || typeof value.observed === "number") && isNullableString(value.unit) &&
    isNullableString(value.period) && ["consistent", "inconsistent", "not_comparable", "no_data"].includes(String(value.result)) &&
    typeof value.evidenceId === "string" && value.computedBy === "rule";
}

const VERDICTS = ["support", "partial_support", "unsupported", "contradicted", "unverifiable"];
const SOURCES = ["survey", "chat_explicit", "user_confirmed", "llm_extraction", "system_inference", "market_data", "unknown"];

function isEvaluation(value: unknown): boolean {
  return isRecord(value) && typeof value.claimEvaluationId === "string" && typeof value.claimId === "string" &&
    VERDICTS.includes(String(value.verdict)) && isStringArray(value.supportEvidenceIds) &&
    isStringArray(value.opposeEvidenceIds) && isStringArray(value.neutralEvidenceIds) &&
    isStringArray(value.unknownEvidenceIds) && Array.isArray(value.citations) && value.citations.every(isCitation) &&
    Array.isArray(value.numericChecks) && value.numericChecks.every(isNumericCheck) &&
    isNumberArray(value.missingDimensions) && isStringArray(value.uncertaintyCodes) && typeof value.createdAt === "string";
}

function isClaim(value: unknown): boolean {
  return isRecord(value) && typeof value.claimId === "string" && Number.isInteger(value.slotId) &&
    typeof value.proposition === "string" && typeof value.verifiable === "boolean" && SOURCES.includes(String(value.origin)) &&
    isNullableString(value.supersededBy) && (value.evaluation === null || isEvaluation(value.evaluation));
}

function isJudgmentSlot(value: unknown): boolean {
  return isRecord(value) && Number.isInteger(value.slotId) && ["RESOLVED", "ABSENT", "CONFLICT", "AMBIGUOUS"].includes(String(value.status)) &&
    ["answered", "unknown", "undecided", "user_declined"].includes(String(value.responseState)) &&
    isStringArray(value.observationIds) && isStringArray(value.values) && isStringArray(value.issueIds) &&
    isStringArray(value.sources) && value.sources.every((item) => SOURCES.includes(item));
}

function isEvidence(value: unknown): boolean {
  if (!isRecord(value) || typeof value.evidenceId !== "string" || !["dart", "news", "quote"].includes(String(value.sourceType)) ||
      typeof value.sourceRef !== "string" || !isNullableString(value.publisher) || !isNullableString(value.publishedAt) ||
      !isNullableString(value.sourceUrl) || typeof value.rawSpan !== "string" ||
      !["headline_snippet", "full_text", "structured_field"].includes(String(value.spanScope)) ||
      !isStringArray(value.relatedQueryIds) || !isStringArray(value.relatedClaimIds) || !isStringArray(value.roles) ||
      !value.roles.every((item) => ["PRIMARY", "CORROBORATIVE"].includes(item)) || !Array.isArray(value.stances) ||
      typeof value.source !== "string" || typeof value.excerpt !== "string" || !isNullableString(value.url)) return false;
  return value.stances.every((item) => isRecord(item) && typeof item.claimId === "string" &&
    ["support", "oppose", "neutral", "unknown"].includes(String(item.stance)) &&
    ["llm", "rule"].includes(String(item.stanceSource)) && isNullableString(item.queryId));
}

function isFinding(value: unknown): boolean {
  return isRecord(value) && typeof value.findingId === "string" && Number.isInteger(value.slotId) &&
    ["mismatch", "missing", "unverified", "conflict"].includes(String(value.kind)) &&
    Array.isArray(value.citations) && value.citations.every(isCitation) && isNullableString(value.claimEvaluationId) &&
    typeof value.createdAt === "string";
}

function isOpposingSearch(value: unknown): boolean {
  return isRecord(value) && ["verified", "unverified"].includes(String(value.status)) &&
    (value.count === null || Number.isInteger(value.count)) && (value.queries === null || isStringArray(value.queries)) &&
    isNullableString(value.reason);
}

function isProviderCollection(value: unknown): boolean {
  return isRecord(value) && ["dart", "news", "quote"].includes(String(value.source)) &&
    ["OK", "PARTIAL", "MISSING"].includes(String(value.status)) && isNullableString(value.reasonCode) &&
    [value.itemsFetched, value.itemsAdopted, value.itemsDeduped, value.queriesRun].every((item) => Number.isInteger(item));
}

function isReport(value: unknown): boolean {
  if (!isRecord(value) || value.schemaVersion !== "s0.v1" || !Array.isArray(value.renderedSlots) ||
      !isStringArray(value.banners) || !Array.isArray(value.theoryNotes) || !Array.isArray(value.citations) ||
      typeof value.createdAt !== "string") return false;
  const slots = value.renderedSlots.every((item) => isRecord(item) && Number.isInteger(item.slotNo) &&
    typeof item.text === "string" && Array.isArray(item.citations) && item.citations.every(isCitation));
  const citations = value.citations.every((item) => isCitation(item) && isRecord(item) &&
    isNullableString(item.sourceUrl) && isNullableString(item.publisher));
  const notes = value.theoryNotes.every((item) => isRecord(item) && typeof item.theory_id === "string" &&
    Array.isArray(item.trigger) && item.trigger.length === 2 && Number.isInteger(item.trigger[0]) &&
    ["absent", "partial"].includes(String(item.trigger[1])) && typeof item.name === "string" &&
    typeof item.definition === "string" && typeof item.observable_pattern === "string" &&
    typeof item.non_diagnostic_warning === "string" && isStringArray(item.source_refs));
  return slots && citations && notes;
}

function isJudgmentContext(value: unknown): boolean {
  if (!isRecord(value)) return false;
  const allowed = new Set(["decisionAction", "holdingState", "timeHorizon", "primaryReasons", "expectedOutcome"]);
  if (Object.keys(value).some((key) => !allowed.has(key))) return false;
  return (
    (value.decisionAction === undefined || DECISION_ACTIONS.includes(value.decisionAction as typeof DECISION_ACTIONS[number])) &&
    (value.holdingState === undefined || HOLDING_STATES.includes(value.holdingState as typeof HOLDING_STATES[number])) &&
    (value.timeHorizon === undefined || TIME_HORIZONS.includes(value.timeHorizon as typeof TIME_HORIZONS[number])) &&
    (value.primaryReasons === undefined || typeof value.primaryReasons === "string") &&
    (value.expectedOutcome === undefined || typeof value.expectedOutcome === "string")
  );
}

function isWorkerResponse(value: unknown): value is ReviewResponse {
  if (!isRecord(value) || typeof value.kind !== "string") return false;
  if (value.kind === "error") {
    return typeof value.code === "string" && typeof value.message === "string";
  }
  if (value.kind === "terminal") {
    return typeof value.reasonCode === "string" && typeof value.message === "string";
  }
  if (value.kind === "hitl") {
    if (!isRecord(value.payload)) return false;
    const { candidates, questions } = value.payload;
    if ((candidates === undefined) === (questions === undefined)) return false;
    if (candidates !== undefined) return Array.isArray(candidates) && candidates.every((item) =>
      isRecord(item) && typeof item.selected_code === "string" &&
      typeof item.display_name === "string" &&
      (item.market === undefined || typeof item.market === "string"));
    return Array.isArray(questions) && questions.every((item) =>
      isRecord(item) && typeof item.ask_id === "string" && typeof item.question === "string" &&
      (item.slot_id === undefined || Number.isInteger(item.slot_id)));
  }
  if (value.kind !== "result" || !isRecord(value.result)) return false;
  const result = value.result;
  return (
    isRecord(result.stock) && isNullableString(result.stock.code) && isNullableString(result.stock.name) && isNullableString(result.stock.market) &&
    Array.isArray(result.judgmentSlots) && result.judgmentSlots.length === 8 && result.judgmentSlots.every(isJudgmentSlot) &&
    Array.isArray(result.claims) && result.claims.every(isClaim) &&
    Array.isArray(result.evidence) && result.evidence.every(isEvidence) &&
    Array.isArray(result.findings) && result.findings.every(isFinding) &&
    (result.opposingSearch === null || isOpposingSearch(result.opposingSearch)) &&
    isRecord(result.providerCollections) && Object.values(result.providerCollections).every(isProviderCollection) &&
    isReport(result.report) &&
    typeof result.finalSummary === "string" &&
    Array.isArray(result.banners) && result.banners.every((item) => typeof item === "string") &&
    typeof result.degraded === "boolean" &&
    isJudgmentContext(result.judgmentContext)
  );
}

export class ReviewWorkerSession {
  readonly id: string;
  private readonly child: ChildLike;
  private readonly remove: () => void;
  private readonly timeoutMs: number;
  private buffer = "";
  private inFlight?: {
    resolve: (value: ReviewResponse) => void;
    reject: (error: Error) => void;
    timer: NodeJS.Timeout;
  };
  private terminal = false;

  constructor(
    id: string,
    child: ChildLike,
    remove: () => void,
    timeoutMs = REQUEST_TIMEOUT_MS,
  ) {
    this.id = id;
    this.child = child;
    this.remove = remove;
    this.timeoutMs = timeoutMs;
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => this.onData(chunk));
    child.on("exit", () => { workerLog("EXIT", this.id); this.finish(new Error("worker exited")); });
    child.on("error", () => { workerLog("PROCESS_ERROR", this.id); this.finish(new Error("worker failed")); });
    if (DEBUG_LOGS) {
      child.stderr.setEncoding("utf8");
      child.stderr.on("data", (chunk: string) => process.stderr.write(chunk));
    } else child.stderr.resume();
  }

  send(message: WorkerMessage): Promise<ReviewResponse> {
    if (this.terminal) return Promise.reject(new Error("session is terminal"));
    if (this.inFlight) return Promise.reject(new Error("request already in flight"));

    workerLog("SEND", this.id, `kind=${message.kind}`);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        workerLog("TIMEOUT", this.id);
        this.finish(new Error("worker timeout"));
      }, this.timeoutMs);
      this.inFlight = { resolve, reject, timer };
      this.child.stdin.write(`${JSON.stringify(message)}\n`, (error) => {
        if (error) this.finish(new Error("worker write failed"));
      });
    });
  }

  private onData(chunk: string) {
    this.buffer += chunk;
    for (;;) {
      const newline = this.buffer.indexOf("\n");
      if (newline < 0) return;
      const line = this.buffer.slice(0, newline);
      this.buffer = this.buffer.slice(newline + 1);
      if (!line.trim()) continue;
      let response: ReviewResponse;
      try {
        const parsed: unknown = JSON.parse(line);
        if (!isWorkerResponse(parsed)) {
          throw new Error("invalid worker response");
        }
        response = parsed;
      } catch {
        workerLog("PROTOCOL_ERROR", this.id);
        this.finish(new Error("invalid worker protocol"));
        return;
      }
      const pending = this.inFlight;
      if (!pending) {
        this.finish(new Error("unexpected worker response"));
        return;
      }
      clearTimeout(pending.timer);
      this.inFlight = undefined;
      workerLog("RECV", this.id, `kind=${response.kind}`);
      pending.resolve(response);
      if (response.kind === "result" || response.kind === "terminal" || response.kind === "error") this.finish();
    }
  }

  private finish(error?: Error) {
    if (this.terminal) return;
    this.terminal = true;
    if (this.inFlight) {
      clearTimeout(this.inFlight.timer);
      if (error) this.inFlight.reject(error);
      this.inFlight = undefined;
    }
    this.child.kill();
    this.remove();
  }
}

function spawnSession(): ReviewWorkerSession {
  const id = randomUUID();
  const repositoryRoot = path.resolve(process.cwd(), "..");
  const child = spawn("uv", ["run", "python", "-m", "app.ui_bridge"], {
    cwd: repositoryRoot,
    stdio: "pipe",
    windowsHide: true,
    env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
  });
  workerLog("SPAWN", id);
  const session = new ReviewWorkerSession(id, child, () => sessions.delete(id));
  sessions.set(id, session);
  return session;
}

export async function startReview(intake: ReviewIntake) {
  const session = spawnSession();
  const response = await session.send({ kind: "start", intake });
  return { sessionId: session.id, ...response };
}

export async function resumeReview(sessionId: string, value: unknown) {
  const session = sessions.get(sessionId);
  if (DEBUG_LOGS) console.error(`[session-registry] GET session=${sessionId} found=${Boolean(session)}`);
  if (!session) throw new Error("review session not found");
  return session.send({ kind: "resume", value });
}
