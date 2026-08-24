import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import test from "node:test";

import { ReviewWorkerSession } from "./review-worker.ts";

const start = {
  kind: "start" as const,
  intake: {
    mode: "SURVEY_FIRST" as const,
    target: { name: "삼성전자" },
    structured: [{ slotId: 4, responseState: "answered" as const, value: "검토" }],
  },
};

function fakeChild() {
  const child = new EventEmitter() as EventEmitter & {
    stdin: PassThrough;
    stdout: PassThrough;
    stderr: PassThrough;
    killed: boolean;
    kill(): boolean;
  };
  child.stdin = new PassThrough();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.killed = false;
  child.kill = () => ((child.killed = true), true);
  return child;
}

function emptyResult(code: string | null = null, name: string | null = null) {
  return {
    stock: { code, name, market: null },
    judgmentSlots: Array.from({ length: 8 }, (_, index) => ({
      slotId: index + 1, status: "ABSENT", responseState: "unknown",
      observationIds: [], values: [], issueIds: [], sources: [],
    })),
    claims: [], evidence: [], findings: [], opposingSearch: null, providerCollections: {},
    report: { schemaVersion: "s0.v1", renderedSlots: [], banners: [], theoryNotes: [], citations: [], createdAt: "2026-08-24T00:00:00+00:00" },
    finalSummary: "done", banners: [], degraded: false, judgmentContext: {},
  };
}

test("one session rejects a concurrent request", async () => {
  const child = fakeChild();
  const session = new ReviewWorkerSession("s1", child, () => undefined, 1_000);
  const first = session.send(start);

  await assert.rejects(session.send({ kind: "resume", value: {} }), /in flight/);
  child.stdout.write('{"kind":"hitl","payload":{"candidates":[]}}\n');
  await first;
});

test("HITL keeps the worker alive for multiple resumes until terminal result", async () => {
  const child = fakeChild();
  let removed = 0;
  const session = new ReviewWorkerSession("s1", child, () => removed++, 1_000);

  const first = session.send(start);
  child.stdout.write('{"kind":"hitl","payload":{"candidates":[]}}\n');
  await first;
  assert.equal(child.killed, false);
  assert.equal(removed, 0);

  const second = session.send({ kind: "resume", value: { selected_code: "005930" } });
  child.stdout.write('{"kind":"hitl","payload":{"questions":[]}}\n');
  await second;
  assert.equal(child.killed, false);
  assert.equal(removed, 0);

  const third = session.send({ kind: "resume", value: { answers: [] } });
  child.stdout.write(`${JSON.stringify({
    kind: "result",
    result: emptyResult("005930", "삼성전자"),
  })}\n`);
  await third;
  assert.equal(child.killed, true);
  assert.equal(removed, 1);
});

test("one session supports stock HITL followed by question HITL and another question turn", async () => {
  const child = fakeChild();
  const writes: string[] = [];
  child.stdin.on("data", (chunk) => writes.push(chunk.toString()));
  const session = new ReviewWorkerSession("s-multi", child, () => {});

  const start = session.send({ kind: "start", intake: { mode: "CHAT_FIRST", freeText: ["삼성전자 살까?"] } });
  child.stdout.emit("data", JSON.stringify({ kind: "hitl", payload: { candidates: [{ selected_code: "005930", display_name: "삼성전자" }] } }) + "\n");
  await start;

  const stock = session.send({ kind: "resume", value: { selected_code: "005930" } });
  child.stdout.emit("data", JSON.stringify({ kind: "hitl", payload: { schema_version: "intake_review_hitl/v1", questions: [{ ask_id: "ask-1", slot_id: 4, question: "근거는?" }] } }) + "\n");
  await stock;

  const firstQuestion = session.send({ kind: "resume", value: { answers: [{ ask_id: "ask-1", response_state: "unknown" }] } });
  child.stdout.emit("data", JSON.stringify({ kind: "hitl", payload: { schema_version: "intake_review_hitl/v1", questions: [{ ask_id: "ask-2", slot_id: 8, question: "조건은?" }] } }) + "\n");
  await firstQuestion;

  assert.equal(writes.length, 3);
  assert.deepEqual(JSON.parse(writes[1]), { kind: "resume", value: { selected_code: "005930" } });
  assert.deepEqual(JSON.parse(writes[2]), { kind: "resume", value: { answers: [{ ask_id: "ask-1", response_state: "unknown" }] } });
});

test("a structured start is sent as intake without reconstructing user text", async () => {
  const child = fakeChild();
  const session = new ReviewWorkerSession("s1", child, () => undefined, 1_000);
  let written = "";
  child.stdin.on("data", (chunk) => { written += chunk.toString(); });

  const pending = session.send({
    kind: "start",
    intake: {
      mode: "SURVEY_FIRST",
      target: { name: "삼성전자" },
      structured: [{ slotId: 4, responseState: "answered", value: "AI 수요와 실적 개선을 기대합니다." }],
    },
  });
  child.stdout.write('{"kind":"hitl","payload":{"candidates":[]}}\n');
  await pending;

  assert.deepEqual(JSON.parse(written), {
    kind: "start",
    intake: {
      mode: "SURVEY_FIRST",
      target: { name: "삼성전자" },
      structured: [{ slotId: 4, responseState: "answered", value: "AI 수요와 실적 개선을 기대합니다." }],
    },
  });
});

test("result and protocol failure are terminal and clean the session", async () => {
  const validResult = JSON.stringify({
    kind: "result",
    result: emptyResult(),
  }) + "\n";
  for (const line of [
    validResult,
    '{"kind":"error","code":"REVIEW_FAILED","message":"failed"}\n',
    "not-json\n",
  ]) {
    const child = fakeChild();
    let removed = 0;
    const session = new ReviewWorkerSession("s1", child, () => removed++, 1_000);
    const pending = session.send(start);
    child.stdout.write(line);
    if (line.startsWith("not")) await assert.rejects(pending);
    else await pending;
    assert.equal(child.killed, true);
    assert.equal(removed, 1);
  }
});

test("malformed discriminated payloads are protocol failures", async () => {
  for (const line of [
    '{"kind":"result"}\n',
    '{"kind":"error","code":1,"message":{}}\n',
    '{"kind":"hitl","payload":{"candidates":"wrong"}}\n',
    '{"kind":"hitl","payload":{"candidates":"wrong","questions":[]}}\n',
    '{"kind":"result","result":{"stock":{"code":null,"name":null},"claims":[null],"evidence":[],"finalSummary":"x","banners":[],"degraded":false}}\n',
  ]) {
    const child = fakeChild();
    let removed = 0;
    const session = new ReviewWorkerSession("s1", child, () => removed++, 1_000);
    const pending = session.send(start);
    child.stdout.write(line);
    await assert.rejects(pending, /protocol/);
    assert.equal(child.killed, true);
    assert.equal(removed, 1);
  }
});

test("result rejects noncanonical judgment context fields", async () => {
  const child = fakeChild();
  let removed = 0;
  const session = new ReviewWorkerSession("s1", child, () => removed++, 1_000);
  const pending = session.send(start);
  child.stdout.write(`${JSON.stringify({
    kind: "result",
    result: {
      ...emptyResult(),
      judgmentContext: { rawText: "must not render" },
    },
  })}\n`);

  await assert.rejects(pending, /protocol/);
  assert.equal(removed, 1);
  assert.equal(child.killed, true);
});

test("worker accepts the canonical rich result contract without verdict compression", async () => {
  const child = fakeChild();
  const session = new ReviewWorkerSession("s1", child, () => undefined, 1_000);
  const pending = session.send(start);
  const evidenceId = "01ARZ3NDEKTSV4RRFFQ69G0003";
  const claimId = "01ARZ3NDEKTSV4RRFFQ69G0001";
  const evaluationId = "01ARZ3NDEKTSV4RRFFQ69G0002";
  child.stdout.write(`${JSON.stringify({
    kind: "result",
    result: {
      stock: { code: "005930", name: "삼성전자", market: "KOSPI" },
      judgmentSlots: Array.from({ length: 8 }, (_, index) => ({
        slotId: index + 1,
        status: "ABSENT",
        responseState: "unknown",
        observationIds: [],
        values: [],
        issueIds: [],
        sources: [],
      })),
      claims: [{
        claimId,
        slotId: 4,
        proposition: "2025년 영업이익이 증가했다",
        verifiable: true,
        origin: "survey",
        supersededBy: null,
        evaluation: {
          claimEvaluationId: evaluationId,
          claimId,
          verdict: "contradicted",
          supportEvidenceIds: [],
          opposeEvidenceIds: [evidenceId],
          neutralEvidenceIds: [],
          unknownEvidenceIds: [],
          citations: [{ evidenceId, span: "공식 근거" }],
          numericChecks: [],
          missingDimensions: [],
          uncertaintyCodes: [],
          createdAt: "2026-08-24T00:00:00+00:00",
        },
      }],
      evidence: [{
        evidenceId,
        sourceType: "dart",
        sourceRef: "ref",
        publisher: "금융감독원",
        publishedAt: "2026-08-24T00:00:00+00:00",
        sourceUrl: "https://dart.fss.or.kr/example",
        rawSpan: "공식 근거",
        spanScope: "structured_field",
        relatedQueryIds: ["01ARZ3NDEKTSV4RRFFQ69G0004"],
        relatedClaimIds: [claimId],
        roles: ["PRIMARY"],
        stances: [{ claimId, stance: "oppose", stanceSource: "llm", queryId: "01ARZ3NDEKTSV4RRFFQ69G0004" }],
        source: "금융감독원", excerpt: "공식 근거", url: "https://dart.fss.or.kr/example",
      }],
      findings: [{
        findingId: "01ARZ3NDEKTSV4RRFFQ69G0006",
        slotId: 4,
        kind: "mismatch",
        citations: [{ evidenceId, span: "공식 근거" }],
        claimEvaluationId: evaluationId,
        createdAt: "2026-08-24T00:00:00+00:00",
      }],
      opposingSearch: { status: "verified", count: 1, queries: ["반대 검색"], reason: null },
      providerCollections: {},
      report: {
        schemaVersion: "s0.v1",
        renderedSlots: [{ slotNo: 4, text: "검토 결과", citations: [{ evidenceId, span: "공식 근거" }] }],
        banners: [], theoryNotes: [],
        citations: [{ evidenceId, span: "공식 근거", sourceUrl: null, publisher: "금융감독원" }],
        createdAt: "2026-08-24T00:00:00+00:00",
      },
      finalSummary: "검토 결과",
      banners: [],
      degraded: false,
      judgmentContext: {},
    },
  })}\n`);

  const response = await pending;
  assert.equal(response.kind, "result");
  if (response.kind === "result") {
    assert.equal(response.result.claims[0].evaluation?.verdict, "contradicted");
  }
});

test("worker accepts a safe terminal response", async () => {
  const child = fakeChild();
  let removed = 0;
  const session = new ReviewWorkerSession("s1", child, () => removed++, 1_000);
  const pending = session.send(start);
  child.stdout.write('{"kind":"terminal","reasonCode":"prompt_injection","message":"검토를 종료했습니다."}\n');

  const response = await pending;
  assert.deepEqual(response, {
    kind: "terminal",
    reasonCode: "prompt_injection",
    message: "검토를 종료했습니다.",
  });
  assert.equal(child.killed, true);
  assert.equal(removed, 1);
});

test("timeout and process exit are terminal and clean the session", async () => {
  for (const terminal of ["timeout", "exit"] as const) {
    const child = fakeChild();
    let removed = 0;
    const session = new ReviewWorkerSession("s1", child, () => removed++, 5);
    const pending = session.send(start);
    if (terminal === "exit") child.emit("exit", 1);
    await assert.rejects(pending);
    assert.equal(child.killed, true);
    assert.equal(removed, 1);
  }
});
