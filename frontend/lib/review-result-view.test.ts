import assert from "node:assert/strict";
import test from "node:test";

import { buildReviewResultView } from "./review-result-view.ts";
import type { ClaimVerdict, ReviewResult } from "./types.ts";

const verdicts: ClaimVerdict[] = ["support", "partial_support", "unsupported", "contradicted", "unverifiable"];

function resultFixture(): ReviewResult {
  const evidenceId = "01ARZ3NDEKTSV4RRFFQ69G5FAV";
  return {
    stock: { code: "005930", name: "삼성전자", market: "KOSPI" },
    judgmentSlots: Array.from({ length: 8 }, (_, index) => ({
      slotId: index + 1,
      status: index === 6 ? "ABSENT" : "RESOLVED",
      responseState: index === 5 ? "unknown" : index === 6 ? "undecided" : index === 7 ? "user_declined" : "answered",
      observationIds: [],
      values: index < 5 ? [`slot-${index + 1}`] : [],
      issueIds: [],
      sources: index < 5 ? ["survey"] : [],
    })),
    claims: [...verdicts.map((verdict, index) => ({
      claimId: `claim-${index}`,
      slotId: 4,
      proposition: `claim ${verdict}`,
      verifiable: true,
      origin: "survey" as const,
      supersededBy: null,
      evaluation: {
        claimEvaluationId: `evaluation-${index}`,
        claimId: `claim-${index}`,
        verdict,
        supportEvidenceIds: verdict === "support" ? [evidenceId] : [],
        opposeEvidenceIds: verdict === "contradicted" ? [evidenceId] : [],
        neutralEvidenceIds: [],
        unknownEvidenceIds: [],
        citations: [],
        numericChecks: index === 0 ? [{
          metric: "영업이익", claimed: "증가", observed: 12, unit: "%", period: "2025",
          result: "consistent" as const, evidenceId, computedBy: "rule" as const,
        }] : [],
        missingDimensions: index === 2 ? [6] : [],
        uncertaintyCodes: index === 2 ? ["coverage_truncated"] : [],
        createdAt: "2026-08-24T00:00:00+00:00",
      },
    })), {
      claimId: "claim-context", slotId: 4, proposition: "장기적으로 기대합니다", verifiable: false,
      origin: "chat_explicit" as const, supersededBy: null, evaluation: null,
    }],
    evidence: [{
      evidenceId, sourceType: "dart", sourceRef: "ref", publisher: "금융감독원",
      publishedAt: "2026-08-23T00:00:00+00:00", sourceUrl: "https://dart.fss.or.kr/example",
      rawSpan: "공식 근거", spanScope: "structured_field", relatedQueryIds: ["query-1"],
      relatedClaimIds: ["claim-0", "claim-3"], roles: ["PRIMARY", "CORROBORATIVE"],
      stances: [
        { claimId: "claim-0", stance: "support", stanceSource: "rule", queryId: "query-1" },
        { claimId: "claim-3", stance: "oppose", stanceSource: "llm", queryId: "query-1" },
        { claimId: "claim-2", stance: "neutral", stanceSource: "llm", queryId: null },
        { claimId: "claim-4", stance: "unknown", stanceSource: "llm", queryId: null },
      ], source: "DART", excerpt: "공식 근거", url: "https://dart.fss.or.kr/example",
    }],
    findings: [{
      findingId: "finding-1", slotId: 4, kind: "mismatch",
      citations: [{ evidenceId, span: "공식 근거" }], claimEvaluationId: "evaluation-0",
      createdAt: "2026-08-24T00:00:00+00:00",
    }],
    opposingSearch: { status: "unverified", count: null, queries: ["반대 근거"], reason: "source_unavailable" },
    providerCollections: {
      dart: { source: "dart", status: "PARTIAL", reasonCode: "coverage_truncated", itemsFetched: 2, itemsAdopted: 1, itemsDeduped: 1, queriesRun: 1 },
    },
    report: {
      schemaVersion: "s0.v1",
      renderedSlots: [
        { slotNo: 1, text: "첫 번째 검토", citations: [{ evidenceId, span: "공식 근거" }] },
        { slotNo: 2, text: "두 번째 검토", citations: [] },
      ],
      banners: ["coverage_truncated", "source_unavailable"],
      theoryNotes: [{
        theory_id: "theory-1", trigger: [4, "partial"], name: "확증 편향", definition: "정의",
        observable_pattern: "관찰 패턴", non_diagnostic_warning: "진단이 아님", source_refs: ["ref-1"],
      }],
      citations: [{ evidenceId, span: "공식 근거", sourceUrl: "https://dart.fss.or.kr/example", publisher: "금융감독원" }],
      createdAt: "2026-08-24T00:00:00+00:00",
    },
    finalSummary: "legacy fallback", banners: ["coverage_truncated", "source_unavailable"], degraded: true,
    judgmentContext: {},
  };
}

test("five verdicts have independent Korean meanings and contradicted is not partial support", () => {
  const view = buildReviewResultView(resultFixture());
  assert.deepEqual(view.claims.slice(0, 5).map((claim) => claim.verdictLabel), [
    "근거로 확인됨", "일부만 확인됨", "뒷받침 근거를 확인하지 못함", "반대되는 근거가 확인됨", "현재 자료로 확인하기 어려움",
  ]);
  assert.notEqual(view.claims[3].verdictLabel, view.claims[1].verdictLabel);
});

test("all eight slots render and non-answer states are meaningful rather than blank", () => {
  const view = buildReviewResultView(resultFixture());
  assert.deepEqual(view.slots.map((slot) => slot.label), [
    "현재 판단", "보유 상태", "투자 관점", "주요 판단 근거", "기대 결과", "확인한 정보", "반대 근거·우려", "판단을 바꾸는 조건",
  ]);
  assert.equal(view.slots[5].responseStateLabel, "잘 모르겠음");
  assert.equal(view.slots[6].responseStateLabel, "아직 정하지 않음");
  assert.equal(view.slots[7].responseStateLabel, "답변하지 않음");
});

test("non-verifiable claim remains visible without being called unsupported", () => {
  const claim = buildReviewResultView(resultFixture()).claims.at(-1)!;
  assert.equal(claim.proposition, "장기적으로 기대합니다");
  assert.equal(claim.verdictLabel, "외부 자료로 직접 검증하는 주장으로 분류되지 않았습니다.");
  assert.notEqual(claim.verdictLabel, "뒷받침 근거를 확인하지 못함");
});

test("evidence keeps canonical roles and all four stances with only safe source links", () => {
  const view = buildReviewResultView(resultFixture());
  assert.deepEqual(view.evidence[0].roleLabels, ["핵심 검증 근거", "보조·맥락 근거"]);
  assert.deepEqual(view.evidence[0].stances.map((stance) => stance.label), [
    "주장을 뒷받침", "주장과 반대", "관련 있으나 방향성 없음", "이 근거만으로 판단 어려움",
  ]);
  assert.equal(view.evidence[0].safeUrl, "https://dart.fss.or.kr/example");
  const unsafe = resultFixture();
  unsafe.evidence[0].sourceUrl = "javascript:alert(1)";
  unsafe.evidence[0].url = "javascript:alert(1)";
  assert.equal(buildReviewResultView(unsafe).evidence[0].safeUrl, null);
});

test("numeric, findings, uncertainty and provider limitations are exposed only from canonical data", () => {
  const view = buildReviewResultView(resultFixture());
  assert.equal(view.numericChecks.length, 1);
  assert.equal(view.findings[0].kindLabel, "내용 불일치");
  assert.deepEqual(view.claims[2].missingDimensions, [6]);
  assert.deepEqual(view.claims[2].uncertaintyLabels, ["일부 근거만 검토되어 판단 범위가 제한되었습니다."]);
  assert.equal(view.providerCollections[0].statusLabel, "일부 자료만 수집됨");
  const empty = resultFixture();
  empty.claims.forEach((claim) => { if (claim.evaluation) claim.evaluation.numericChecks = []; });
  empty.findings = [];
  assert.equal(buildReviewResultView(empty).numericChecks.length, 0);
  assert.equal(buildReviewResultView(empty).findings.length, 0);
});

test("raw limitation codes are not used as normal user-facing wording", () => {
  const view = buildReviewResultView(resultFixture());
  assert.equal(view.banners[0].label, "일부 근거만 검토되어 판단 범위가 제한되었습니다.");
  assert.equal(view.banners[1].label, "일부 자료원을 사용할 수 없어 검토 범위가 제한되었습니다.");
  assert.ok(!view.banners[0].label.includes("coverage_truncated"));
  assert.ok(!view.claims[2].uncertaintyLabels[0].includes("coverage_truncated"));
});

test("findings resolve to actionable Korean text and fall back to slot wording", () => {
  const view = buildReviewResultView(resultFixture());
  assert.match(view.findings[0].message, /claim support/);
  const fallback = resultFixture();
  fallback.findings = [{ ...fallback.findings[0], kind: "unverified", claimEvaluationId: null }];
  assert.equal(buildReviewResultView(fallback).findings[0].message, "주요 판단 근거에 필요한 자료를 충분히 확인하지 못했습니다.");
});

test("summary counts are deterministic and do not become investment advice", () => {
  const view = buildReviewResultView(resultFixture());
  assert.deepEqual(view.summaryCounts, [
    { key: "confirmed", label: "확인됨", count: 1 },
    { key: "partial", label: "일부 확인", count: 1 },
    { key: "difficult", label: "확인 어려움", count: 2 },
    { key: "needs_review", label: "다시 확인", count: 1 },
  ]);
  assert.ok(!JSON.stringify(view).match(/매수|매도|추천/));
});

test("report slots expose canonical slot labels instead of generic review item text", () => {
  const view = buildReviewResultView(resultFixture());
  assert.equal(view.report.slots[0].label, "현재 판단");
  assert.notEqual(view.report.slots[0].label, "검토 항목");
});

test("opposing search unverified never claims that opposing evidence is absent", () => {
  const view = buildReviewResultView(resultFixture());
  assert.equal(view.opposingSearch?.statusLabel, "반대 방향 근거 확인이 제한되었습니다.");
  assert.notEqual(view.opposingSearch?.statusLabel, "반대 근거가 없습니다.");
  const verified = resultFixture();
  verified.opposingSearch = { status: "verified", count: 2, queries: ["query"], reason: null };
  assert.equal(buildReviewResultView(verified).opposingSearch?.statusLabel, "반대 방향 근거도 별도로 확인했습니다.");
  verified.opposingSearch = null;
  assert.equal(buildReviewResultView(verified).opposingSearch, null);
});

test("each banner and structured report slot remains separate with citation lineage and creation time", () => {
  const view = buildReviewResultView(resultFixture());
  assert.deepEqual(view.banners.map((banner) => banner.code), ["coverage_truncated", "source_unavailable"]);
  assert.deepEqual(view.report.slots.map((slot) => slot.text), ["첫 번째 검토", "두 번째 검토"]);
  assert.equal(view.report.slots[0].citations[0].evidenceId, "01ARZ3NDEKTSV4RRFFQ69G5FAV");
  assert.equal(view.report.createdAt, "2026-08-24T00:00:00+00:00");
  assert.equal(view.theoryNotes.length, 1);
});

test("empty, context-only and degraded results remain renderable without empty optional panels", () => {
  const empty = resultFixture();
  empty.evidence = [];
  empty.findings = [];
  empty.opposingSearch = null;
  empty.report.theoryNotes = [];
  empty.claims.forEach((claim) => { if (claim.evaluation) claim.evaluation.numericChecks = []; });
  const view = buildReviewResultView(empty);
  assert.deepEqual(view.evidence, []);
  assert.deepEqual(view.numericChecks, []);
  assert.deepEqual(view.findings, []);
  assert.equal(view.opposingSearch, null);
  assert.deepEqual(view.theoryNotes, []);
  assert.equal(view.degraded, true);
  assert.equal(view.report.slots.length, 2);
});
