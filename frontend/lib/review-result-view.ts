import type {
  ClaimVerdict, EvidenceStance, EvidenceView, JudgmentSlotView, ReviewResult,
} from "./types.ts";

const SLOT_LABELS = [
  "현재 판단", "보유 상태", "투자 관점", "주요 판단 근거", "기대 결과",
  "확인한 정보", "반대 근거·우려", "판단을 바꾸는 조건",
] as const;

const RESPONSE_STATE_LABELS = {
  answered: "답변함", unknown: "잘 모르겠음", undecided: "아직 정하지 않음", user_declined: "답변하지 않음",
} as const;

const SLOT_STATUS_LABELS = {
  RESOLVED: "확인됨", ABSENT: "정보 없음", CONFLICT: "서로 다른 내용이 있음", AMBIGUOUS: "추가 확인 필요",
} as const;

const VERDICT_LABELS: Record<ClaimVerdict, string> = {
  support: "근거로 확인됨",
  partial_support: "일부만 확인됨",
  unsupported: "뒷받침 근거를 확인하지 못함",
  contradicted: "반대되는 근거가 확인됨",
  unverifiable: "현재 자료로 확인하기 어려움",
};

const VERDICT_SYMBOLS: Record<ClaimVerdict, string> = {
  support: "✓", partial_support: "◐", unsupported: "–", contradicted: "!", unverifiable: "?",
};

const ROLE_LABELS = { PRIMARY: "핵심 검증 근거", CORROBORATIVE: "보조·맥락 근거" } as const;
const STANCE_LABELS: Record<EvidenceStance, string> = {
  support: "주장을 뒷받침", oppose: "주장과 반대", neutral: "관련 있으나 방향성 없음", unknown: "이 근거만으로 판단 어려움",
};

const FINDING_LABELS = {
  mismatch: "내용 불일치", missing: "확인할 정보 누락", unverified: "검증되지 않음", conflict: "상충하는 내용",
} as const;

const REASON_LABELS: Record<string, string> = {
  coverage_truncated: "근거 범위가 일부 제한됨",
  no_result: "조건에 맞는 자료를 찾지 못함",
  stale_data: "자료의 최신성이 제한됨",
  source_unavailable: "해당 자료원을 사용할 수 없음",
  evidence_insufficient: "판단에 필요한 근거가 충분하지 않음",
};

const BANNER_LABELS: Record<string, string> = {
  coverage_truncated: "수집 가능한 근거 범위가 일부 제한되었습니다.",
  no_result: "일부 항목에서 조건에 맞는 자료를 찾지 못했습니다.",
  stale_data: "일부 자료의 최신성이 제한될 수 있습니다.",
  source_unavailable: "일부 자료원을 사용할 수 없어 검토 범위가 제한되었습니다.",
  evidence_insufficient: "현재 자료만으로 충분히 확인하기 어려운 항목이 있습니다.",
};

const PROVIDER_STATUS_LABELS = {
  OK: "자료 수집 완료", PARTIAL: "일부 자료만 수집됨", MISSING: "자료를 수집하지 못함",
} as const;

const NUMERIC_RESULT_LABELS = {
  consistent: "수치가 일치함", inconsistent: "수치가 일치하지 않음",
  not_comparable: "직접 비교하기 어려움", no_data: "비교할 자료 없음",
} as const;

export function safeHttpUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

function reasonLabel(code: string): string {
  return REASON_LABELS[code] ?? "추가 확인이 필요한 제한 사항";
}

function slotView(slot: JudgmentSlotView) {
  return {
    ...slot,
    label: SLOT_LABELS[slot.slotId - 1] ?? `항목 ${slot.slotId}`,
    responseStateLabel: RESPONSE_STATE_LABELS[slot.responseState],
    statusLabel: SLOT_STATUS_LABELS[slot.status],
    displayValues: slot.values.length ? slot.values : [],
  };
}

function evidenceView(evidence: EvidenceView) {
  return {
    ...evidence,
    safeUrl: safeHttpUrl(evidence.sourceUrl ?? evidence.url),
    roleLabels: evidence.roles.map((role) => ROLE_LABELS[role]),
    stances: evidence.stances.map((stance) => ({ ...stance, label: STANCE_LABELS[stance.stance] })),
  };
}

export function buildReviewResultView(result: ReviewResult) {
  const evidenceById = new Map(result.evidence.map((item) => [item.evidenceId, evidenceView(item)]));
  const claims = result.claims.map((claim) => ({
    ...claim,
    slotLabel: SLOT_LABELS[claim.slotId - 1] ?? `항목 ${claim.slotId}`,
    verdictLabel: claim.evaluation
      ? VERDICT_LABELS[claim.evaluation.verdict]
      : "외부 자료로 직접 검증하는 주장으로 분류되지 않았습니다.",
    verdictSymbol: claim.evaluation ? VERDICT_SYMBOLS[claim.evaluation.verdict] : "○",
    missingDimensions: claim.evaluation?.missingDimensions ?? [],
    uncertaintyLabels: (claim.evaluation?.uncertaintyCodes ?? []).map(reasonLabel),
    bucketEvidence: claim.evaluation ? {
      support: claim.evaluation.supportEvidenceIds.map((id) => evidenceById.get(id)).filter(Boolean),
      oppose: claim.evaluation.opposeEvidenceIds.map((id) => evidenceById.get(id)).filter(Boolean),
      neutral: claim.evaluation.neutralEvidenceIds.map((id) => evidenceById.get(id)).filter(Boolean),
      unknown: claim.evaluation.unknownEvidenceIds.map((id) => evidenceById.get(id)).filter(Boolean),
    } : null,
  }));
  const numericChecks = claims.flatMap((claim) => (claim.evaluation?.numericChecks ?? []).map((check) => ({
    ...check, claimId: claim.claimId, claimProposition: claim.proposition, resultLabel: NUMERIC_RESULT_LABELS[check.result],
  })));
  const citations = new Map(result.report.citations.map((citation) => [citation.evidenceId, {
    ...citation, safeUrl: safeHttpUrl(citation.sourceUrl), evidence: evidenceById.get(citation.evidenceId) ?? null,
  }]));

  return {
    stock: result.stock,
    degraded: result.degraded,
    banners: result.report.banners.map((code) => ({ code, label: BANNER_LABELS[code] ?? reasonLabel(code) })),
    slots: result.judgmentSlots.slice().sort((a, b) => a.slotId - b.slotId).map(slotView),
    claims,
    evidence: result.evidence.map(evidenceView),
    numericChecks,
    findings: result.findings.map((finding) => ({
      ...finding, kindLabel: FINDING_LABELS[finding.kind], slotLabel: SLOT_LABELS[finding.slotId - 1] ?? `항목 ${finding.slotId}`,
      citations: finding.citations.map((citation) => ({ ...citation, evidence: evidenceById.get(citation.evidenceId) ?? null })),
    })),
    opposingSearch: result.opposingSearch ? {
      ...result.opposingSearch,
      statusLabel: result.opposingSearch.status === "verified"
        ? "반대 방향 근거도 별도로 확인했습니다."
        : "반대 방향 근거 확인이 제한되었습니다.",
    } : null,
    providerCollections: Object.entries(result.providerCollections).map(([key, collection]) => ({
      key, ...collection, statusLabel: PROVIDER_STATUS_LABELS[collection.status],
      reasonLabel: collection.reasonCode ? reasonLabel(collection.reasonCode) : null,
    })),
    report: {
      ...result.report,
      slots: result.report.renderedSlots.map((slot) => ({
        ...slot,
        citations: slot.citations.map((citation) => citations.get(citation.evidenceId) ?? {
          ...citation, sourceUrl: null, publisher: null, safeUrl: null, evidence: null,
        }),
      })),
    },
    theoryNotes: result.report.theoryNotes,
    finalSummaryFallback: result.finalSummary,
  };
}
