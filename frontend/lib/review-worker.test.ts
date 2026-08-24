import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import test from "node:test";

import { ReviewWorkerSession } from "./review-worker.ts";

const start = {
  kind: "start" as const,
  intake: {
    stockInput: "삼성전자",
    decisionAction: "CONSIDER_ENTRY" as const,
    holdingState: "NOT_HOLDING" as const,
    timeHorizon: "LONG" as const,
    primaryReasons: "검토",
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
    result: { stock: { code: "005930", name: "삼성전자" }, claims: [], evidence: [], finalSummary: "done", banners: [], degraded: false, judgmentContext: {} },
  })}\n`);
  await third;
  assert.equal(child.killed, true);
  assert.equal(removed, 1);
});

test("a structured start is sent as intake without reconstructing user text", async () => {
  const child = fakeChild();
  const session = new ReviewWorkerSession("s1", child, () => undefined, 1_000);
  let written = "";
  child.stdin.on("data", (chunk) => { written += chunk.toString(); });

  const pending = session.send({
    kind: "start",
    intake: {
      stockInput: "삼성전자",
      decisionAction: "CONSIDER_ENTRY",
      holdingState: "NOT_HOLDING",
      timeHorizon: "LONG",
      primaryReasons: "AI 수요와 실적 개선을 기대합니다.",
      informationChecked: ["FINANCIALS", "NEWS"],
    },
  });
  child.stdout.write('{"kind":"hitl","payload":{"candidates":[]}}\n');
  await pending;

  assert.deepEqual(JSON.parse(written), {
    kind: "start",
    intake: {
      stockInput: "삼성전자",
      decisionAction: "CONSIDER_ENTRY",
      holdingState: "NOT_HOLDING",
      timeHorizon: "LONG",
      primaryReasons: "AI 수요와 실적 개선을 기대합니다.",
      informationChecked: ["FINANCIALS", "NEWS"],
    },
  });
});

test("result and protocol failure are terminal and clean the session", async () => {
  const validResult = JSON.stringify({
    kind: "result",
    result: { stock: { code: null, name: null }, claims: [], evidence: [], finalSummary: "done", banners: [], degraded: false, judgmentContext: {} },
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
      stock: { code: null, name: null }, claims: [], evidence: [], finalSummary: "done", banners: [], degraded: false,
      judgmentContext: { rawText: "must not render" },
    },
  })}\n`);

  await assert.rejects(pending, /protocol/);
  assert.equal(removed, 1);
  assert.equal(child.killed, true);
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
