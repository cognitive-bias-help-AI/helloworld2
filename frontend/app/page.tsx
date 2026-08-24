"use client";

import { FormEvent, type ReactNode, useRef, useState } from "react";

import { resumeReview, submitReview } from "../lib/api";
import { INFORMATION_CATEGORIES, parseReviewIntake, targetFromStockInput, toggleInformationChecked, type DecisionAction, type HoldingState, type InformationCategory, type IntakeMode, type ResponseState, type StructuredResponse, type TimeHorizon } from "../lib/intake";
import type { HitlPayload, ReviewResponse } from "../lib/types";
import { applyReviewResponse, beginReview, initialReviewPageState, questionResumeValue, reviewFormControls, reviewPageText, selectedCodeResumeValue } from "../lib/review-page-state";
import { ReviewResult } from "../components/review/ReviewResult";
const information: Record<InformationCategory, string> = { FINANCIALS: "실적", DISCLOSURE: "공시", NEWS: "뉴스", PRICE_CHART: "주가", INDUSTRY: "업종", OTHER: "기타", NONE_CHECKED: "아직 확인하지 않음" };
const responseStateLabels: Record<ResponseState, string> = { answered: "직접 답변", unknown: "잘 모르겠어요", undecided: "아직 정하지 못했어요", user_declined: "답변하지 않을게요" };

export default function Home() {
  const [mode, setMode] = useState<IntakeMode>("SURVEY_FIRST");
  const [stockInput, setStockInput] = useState("");
  const [chatText, setChatText] = useState("");
  const [additionalText, setAdditionalText] = useState("");
  const [decisionAction, setDecisionAction] = useState<DecisionAction>("CONSIDER_ENTRY");
  const [holdingState, setHoldingState] = useState<HoldingState>("NOT_HOLDING");
  const [timeHorizon, setTimeHorizon] = useState<TimeHorizon>("LONG");
  const [primaryReasons, setPrimaryReasons] = useState("");
  const [expectedOutcome, setExpectedOutcome] = useState("");
  const [informationChecked, setInformationChecked] = useState<InformationCategory[]>([]);
  const [counterEvidenceConcerns, setCounterEvidenceConcerns] = useState("");
  const [changeConditions, setChangeConditions] = useState("");
  const [responseStates, setResponseStates] = useState<Record<number, ResponseState>>({ 1: "answered", 2: "answered", 3: "answered", 4: "answered", 5: "answered", 6: "answered", 7: "answered", 8: "answered" });
  const [flow, setFlow] = useState(initialReviewPageState);
  const requestInFlight = useRef(false);
  const loading = flow.view === "loading";

  function apply(response: ReviewResponse & { sessionId?: string }) {
    setFlow((current) => applyReviewResponse(current, response));
  }

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (loading || requestInFlight.current) return;
    let intake;
    try {
      const target = targetFromStockInput(stockInput);
      const structured: StructuredResponse[] = [];
      const add = (slotId: number, value?: string | string[]) => {
        const responseState = responseStates[slotId];
        if (responseState === "answered") {
          if ((typeof value === "string" && !value.trim()) || (Array.isArray(value) && !value.length) || value === undefined) return;
          structured.push({ slotId, responseState, value });
        } else structured.push({ slotId, responseState });
      };
      if (mode !== "CHAT_FIRST") {
        add(1, decisionAction); add(2, holdingState); add(3, timeHorizon); add(4, primaryReasons);
        add(5, expectedOutcome); add(6, informationChecked); add(7, counterEvidenceConcerns); add(8, changeConditions);
      }
      intake = parseReviewIntake({
        mode,
        ...(target ? { target } : {}),
        ...(mode === "CHAT_FIRST" ? { freeText: [chatText] } : {}),
        ...(mode === "SURVEY_FIRST" ? { structured } : {}),
        ...(mode === "HYBRID" ? { structured, ...(additionalText.trim() ? { freeText: [additionalText] } : {}) } : {}),
      });
    } catch {
      apply({ kind: "error", code: "REVIEW_FAILED", message: "필수 입력 내용을 다시 확인해주세요." });
      return;
    }
    requestInFlight.current = true;
    setFlow(beginReview);
    try { apply(await submitReview(intake)); }
    catch (reason) { apply({ kind: "error", code: "REVIEW_FAILED", message: reason instanceof Error ? reason.message : "점검을 시작하지 못했습니다." }); }
    finally { requestInFlight.current = false; }
  }

  async function resume(value: unknown) {
    if (loading || requestInFlight.current) return;
    requestInFlight.current = true;
    setFlow(beginReview);
    try { apply(await resumeReview(flow.sessionId, value)); }
    catch (reason) { apply({ kind: "error", code: "REVIEW_FAILED", message: reason instanceof Error ? reason.message : "점검을 계속하지 못했습니다." }); }
    finally { requestInFlight.current = false; }
  }

  return <main>
    <header className="intro"><h1 className="brand">{reviewPageText.title} <span>Beta</span></h1><p>내 판단의 근거를 객관적인 자료와 함께 다시 확인합니다.</p></header>
    <section className="card intake-card"><form onSubmit={start}><fieldset disabled={loading}>
      <div className="mode-selector" role="group" aria-label="입력 방식">{(["CHAT_FIRST", "SURVEY_FIRST", "HYBRID"] as const).map((item) => <button type="button" className={mode === item ? "active" : ""} key={item} onClick={() => setMode(item)}>{item === "CHAT_FIRST" ? "빠르게 입력" : item === "SURVEY_FIRST" ? "항목별 입력" : "둘 다"}</button>)}</div>
      {mode === "CHAT_FIRST" && <FormSection number="1" title="판단을 자유롭게 적어주세요"><label>종목 <span className="optional">(선택)</span><input value={stockInput} onChange={(event) => setStockInput(event.target.value)} placeholder="예: 삼성전자 또는 005930" /></label><label>판단 내용<textarea value={chatText} onChange={(event) => setChatText(event.target.value)} rows={7} placeholder="예: 삼성전자 살까 고민 중인데 AI 수요 때문에 실적이 좋아질 것 같아." /></label></FormSection>}
      {mode !== "CHAT_FIRST" && <>
      <FormSection number="1" title="어떤 판단을 하고 있나요?"><label>종목<input value={stockInput} onChange={(event) => setStockInput(event.target.value)} placeholder="예: 삼성전자" required={reviewFormControls.stockInput.required} /></label><div className="select-grid"><label>현재 판단<ResponseStateSelect value={responseStates[1]} onChange={(value) => setResponseStates({ ...responseStates, 1: value })} />{responseStates[1] === "answered" && <Select value={decisionAction} onChange={setDecisionAction} labels={reviewFormControls.decisionAction} />}</label><label>보유 상태<ResponseStateSelect value={responseStates[2]} onChange={(value) => setResponseStates({ ...responseStates, 2: value })} />{responseStates[2] === "answered" && <Select value={holdingState} onChange={setHoldingState} labels={reviewFormControls.holdingState} />}</label><label>투자 관점<ResponseStateSelect value={responseStates[3]} onChange={(value) => setResponseStates({ ...responseStates, 3: value })} />{responseStates[3] === "answered" && <Select value={timeHorizon} onChange={setTimeHorizon} labels={reviewFormControls.timeHorizon} />}</label></div></FormSection>
      <FormSection number="2" title="왜 그렇게 생각하나요?"><ResponseStateSelect value={responseStates[4]} onChange={(value) => setResponseStates({ ...responseStates, 4: value })} />{responseStates[4] === "answered" && <label>주요 판단 근거<textarea value={primaryReasons} onChange={(event) => setPrimaryReasons(event.target.value)} rows={4} placeholder="판단에 이르게 된 핵심 근거를 적어주세요." required /></label>}</FormSection>
      <FormSection number="3" title="어떤 결과를 기대하나요?"><ResponseStateSelect value={responseStates[5]} onChange={(value) => setResponseStates({ ...responseStates, 5: value })} />{responseStates[5] === "answered" && <label>기대 결과<textarea value={expectedOutcome} onChange={(event) => setExpectedOutcome(event.target.value)} rows={3} placeholder="예: 실적 개선이 이어지며 기업가치가 높아질 것으로 기대합니다." /></label>}</FormSection>
      <FormSection number="4" title="이미 무엇을 확인했나요?"><ResponseStateSelect value={responseStates[6]} onChange={(value) => setResponseStates({ ...responseStates, 6: value })} />{responseStates[6] === "answered" && <div className="checks" role="group" aria-label="확인한 정보">{INFORMATION_CATEGORIES.map((category) => <label className="check" key={category}><input type="checkbox" checked={informationChecked.includes(category)} onChange={() => setInformationChecked((selected) => toggleInformationChecked(selected, category))} />{information[category]}</label>)}</div>}</FormSection>
      <FormSection number="5" title="반대되는 근거나 우려되는 점이 있나요?"><ResponseStateSelect value={responseStates[7]} onChange={(value) => setResponseStates({ ...responseStates, 7: value })} />{responseStates[7] === "answered" && <label>반대 근거 또는 우려<textarea value={counterEvidenceConcerns} onChange={(event) => setCounterEvidenceConcerns(event.target.value)} rows={3} placeholder="예: 경쟁력 회복이 늦어질 수 있습니다." /></label>}</FormSection>
      <FormSection number="6" title="무엇이 바뀌면 다시 판단할까요?"><ResponseStateSelect value={responseStates[8]} onChange={(value) => setResponseStates({ ...responseStates, 8: value })} />{responseStates[8] === "answered" && <label>변경 조건<textarea value={changeConditions} onChange={(event) => setChangeConditions(event.target.value)} rows={3} placeholder="예: 영업이익 증가세가 꺾이면 다시 검토합니다." /></label>}</FormSection>
      {mode === "HYBRID" && <FormSection number="7" title="추가로 하고 싶은 말"><textarea value={additionalText} onChange={(event) => setAdditionalText(event.target.value)} rows={4} placeholder="항목에 담기 어려운 판단 맥락을 자유롭게 적어주세요." /></FormSection>}
      </>}
    </fieldset><button className="primary" disabled={loading || (mode === "CHAT_FIRST" ? !chatText.trim() : !stockInput.trim() || (responseStates[4] === "answered" && !primaryReasons.trim()))}>{loading ? "점검을 준비하고 있습니다" : "판단 근거 점검하기"}</button><p className="form-note">입력하지 않은 내용은 점검 과정에서 추가로 질문할 수 있습니다.</p></form></section>
    <div aria-live="polite">{loading && <section className="card state"><h2>점검 중</h2><p>{reviewPageText.loading}</p></section>}{flow.view === "hitl" && flow.hitl && <Hitl payload={flow.hitl} onResume={resume} />}{flow.view === "success" && flow.result && <ReviewResult result={flow.result} />}{flow.view === "error" && <section className="card error"><h2>점검을 완료하지 못했습니다</h2><p>{flow.error}</p></section>}</div>
    <footer>매수·매도 추천이 아닌 판단 근거 점검 결과입니다.</footer>
  </main>;
}

function FormSection({ number, title, children }: { number: string; title: string; children: ReactNode }) { return <section className="form-section"><h2><span>{number}.</span> {title}</h2>{children}</section>; }
function Select<T extends string>({ value, onChange, labels }: { value: T; onChange: (value: T) => void; labels: Record<T, string> }) { return <select value={value} onChange={(event) => onChange(event.target.value as T)}>{(Object.entries(labels) as Array<[T, string]>).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>; }
function ResponseStateSelect({ value, onChange }: { value: ResponseState; onChange: (value: ResponseState) => void }) { return <span className="response-state">응답 방식 <select aria-label="응답 방식" value={value} onChange={(event) => onChange(event.target.value as ResponseState)}>{Object.entries(responseStateLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></span>; }

function Hitl({ payload, onResume }: { payload: HitlPayload; onResume: (value: unknown) => void }) {
  const [choice, setChoice] = useState("");
  const [answers, setAnswers] = useState<Record<string, { responseState: ResponseState; answer?: string }>>({});
  if ("candidates" in payload) return <section className="card"><h2>종목을 확인해주세요</h2>{payload.candidates.map((item) => <label className="option" key={item.selected_code}><input type="radio" name="stock" value={item.selected_code} checked={choice === item.selected_code} onChange={() => setChoice(item.selected_code)} /> <span>{item.display_name} · {item.selected_code}{item.market ? ` · ${item.market}` : ""}</span></label>)}<button className="primary" disabled={!choice} onClick={() => onResume(selectedCodeResumeValue(choice))}>이 종목으로 계속</button></section>;
  const complete = payload.questions.every((item) => answers[item.ask_id] && (answers[item.ask_id].responseState !== "answered" || answers[item.ask_id].answer?.trim()));
  return <section className="card"><h2>추가 확인이 필요합니다</h2>{payload.questions.map((item) => { const response = answers[item.ask_id]; return <div key={item.ask_id}><p>{item.question}</p><ResponseStateSelect value={response?.responseState ?? "answered"} onChange={(responseState) => setAnswers({ ...answers, [item.ask_id]: { responseState } })} />{(response?.responseState ?? "answered") === "answered" && <textarea rows={3} value={response?.answer || ""} onChange={(event) => setAnswers({ ...answers, [item.ask_id]: { responseState: "answered", answer: event.target.value } })} />}</div>; })}<button className="primary" disabled={!complete} onClick={() => onResume(questionResumeValue(payload.questions, answers))}>답변하고 계속</button></section>;
}
