import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "node:path";

import type { ReviewResponse, WorkerMessage } from "./types.ts";

type ChildLike = {
  stdin: Pick<NodeJS.WritableStream, "write">;
  stdout: Pick<NodeJS.ReadableStream, "setEncoding" | "on">;
  stderr: Pick<NodeJS.ReadableStream, "resume">;
  kill(): boolean;
  on(event: "exit" | "error", listener: () => void): unknown;
};

const sessions = new Map<string, ReviewWorkerSession>();
const REQUEST_TIMEOUT_MS = 120_000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isWorkerResponse(value: unknown): value is ReviewResponse {
  if (!isRecord(value) || typeof value.kind !== "string") return false;
  if (value.kind === "error") {
    return typeof value.code === "string" && typeof value.message === "string";
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
      isRecord(item) && typeof item.ask_id === "string" && typeof item.question === "string");
  }
  if (value.kind !== "result" || !isRecord(value.result)) return false;
  const result = value.result;
  return (
    isRecord(result.stock) && isNullableString(result.stock.code) && isNullableString(result.stock.name) &&
    Array.isArray(result.claims) && result.claims.every((item) =>
      isRecord(item) && typeof item.text === "string" && typeof item.summary === "string" &&
      ["verified", "partial", "unverified"].includes(String(item.status))) &&
    Array.isArray(result.evidence) && result.evidence.every((item) =>
      isRecord(item) && typeof item.source === "string" && typeof item.excerpt === "string" &&
      isNullableString(item.url) && isNullableString(item.publishedAt)) &&
    typeof result.finalSummary === "string" &&
    Array.isArray(result.banners) && result.banners.every((item) => typeof item === "string") &&
    typeof result.degraded === "boolean"
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
    child.on("exit", () => this.finish(new Error("worker exited")));
    child.on("error", () => this.finish(new Error("worker failed")));
    child.stderr.resume();
  }

  send(message: WorkerMessage): Promise<ReviewResponse> {
    if (this.terminal) return Promise.reject(new Error("session is terminal"));
    if (this.inFlight) return Promise.reject(new Error("request already in flight"));

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.finish(new Error("worker timeout")), this.timeoutMs);
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
      pending.resolve(response);
      if (response.kind === "result" || response.kind === "error") this.finish();
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
  const session = new ReviewWorkerSession(id, child, () => sessions.delete(id));
  sessions.set(id, session);
  return session;
}

export async function startReview(text: string) {
  const session = spawnSession();
  const response = await session.send({ kind: "start", text });
  return { sessionId: session.id, ...response };
}

export async function resumeReview(sessionId: string, value: unknown) {
  const session = sessions.get(sessionId);
  if (!session) throw new Error("review session not found");
  return session.send({ kind: "resume", value });
}
