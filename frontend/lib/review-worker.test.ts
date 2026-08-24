import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import test from "node:test";

import { ReviewWorkerSession } from "./review-worker.ts";

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
  const first = session.send({ kind: "start", text: "검토" });

  await assert.rejects(session.send({ kind: "resume", value: {} }), /in flight/);
  child.stdout.write('{"kind":"hitl","payload":{"candidates":[]}}\n');
  await first;
});

test("result and protocol failure are terminal and clean the session", async () => {
  const validResult = JSON.stringify({
    kind: "result",
    result: { stock: { code: null, name: null }, claims: [], evidence: [], finalSummary: "done", banners: [], degraded: false },
  }) + "\n";
  for (const line of [
    validResult,
    '{"kind":"error","code":"REVIEW_FAILED","message":"failed"}\n',
    "not-json\n",
  ]) {
    const child = fakeChild();
    let removed = 0;
    const session = new ReviewWorkerSession("s1", child, () => removed++, 1_000);
    const pending = session.send({ kind: "start", text: "검토" });
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
    const pending = session.send({ kind: "start", text: "검토" });
    child.stdout.write(line);
    await assert.rejects(pending, /protocol/);
    assert.equal(child.killed, true);
    assert.equal(removed, 1);
  }
});

test("timeout and process exit are terminal and clean the session", async () => {
  for (const terminal of ["timeout", "exit"] as const) {
    const child = fakeChild();
    let removed = 0;
    const session = new ReviewWorkerSession("s1", child, () => removed++, 5);
    const pending = session.send({ kind: "start", text: "검토" });
    if (terminal === "exit") child.emit("exit", 1);
    await assert.rejects(pending);
    assert.equal(child.killed, true);
    assert.equal(removed, 1);
  }
});
