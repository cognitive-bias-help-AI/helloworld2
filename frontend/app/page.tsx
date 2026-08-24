"use client";

import { FormEvent, useState } from "react";
import { resumeReview, submitReview } from "../lib/api";
import type { HitlPayload, ReviewResponse, ReviewResult } from "../lib/types";

type View = "idle" | "loading" | "hitl" | "success" | "error";

export default function Home() {
  const [text, setText] = useState("");
  const [view, setView] = useState<View>("idle");
  const [sessionId, setSessionId] = useState("");
  const [hitl, setHitl] = useState<HitlPayload | null>(null);
  const [result, setResult] = useState<ReviewResult | null>(null);
  const [error, setError] = useState("");

  function apply(response: ReviewResponse & { sessionId?: string }) {
    if (response.sessionId) setSessionId(response.sessionId);
    if (response.kind === "hitl") { setHitl(response.payload); setView("hitl"); }
    if (response.kind === "result") { setResult(response.result); setView("success"); }
    if (response.kind === "error") { setError(response.message); setView("error"); }
  }

  async function start(event: FormEvent) {
    event.preventDefault();
    if (!text.trim() || view === "loading") return;
    setView("loading"); setError("");
    try { apply(await submitReview(text.trim())); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "검토에 실패했습니다."); setView("error"); }
  }

  async function resume(value: unknown) {
    setView("loading");
    try { apply(await resumeReview(sessionId, value)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "검토에 실패했습니다."); setView("error"); }
  }

  return <main>
    <header><p className="eyebrow">LOCAL E2E REVIEW</p><h1>투자 판단의 근거를 점검합니다</h1><p>작성한 판단을 기존 검토 흐름에 전달하고, 저장된 결과와 출처를 그대로 보여줍니다.</p></header>
    <section className="card">
      <form onSubmit={start}>
        <label htmlFor="review-text">검토할 판단</label>
        <textarea id="review-text" value={text} onChange={(e) => setText(e.target.value)} rows={6} placeholder="예: 삼성전자 영업이익이 증가했으니 지금 매수해도 될까?" disabled={view === "loading"} />
        <button disabled={!text.trim() || view === "loading"}>근거 점검 시작</button>
      </form>
    </section>
    <div aria-live="polite">
      {view === "loading" && <section className="card state"><h2>검토 중</h2><p>판단 근거를 점검하고 있습니다...</p></section>}
      {view === "hitl" && hitl && <Hitl payload={hitl} onResume={resume} />}
      {view === "success" && result && <Result result={result} />}
      {view === "error" && <section className="card error"><h2>검토를 완료하지 못했습니다</h2><p>{error}</p></section>}
    </div>
    <footer>이 화면은 기존 검토 결과를 표현하며, 투자 권유·매수/매도 판단을 생성하지 않습니다.</footer>
  </main>;
}

function Hitl({ payload, onResume }: { payload: HitlPayload; onResume: (value: unknown) => void }) {
  const [choice, setChoice] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  if ("candidates" in payload) return <section className="card"><h2>종목을 확인해주세요</h2>{payload.candidates.map((item) => <label className="option" key={item.selected_code}><input type="radio" name="stock" value={item.selected_code} checked={choice === item.selected_code} onChange={() => setChoice(item.selected_code)} /> <span>{item.display_name} · {item.selected_code}{item.market ? ` · ${item.market}` : ""}</span></label>)}<button disabled={!choice} onClick={() => onResume({ selected_code: choice })}>이 종목으로 계속</button></section>;
  const complete = payload.questions.every((item) => answers[item.ask_id]?.trim());
  return <section className="card"><h2>추가 확인이 필요합니다</h2>{payload.questions.map((item) => <label key={item.ask_id}>{item.question}<textarea rows={3} value={answers[item.ask_id] || ""} onChange={(e) => setAnswers({ ...answers, [item.ask_id]: e.target.value })} /></label>)}<button disabled={!complete} onClick={() => onResume({ answers: payload.questions.map((item) => ({ ask_id: item.ask_id, answer: answers[item.ask_id].trim() })) })}>답변하고 계속</button></section>;
}

function Result({ result }: { result: ReviewResult }) {
  return <section className="result">
    {result.degraded && <div className="banner">일부 근거 수집이 제한된 결과입니다.</div>}
    <div className="card"><p className="eyebrow">검토 대상</p><h2>{result.stock.name || "종목 미확인"} <small>{result.stock.code}</small></h2></div>
    <div className="card"><h2>주장 점검</h2>{result.claims.map((claim, index) => <article className="claim" key={`${claim.text}-${index}`}><strong>{claim.status === "verified" ? "✓ 확인" : claim.status === "partial" ? "△ 일부 확인" : "? 미확인"}</strong><p>{claim.text}</p><small>{claim.summary}</small></article>)}</div>
    <div className="card"><h2>근거</h2>{result.evidence.length ? result.evidence.map((item, index) => <article className="evidence" key={`${item.source}-${index}`}><strong>{item.source}</strong>{item.publishedAt && <small> · {new Date(item.publishedAt).toLocaleDateString("ko-KR")}</small>}<p>{item.excerpt}</p>{item.url && <a href={item.url} target="_blank" rel="noreferrer">원문 보기</a>}</article>) : <p>표시할 근거가 없습니다.</p>}</div>
    <div className="card"><h2>최종 검토 결과</h2><p className="summary">{result.finalSummary}</p></div>
  </section>;
}
