import type { ReviewResult as ReviewResultData } from "../../lib/types";
import { buildReviewResultView } from "../../lib/review-result-view";

const sourceLabels: Record<string, string> = { dart: "DART", news: "NAVER 뉴스", quote: "Kiwoom", DART: "DART" };
const bucketLabels = { support: "뒷받침 근거", oppose: "반대 근거", neutral: "중립 근거", unknown: "판단이 어려운 근거" } as const;

function dateLabel(value: string | null) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("ko-KR");
}

type EvidenceItemView = ReturnType<typeof buildReviewResultView>["evidence"][number];

function EvidenceCard({ item }: { item: EvidenceItemView }) {
  return <article className="evidence-card" id={`evidence-${item.evidenceId}`}>
    <div className="evidence-meta"><strong>{item.publisher || sourceLabels[item.sourceType] || item.source}</strong>{dateLabel(item.publishedAt) && <span>{dateLabel(item.publishedAt)}</span>}</div>
    <div className="tag-row">{item.roleLabels.map((label) => <span className="role-tag" key={label}>{label}</span>)}{item.stances.map((stance, index) => <span className={`stance-tag stance-${stance.stance}`} key={`${stance.claimId}-${index}`}>{stance.label}</span>)}</div>
    <blockquote>{item.rawSpan || item.excerpt}</blockquote>
    {item.safeUrl && <a className="source-link" href={item.safeUrl} target="_blank" rel="noopener noreferrer">원문 자료 열기</a>}
    <details><summary>근거 계보 보기</summary><p>Evidence ID: <code>{item.evidenceId}</code></p><p>관련 Claim: {item.relatedClaimIds.join(", ") || "없음"}</p><p>관련 Query: {item.relatedQueryIds.join(", ") || "없음"}</p></details>
  </article>;
}

type EvidenceGroupView = ReturnType<typeof buildReviewResultView>["evidenceGroups"][number];

function EvidenceSourceToggles({ groups, emptyMessage }: { groups: EvidenceGroupView[]; emptyMessage: string }) {
  return <div className="evidence-source-list">
    {groups.map((group) => <details className="evidence-source-toggle" key={group.sourceType}>
      <summary>
        <span className="evidence-toggle-title"><strong>{group.label}</strong><small>{group.description}</small></span>
        <span className="evidence-toggle-meta"><b>{group.items.length}건</b><em>{group.statusLabel}</em></span>
      </summary>
      <div className="evidence-source-body">
        {group.reasonLabel && <p className="evidence-source-reason">{group.reasonLabel}</p>}
        {group.items.length > 0
          ? group.items.map((item) => <EvidenceCard item={item} key={item.evidenceId} />)
          : <p className="empty-note">{emptyMessage}</p>}
      </div>
    </details>)}
  </div>;
}

export function ReviewResult({ result }: { result: ReviewResultData }) {
  const view = buildReviewResultView(result);

  return <section className="review-result" aria-labelledby="review-result-title">
    <section className="card result-summary">
      <p className="eyebrow">판단 근거 점검 결과</p>
      <h2 id="review-result-title">{view.stock.name || "종목 미확인"} {view.stock.code && <small>{view.stock.code}</small>}</h2>
      {view.stock.market && <p className="market-label">{view.stock.market}</p>}
      <p>입력한 판단을 자료와 대조해 확인된 부분과 다시 살펴볼 부분을 정리했습니다.</p>
      {view.degraded && <p className="degraded-note">일부 자료 수집이 제한된 결과입니다.</p>}
      {view.summaryCounts.length > 0 && <div className="summary-counts" aria-label="판단 점검 요약">{view.summaryCounts.map((item) => <div key={item.key}><strong>{item.count}</strong><span>{item.label}</span></div>)}</div>}
      {view.banners.length > 0 && <div className="banner-list" aria-label="검토 범위 안내">{view.banners.map((banner) => <div className="result-banner" key={banner.code}><strong>검토 안내</strong><span>{banner.label}</span></div>)}</div>}
    </section>

    <section className="card" aria-labelledby="judgment-title">
      <h2 id="judgment-title">내 판단</h2>
      <div className="slot-list">{view.slots.map((slot) => <article className="slot-card" key={slot.slotId}>
        <div className="slot-heading"><h3>{slot.label}</h3></div>
        <p className={`state-chip state-${slot.responseState}`}>{slot.responseStateLabel}</p>
        {slot.displayValues.length > 0 && <ul>{slot.displayValues.map((value, index) => <li key={`${slot.slotId}-${index}`}>{value}</li>)}</ul>}
        <p className="slot-status">상태: {slot.statusLabel}</p>
        {(slot.sources.length > 0 || slot.issueIds.length > 0) && <details><summary>입력 출처와 상세 상태</summary><p>출처: {slot.sources.join(", ") || "없음"}</p>{slot.issueIds.length > 0 && <p>확인 항목: {slot.issueIds.join(", ")}</p>}</details>}
      </article>)}</div>
    </section>

    <section className="card" aria-labelledby="claims-title">
      <h2 id="claims-title">핵심 점검 결과</h2>
      {view.claims.length ? view.claims.map((claim) => <article className="claim-card" key={claim.claimId}>
        <div className={`verdict verdict-${claim.evaluation?.verdict ?? "context"}`}><span aria-hidden="true">{claim.verdictSymbol}</span><strong>{claim.verdictLabel}</strong></div>
        <p className="claim-proposition">{claim.proposition}</p>
        <p className="claim-explanation">{claim.explanation}</p>
        <p className="claim-slot">관련 항목: {claim.slotLabel}</p>
        <details><summary>판단 근거 상세</summary>
          <dl className="detail-list"><div><dt>검증 대상</dt><dd>{claim.verifiable ? "예" : "아니요"}</dd></div><div><dt>입력 출처</dt><dd>{claim.origin}</dd></div><div><dt>Claim ID</dt><dd><code>{claim.claimId}</code></dd></div></dl>
          {claim.evaluation && <>
            {claim.missingDimensionLabels.length > 0 && <div className="detail-block"><strong>추가 확인이 필요한 정보</strong><ul>{claim.missingDimensionLabels.map((slot) => <li key={slot}>{slot}</li>)}</ul></div>}
            {claim.limitationLabel && <div className="detail-block"><strong>검토 범위</strong><p>{claim.limitationLabel}</p></div>}
            {claim.stanceSummary.length > 0 && <div className="detail-block"><strong>{claim.limitationKind === "source_limited" ? "참고자료 방향" : "근거 방향"}</strong><ul>{claim.stanceSummary.map((item) => <li key={item.key}>{item.label} {item.count}건</li>)}</ul></div>}
            {claim.uncertaintyLabels.length > 0 && <div className="detail-block"><strong>불확실성</strong><ul>{claim.uncertaintyLabels.map((label, index) => <li key={`${label}-${index}`}>{label} <code>{claim.evaluation!.uncertaintyCodes[index]}</code></li>)}</ul></div>}
            {claim.bucketEvidence && Object.entries(claim.bucketEvidence).map(([bucket, items]) => items.length > 0 && <div className="detail-block" key={bucket}><strong>{bucketLabels[bucket as keyof typeof bucketLabels]}</strong><ul>{items.map((item) => item && <li key={item.evidenceId}><a href={`#evidence-${item.evidenceId}`}>{item.publisher || sourceLabels[item.sourceType] || item.source}</a></li>)}</ul></div>)}
          </>}
        </details>
      </article>) : <p>표시할 주장이 없습니다.</p>}
    </section>

    <section className="card" aria-labelledby="evidence-title">
      <h2 id="evidence-title">확인한 근거</h2>
      <p className="evidence-intro">자료원을 나누어 확인할 수 있습니다. 근거의 역할과 판단 연결은 원문 그대로 유지됩니다.</p>
      <div className="evidence-layer-list">
        <section className="evidence-layer" aria-labelledby="claim-evidence-title">
          <h3 id="claim-evidence-title">판단 검증 근거</h3>
          <p>내 Claim과 직접 연결된 자료입니다.</p>
          <EvidenceSourceToggles groups={view.claimEvidenceGroups} emptyMessage="Claim과 직접 연결된 근거가 없습니다." />
        </section>
        <section className="evidence-layer" aria-labelledby="context-evidence-title">
          <h3 id="context-evidence-title">종목 참고 자료</h3>
          <p>종목 전체 맥락을 보기 위해 별도로 수집한 자료입니다.</p>
          <EvidenceSourceToggles groups={view.contextEvidenceGroups} emptyMessage="종목 참고 자료가 없습니다." />
        </section>
      </div>
    </section>

    {view.numericChecks.length > 0 && <section className="card" aria-labelledby="numeric-title"><h2 id="numeric-title">수치 확인</h2><div className="table-scroll"><table><thead><tr><th>지표</th><th>기간</th><th>주장 값</th><th>확인 값</th><th>결과</th></tr></thead><tbody>{view.numericChecks.map((check, index) => <tr key={`${check.claimId}-${index}`}><td>{check.metric}</td><td>{check.period || "-"}</td><td>{check.claimed}</td><td>{check.observed ?? "자료 없음"}{check.observed !== null && check.unit ? ` ${check.unit}` : ""}</td><td>{check.resultLabel}</td></tr>)}</tbody></table></div></section>}

    {(view.claims.some((claim) => claim.missingDimensions.length || claim.uncertaintyLabels.length || claim.limitationLabel) || view.providerCollections.length > 0) && <section className="card" aria-labelledby="limits-title"><h2 id="limits-title">자료 범위와 불확실성</h2>
      {view.claims.map((claim) => (claim.missingDimensions.length > 0 || claim.uncertaintyLabels.length > 0 || claim.limitationLabel) && <article className="limit-item" key={claim.claimId}><strong>{claim.proposition}</strong>{claim.missingDimensionLabels.length > 0 && <p>추가 확인이 필요한 정보: {claim.missingDimensionLabels.join(", ")}</p>}{claim.limitationLabel && <p>{claim.limitationLabel}</p>}{claim.uncertaintyLabels.map((label, index) => <p key={`${label}-${index}`}>{label}</p>)}</article>)}
      {view.providerCollections.length > 0 && <details><summary>자료원별 수집 상태</summary><div className="provider-grid">{view.providerCollections.map((provider) => <article key={provider.key}><h3>{sourceLabels[provider.source] || provider.source}</h3><strong>{provider.statusLabel}</strong>{provider.reasonLabel && <p>{provider.reasonLabel}</p>}<p>요청 {provider.queriesRun}회 · 채택 {provider.itemsAdopted}건</p>{provider.reasonCode && <code>{provider.reasonCode}</code>}</article>)}</div></details>}
    </section>}

    {view.findings.length > 0 && <section className="card" aria-labelledby="findings-title"><h2 id="findings-title">다시 확인할 지점</h2>{view.findings.map((finding) => <article className="finding-card" key={finding.findingId}><strong>{finding.kindLabel}</strong><p>{finding.message}</p>{finding.citations.length > 0 && <ul>{finding.citations.map((citation, index) => <li key={`${citation.evidenceId}-${index}`}><a href={`#evidence-${citation.evidenceId}`}>{citation.span}</a></li>)}</ul>}</article>)}</section>}

    {view.opposingSearch && <section className="card opposing-search" aria-labelledby="opposing-title"><h2 id="opposing-title">반대 방향 근거 확인</h2><p><strong>{view.opposingSearch.statusLabel}</strong></p>{view.opposingSearch.count !== null && <p>확인된 항목: {view.opposingSearch.count}건</p>}{view.opposingSearch.queries && view.opposingSearch.queries.length > 0 && <details><summary>검색 범위 보기</summary><ul>{view.opposingSearch.queries.map((query) => <li key={query}>{query}</li>)}</ul></details>}{view.opposingSearch.reasonLabel && <p className="muted">{view.opposingSearch.reasonLabel}</p>}</section>}

    <section className="card final-report" aria-labelledby="report-title"><h2 id="report-title">최종 검토 보고서</h2>
      {view.report.slots.length > 0 ? view.report.slots.map((slot) => <article className="report-slot" key={slot.slotNo}><h3>{slot.slotNo}. {slot.label}</h3><p>{slot.text}</p>{slot.citations.length > 0 && <div className="report-citations"><strong>연결된 근거</strong><ul>{slot.citations.map((citation, index) => <li key={`${citation.evidenceId}-${index}`}><a href={`#evidence-${citation.evidenceId}`}>{citation.publisher || citation.evidence?.publisher || citation.span}</a>{citation.safeUrl && <> · <a href={citation.safeUrl} target="_blank" rel="noopener noreferrer">원문</a></>}</li>)}</ul></div>}</article>) : <p className="summary">{view.finalSummaryFallback}</p>}
      <p className="report-date">생성 시각: <time dateTime={view.report.createdAt}>{dateLabel(view.report.createdAt)}</time></p>
      {view.theoryNotes.length > 0 && <details className="theory-notes"><summary>이론 설명 보기</summary>{view.theoryNotes.map((note) => <article key={note.theory_id}><h3>{note.name}</h3><p>{note.definition}</p><p>{note.observable_pattern}</p><p className="muted">{note.non_diagnostic_warning}</p></article>)}</details>}
    </section>
    <details className="card technical-details"><summary>기술 상세</summary><p>정규화된 제한 코드: {view.banners.map((banner) => banner.code).join(", ") || "없음"}</p><p>불확실성 코드: {view.claims.flatMap((claim) => claim.evaluation?.uncertaintyCodes ?? []).join(", ") || "없음"}</p><p>자료원 상태 코드: {view.providerCollections.map((provider) => provider.reasonCode).filter(Boolean).join(", ") || "없음"}</p></details>
  </section>;
}
