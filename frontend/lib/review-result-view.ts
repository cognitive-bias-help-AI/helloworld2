import type {
  ClaimVerdict, ClaimView, EvidenceStance, EvidenceView, JudgmentSlotView, ProviderCollectionView, ReviewResult,
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
  coverage_truncated: "일부 근거만 검토되어 판단 범위가 제한되었습니다.",
  no_result: "조건에 맞는 자료를 찾지 못했습니다.",
  stale_data: "일부 자료의 최신성이 제한될 수 있습니다.",
  source_unavailable: "일부 자료원을 사용할 수 없어 검토 범위가 제한되었습니다.",
  evidence_insufficient: "현재 확보된 자료만으로 충분히 확인하기 어렵습니다.",
};

const LIMITATION_LABELS = {
  source_limited: "관련 자료는 확인되었으나 직접 검증 범위가 제한됩니다.",
  no_result: "현재 검색 조건에서 이 판단을 확인할 자료를 충분히 찾지 못했습니다.",
  provider_failure: "필요한 자료원에서 데이터를 불러오지 못해 검토가 제한되었습니다.",
  expectation: "미래에 대한 기대이므로 직접 사실 확인 대상이 아닙니다. 현재 근거의 범위만 점검합니다.",
} as const;

const BANNER_LABELS: Record<string, string> = {
  coverage_truncated: "일부 근거만 검토되어 판단 범위가 제한되었습니다.",
  no_result: "조건에 맞는 자료를 찾지 못했습니다.",
  stale_data: "일부 자료의 최신성이 제한될 수 있습니다.",
  source_unavailable: "일부 자료원을 사용할 수 없어 검토 범위가 제한되었습니다.",
  evidence_insufficient: "현재 확보된 자료만으로 충분히 확인하기 어렵습니다.",
};

const VERDICT_EXPLANATIONS: Record<ClaimVerdict, string> = {
  support: "현재 확보한 자료가 이 판단을 뒷받침합니다.",
  partial_support: "일부 근거는 확인되지만 모든 내용을 뒷받침하지는 않습니다.",
  unsupported: "현재 확보한 자료만으로는 이 판단을 충분히 확인하기 어렵습니다.",
  contradicted: "현재 확인된 근거와 이 판단 사이에 차이가 있습니다.",
  unverifiable: "현재 확보한 자료만으로는 이 판단을 충분히 확인하기 어렵습니다.",
};

const SUMMARY_BUCKETS = [
  ["confirmed", "확인됨", ["support"]],
  ["partial", "일부 확인", ["partial_support"]],
  ["difficult", "확인 어려움", ["unsupported", "unverifiable"]],
  ["needs_review", "다시 확인", ["contradicted"]],
] as const;

const PROVIDER_STATUS_LABELS = {
  OK: "자료 수집 완료", PARTIAL: "일부 자료만 수집됨", MISSING: "자료를 수집하지 못함",
} as const;

const EVIDENCE_GROUPS = [
  { sourceType: "news", label: "검색", description: "NAVER 뉴스" },
  { sourceType: "dart", label: "DART", description: "공시·재무" },
  { sourceType: "quote", label: "Kiwoom", description: "주가 이력" },
] as const;

const STANCE_SUMMARY = [
  ["support", "뒷받침 방향", "supportEvidenceIds"],
  ["oppose", "반대 방향", "opposeEvidenceIds"],
  ["neutral", "중립 방향", "neutralEvidenceIds"],
  ["unknown", "판단 어려운 방향", "unknownEvidenceIds"],
] as const;

type ProviderCollectionProjection = ProviderCollectionView & {
  key: string;
  statusLabel: string;
  reasonLabel: string | null;
};

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

function findingMessage(
  kind: "mismatch" | "missing" | "unverified" | "conflict",
  slotLabel: string,
  proposition: string | null,
  limitation: string | null,
): string {
  if (kind === "unverified") {
    return proposition
      ? `${proposition}\n${limitation ?? "현재 확보한 자료만으로는 이 판단 근거를 충분히 확인하기 어렵습니다."}`
      : `${slotLabel}에 필요한 자료를 충분히 확인하지 못했습니다.`;
  }
  if (kind === "mismatch") {
    return proposition
      ? `${proposition}\n사용자 판단과 확인된 근거 사이에 차이가 있습니다.`
      : `${slotLabel}에서 입력한 판단과 확인된 근거 사이에 차이가 있습니다.`;
  }
  if (kind === "missing") return `${slotLabel}에 필요한 정보가 부족해 충분히 점검하지 못했습니다.`;
  return `${slotLabel}에 서로 다른 정보가 있어 추가 확인이 필요합니다.`;
}

function uniqueStrings(values: string[]): string[] {
  return [...new Set(values)];
}

function isProviderFailure(collection: ProviderCollectionView): boolean {
  return collection.status === "MISSING" || [
    "auth_failed", "ip_mismatch", "rate_limit", "upstream_5xx", "upstream_timeout", "contract_violation",
  ].includes(collection.reasonCode ?? "");
}

function claimLimitation(
  claim: ClaimView,
  evidence: EvidenceView[],
  collections: ProviderCollectionView[],
): keyof typeof LIMITATION_LABELS | null {
  if (!claim.verifiable) return "expectation";
  const claimEvidence = evidence.filter((item) => item.relatedClaimIds.includes(claim.claimId));
  if (claimEvidence.length > 0) {
    const hasPrimary = claimEvidence.some((item) => item.roles.includes("PRIMARY"));
    const hasCorroborative = claimEvidence.some((item) => item.roles.includes("CORROBORATIVE"));
    return !hasPrimary && hasCorroborative ? "source_limited" : null;
  }
  return collections.some(isProviderFailure) ? "provider_failure" : "no_result";
}

function slotView(slot: JudgmentSlotView) {
  return {
    ...slot,
    label: SLOT_LABELS[slot.slotId - 1] ?? `항목 ${slot.slotId}`,
    responseStateLabel: RESPONSE_STATE_LABELS[slot.responseState],
    statusLabel: SLOT_STATUS_LABELS[slot.status],
    displayValues: slot.values.length ? uniqueStrings(slot.values) : [],
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

function groupEvidence(
  evidence: ReturnType<typeof evidenceView>[],
  providerCollections: ProviderCollectionProjection[],
) {
  return EVIDENCE_GROUPS.map((group) => {
    const items = evidence.filter((item) => item.sourceType === group.sourceType);
    const provider = providerCollections.find((collection) => collection.source === group.sourceType);
    return {
      ...group,
      items,
      statusLabel: provider?.statusLabel ?? (items.length > 0 ? "자료 수집 완료" : "표시할 자료 없음"),
      reasonLabel: provider?.reasonLabel ?? null,
    };
  });
}

export function buildReviewResultView(result: ReviewResult) {
  const evidenceById = new Map(result.evidence.map((item) => [item.evidenceId, evidenceView(item)]));
  const evidence = result.evidence.map(evidenceView);
  const providerCollections = Object.entries(result.providerCollections).map(([key, collection]) => ({
    key, ...collection, statusLabel: PROVIDER_STATUS_LABELS[collection.status],
    reasonLabel: collection.reasonCode ? reasonLabel(collection.reasonCode) : null,
  }));
  const claimEvidence = evidence.filter((item) => item.relatedClaimIds.length > 0);
  const contextEvidence = evidence.filter((item) => item.relatedClaimIds.length === 0);
  const evidenceGroups = groupEvidence(evidence, providerCollections);
  const claimEvidenceGroups = groupEvidence(claimEvidence, providerCollections);
  const contextEvidenceGroups = groupEvidence(contextEvidence, providerCollections);
  const claims = result.claims.map((claim) => ({
    ...claim,
    limitationKind: claimLimitation(claim, evidence, providerCollections),
    limitationLabel: (() => {
      const kind = claimLimitation(claim, evidence, providerCollections);
      return kind ? LIMITATION_LABELS[kind] : null;
    })(),
    slotLabel: SLOT_LABELS[claim.slotId - 1] ?? `항목 ${claim.slotId}`,
    verdictLabel: claim.evaluation
      ? VERDICT_LABELS[claim.evaluation.verdict]
      : "외부 자료로 직접 검증하는 주장으로 분류되지 않았습니다.",
    verdictSymbol: claim.evaluation ? VERDICT_SYMBOLS[claim.evaluation.verdict] : "○",
    explanation: (() => {
      const kind = claimLimitation(claim, evidence, providerCollections);
      return kind ? LIMITATION_LABELS[kind] : claim.evaluation
        ? VERDICT_EXPLANATIONS[claim.evaluation.verdict]
        : "외부 자료로 직접 검증하는 주장으로 분류되지 않았습니다.";
    })(),
    missingDimensions: claim.evaluation?.missingDimensions ?? [],
    missingDimensionLabels: (claim.evaluation?.missingDimensions ?? []).map((slot) => SLOT_LABELS[slot - 1] ?? `항목 ${slot}`),
    uncertaintyLabels: (claim.evaluation?.uncertaintyCodes ?? []).map(reasonLabel),
    stanceSummary: claim.evaluation
      ? STANCE_SUMMARY.map(([key, label, field]) => ({
        key,
        label,
        count: claim.evaluation![field].length,
      })).filter((item) => item.count > 0)
      : [],
    bucketEvidence: claim.evaluation ? {
      support: claim.evaluation.supportEvidenceIds.map((id) => evidenceById.get(id)).filter(Boolean),
      oppose: claim.evaluation.opposeEvidenceIds.map((id) => evidenceById.get(id)).filter(Boolean),
      neutral: claim.evaluation.neutralEvidenceIds.map((id) => evidenceById.get(id)).filter(Boolean),
      unknown: claim.evaluation.unknownEvidenceIds.map((id) => evidenceById.get(id)).filter(Boolean),
    } : null,
  }));
  const claimsByEvaluationId = new Map(claims.filter((claim) => claim.evaluation).map((claim) => [claim.evaluation!.claimEvaluationId, claim]));
  const summaryCounts = SUMMARY_BUCKETS.map(([key, label, verdicts]) => ({
    key,
    label,
    count: claims.filter((claim) => claim.evaluation && verdicts.includes(claim.evaluation.verdict as never)).length,
  })).filter((item) => item.count > 0);
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
    summaryCounts,
    evidence,
    evidenceGroups,
    claimEvidence,
    contextEvidence,
    claimEvidenceGroups,
    contextEvidenceGroups,
    numericChecks,
    findings: result.findings.map((finding) => ({
      ...finding, kindLabel: FINDING_LABELS[finding.kind], slotLabel: SLOT_LABELS[finding.slotId - 1] ?? `항목 ${finding.slotId}`,
      message: findingMessage(
        finding.kind,
        SLOT_LABELS[finding.slotId - 1] ?? `항목 ${finding.slotId}`,
        claimsByEvaluationId.get(finding.claimEvaluationId ?? "")?.proposition ?? null,
        claimsByEvaluationId.get(finding.claimEvaluationId ?? "")?.limitationLabel ?? null,
      ),
      citations: finding.citations.map((citation) => ({ ...citation, evidence: evidenceById.get(citation.evidenceId) ?? null })),
    })),
    opposingSearch: result.opposingSearch ? {
      ...result.opposingSearch,
      reasonLabel: result.opposingSearch.reason ? reasonLabel(result.opposingSearch.reason) : null,
      statusLabel: result.opposingSearch.status === "verified"
        ? "반대 방향 근거도 별도로 확인했습니다."
        : "반대 방향 근거 확인이 제한되었습니다.",
    } : null,
    providerCollections,
    report: {
      ...result.report,
      slots: result.report.renderedSlots.map((slot) => ({
        ...slot,
        label: SLOT_LABELS[slot.slotNo - 1] ?? `항목 ${slot.slotNo}`,
        citations: slot.citations.map((citation) => citations.get(citation.evidenceId) ?? {
          ...citation, sourceUrl: null, publisher: null, safeUrl: null, evidence: null,
        }),
      })),
    },
    theoryNotes: result.report.theoryNotes,
    finalSummaryFallback: result.finalSummary,
  };
}
