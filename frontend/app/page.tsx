"use client";

import { FormEvent, type ReactNode, useState } from "react";

import { resumeReview, submitReview } from "../lib/api";
import { INFORMATION_CATEGORIES, parseReviewIntake, toggleInformationChecked, type DecisionAction, type HoldingState, type InformationCategory, type TimeHorizon } from "../lib/intake";
import type { HitlPayload, ReviewResponse, ReviewResult } from "../lib/types";
import { applyReviewResponse, beginReview, initialReviewPageState, questionResumeValue, reviewFormControls, reviewPageText, selectedCodeResumeValue } from "../lib/review-page-state";
const information: Record<InformationCategory, string> = { FINANCIALS: "실적", DISCLOSURE: "공시", NEWS: "뉴스", PRICE_CHART: "주가", INDUSTRY: "업종", OTHER: "기타", NONE_CHECKED: "아직 확인하지 않음" };

export default function Home() {
  const [stockInput, setStockInput] = useState("");
  const [decisionAction, setDecisionAction] = useState<DecisionAction>("CONSIDER_ENTRY");
  const [holdingState, setHoldingState] = useState<HoldingState>("NOT_HOLDING");
  const [timeHorizon, setTimeHorizon] = useState<TimeHorizon>("LONG");
  const [primaryReasons, setPrimaryReasons] = useState("");
  const [expectedOutcome, setExpectedOutcome] = useState("");
  const [informationChecked, setInformationChecked] = useState<InformationCategory[]>([]);
  const [counterEvidenceConcerns, setCounterEvidenceConcerns] = useState("");
  const [changeConditions, setChangeConditions] = useState("");
  const [flow, setFlow] = useState(initialReviewPageState);
  const loading = flow.view === "loading";

  function apply(response: ReviewResponse & { sessionId?: string }) {
    setFlow((current) => applyReviewResponse(current, response));
  }

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (loading) return;
    let intake;
    try {
      intake = parseReviewIntake({ stockInput, decisionAction, holdingState, timeHorizon, primaryReasons, expectedOutcome, informationChecked, counterEvidenceConcerns, changeConditions });
    } catch {
      apply({ kind: "error", code: "REVIEW_FAILED", message: "필수 입력 내용을 다시 확인해주세요." });
      return;
    }
    setFlow(beginReview);
    try { apply(await submitReview(intake)); }
    catch (reason) { apply({ kind: "error", code: "REVIEW_FAILED", message: reason instanceof Error ? reason.message : "점검을 시작하지 못했습니다." }); }
  }

  async function resume(value: unknown) {
    if (loading) return;
    setFlow(beginReview);
    try { apply(await resumeReview(flow.sessionId, value)); }
    catch (reason) { apply({ kind: "error", code: "REVIEW_FAILED", message: reason instanceof Error ? reason.message : "점검을 계속하지 못했습니다." }); }
  }

  return <main>
    <header className="intro"><h1 className="brand">{reviewPageText.title} <span>Beta</span></h1><p>내 판단의 근거를 객관적인 자료와 함께 다시 확인합니다.</p></header>
    <section className="card intake-card"><form onSubmit={start}><fieldset disabled={loading}>
      <FormSection number="1" title="어떤 판단을 하고 있나요?"><label>종목<input value={stockInput} onChange={(event) => setStockInput(event.target.value)} placeholder="예: 삼성전자" required={reviewFormControls.stockInput.required} /></label><div className="select-grid"><label>현재 판단<Select value={decisionAction} onChange={setDecisionAction} labels={reviewFormControls.decisionAction} /></label><label>보유 상태<Select value={holdingState} onChange={setHoldingState} labels={reviewFormControls.holdingState} /></label><label>투자 관점<Select value={timeHorizon} onChange={setTimeHorizon} labels={reviewFormControls.timeHorizon} /></label></div></FormSection>
      <FormSection number="2" title="왜 그렇게 생각하나요?"><label>주요 판단 근거<textarea value={primaryReasons} onChange={(event) => setPrimaryReasons(event.target.value)} rows={4} placeholder="판단에 이르게 된 핵심 근거를 적어주세요." required={reviewFormControls.primaryReasons.required} /></label></FormSection>
      <FormSection number="3" title="어떤 결과를 기대하나요?"><label>기대 결과<textarea value={expectedOutcome} onChange={(event) => setExpectedOutcome(event.target.value)} rows={3} placeholder="예: 실적 개선이 이어지며 기업가치가 높아질 것으로 기대합니다." /></label></FormSection>
      <FormSection number="4" title="이미 무엇을 확인했나요?"><div className="checks" role="group" aria-label="확인한 정보">{INFORMATION_CATEGORIES.map((category) => <label className="check" key={category}><input type="checkbox" checked={informationChecked.includes(category)} onChange={() => setInformationChecked((selected) => toggleInformationChecked(selected, category))} />{information[category]}</label>)}</div></FormSection>
      <FormSection number="5" title="반대되는 근거나 우려되는 점이 있나요?"><label>반대 근거 또는 우려<textarea value={counterEvidenceConcerns} onChange={(event) => setCounterEvidenceConcerns(event.target.value)} rows={3} placeholder="예: 경쟁력 회복이 늦어질 수 있습니다." /></label></FormSection>
      <FormSection number="6" title="무엇이 바뀌면 다시 판단할까요?"><label>변경 조건<textarea value={changeConditions} onChange={(event) => setChangeConditions(event.target.value)} rows={3} placeholder="예: 영업이익 증가세가 꺾이면 다시 검토합니다." /></label></FormSection>
    </fieldset><button className="primary" disabled={loading || !stockInput.trim() || !primaryReasons.trim()}>{loading ? "점검을 준비하고 있습니다" : "판단 근거 점검하기"}</button><p className="form-note">입력하지 않은 내용은 점검 과정에서 추가로 질문할 수 있습니다.</p></form></section>
    <div aria-live="polite">{loading && <section className="card state"><h2>점검 중</h2><p>{reviewPageText.loading}</p></section>}{flow.view === "hitl" && flow.hitl && <Hitl payload={flow.hitl} onResume={resume} />}{flow.view === "success" && flow.result && <Result result={flow.result} />}{flow.view === "error" && <section className="card error"><h2>점검을 완료하지 못했습니다</h2><p>{flow.error}</p></section>}</div>
    <footer>매수·매도 추천이 아닌 판단 근거 점검 결과입니다.</footer>
  </main>;
}

function FormSection({ number, title, children }: { number: string; title: string; children: ReactNode }) { return <section className="form-section"><h2><span>{number}.</span> {title}</h2>{children}</section>; }
function Select<T extends string>({ value, onChange, labels }: { value: T; onChange: (value: T) => void; labels: Record<T, string> }) { return <select value={value} onChange={(event) => onChange(event.target.value as T)}>{(Object.entries(labels) as Array<[T, string]>).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>; }

function Hitl({ payload, onResume }: { payload: HitlPayload; onResume: (value: unknown) => void }) {
  const [choice, setChoice] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  if ("candidates" in payload) return <section className="card"><h2>종목을 확인해주세요</h2>{payload.candidates.map((item) => <label className="option" key={item.selected_code}><input type="radio" name="stock" value={item.selected_code} checked={choice === item.selected_code} onChange={() => setChoice(item.selected_code)} /> <span>{item.display_name} · {item.selected_code}{item.market ? ` · ${item.market}` : ""}</span></label>)}<button className="primary" disabled={!choice} onClick={() => onResume(selectedCodeResumeValue(choice))}>이 종목으로 계속</button></section>;
  const complete = payload.questions.every((item) => answers[item.ask_id]?.trim());
  return <section className="card"><h2>추가 확인이 필요합니다</h2>{payload.questions.map((item) => <label key={item.ask_id}>{item.question}<textarea rows={3} value={answers[item.ask_id] || ""} onChange={(event) => setAnswers({ ...answers, [item.ask_id]: event.target.value })} /></label>)}<button className="primary" disabled={!complete} onClick={() => onResume(questionResumeValue(payload.questions, answers))}>답변하고 계속</button></section>;
}

function Result({ result }: { result: ReviewResult }) {
  const context = [
    ["현재 판단", result.judgmentContext.decisionAction && reviewFormControls.decisionAction[result.judgmentContext.decisionAction]],
    ["보유 상태", result.judgmentContext.holdingState && reviewFormControls.holdingState[result.judgmentContext.holdingState]],
    ["투자 관점", result.judgmentContext.timeHorizon && reviewFormControls.timeHorizon[result.judgmentContext.timeHorizon]],
    ["주요 판단 근거", result.judgmentContext.primaryReasons],
    ["기대 결과", result.judgmentContext.expectedOutcome],
  ].filter((entry): entry is [string, string] => Boolean(entry[1]));
  return <section className="result">{result.degraded && <div className="banner">일부 근거 수집이 제한된 결과입니다.</div>}<div className="card"><p className="eyebrow">검토 대상</p><h2>{result.stock.name || "종목 미확인"} <small>{result.stock.code}</small></h2></div>{context.length > 0 && <div className="card judgment-context"><h2>사용자 판단 맥락</h2><dl>{context.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></div>}<div className="card"><h2>주장 점검</h2>{result.claims.map((claim, index) => <article className="claim" key={`${claim.text}-${index}`}><strong>{claim.status === "verified" ? "✓ 확인" : claim.status === "partial" ? "△ 일부 확인" : "? 미확인"}</strong><p>{claim.text}</p><small>{claim.summary}</small></article>)}</div><div className="card"><h2>근거</h2>{result.evidence.length ? result.evidence.map((item, index) => <article className="evidence" key={`${item.source}-${index}`}><strong>{item.source}</strong>{item.publishedAt && <small> · {new Date(item.publishedAt).toLocaleDateString("ko-KR")}</small>}<p>{item.excerpt}</p>{item.url && <a href={item.url} target="_blank" rel="noreferrer">원문 보기</a>}</article>) : <p>표시할 근거가 없습니다.</p>}</div><div className="card"><h2>최종 검토 결과</h2><p className="summary">{result.finalSummary}</p></div></section>;
}
