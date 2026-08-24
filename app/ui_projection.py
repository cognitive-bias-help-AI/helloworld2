"""Allowlisted, read-only projection of canonical review artifacts for the UI."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.orchestration.intake_review_runtime import load_current_slot_projections
from app.orchestration.reporting import ReportArtifact
from app.schemas.frozen import CollectionResult, OpposeBlock, ReasonCode


def _citation(item: Any) -> dict[str, Any]:
    return {"evidenceId": str(item.evidence_id), "span": item.span}


def _numeric_check(item: Any) -> dict[str, Any]:
    return {
        "metric": item.metric,
        "claimed": item.claimed,
        "observed": item.observed,
        "unit": item.unit,
        "period": item.period,
        "result": item.result,
        "evidenceId": str(item.evidence_id),
        "computedBy": item.computed_by,
    }


def _evaluation(item: Any) -> dict[str, Any]:
    return {
        "claimEvaluationId": str(item.claim_evaluation_id),
        "claimId": str(item.claim_id),
        "verdict": item.verdict,
        "supportEvidenceIds": [str(value) for value in item.support_evidence_ids],
        "opposeEvidenceIds": [str(value) for value in item.oppose_evidence_ids],
        "neutralEvidenceIds": [str(value) for value in item.neutral_evidence_ids],
        "unknownEvidenceIds": [str(value) for value in item.unknown_evidence_ids],
        "citations": [_citation(value) for value in item.citations],
        "numericChecks": [_numeric_check(value) for value in item.numeric_checks],
        "missingDimensions": list(item.missing_dimensions),
        "uncertaintyCodes": [value.value for value in item.uncertainty_codes],
        "createdAt": item.created_at.isoformat(),
    }


def _report(report: ReportArtifact) -> dict[str, Any]:
    return {
        "schemaVersion": report.schema_version,
        "renderedSlots": [
            {
                "slotNo": item.slot_no,
                "text": item.text,
                "citations": [_citation(value) for value in item.citations],
            }
            for item in report.rendered_slots
        ],
        "banners": list(report.banners),
        "theoryNotes": [item.model_dump(mode="json") for item in report.theory_notes],
        "citations": [
            {
                "evidenceId": str(item.evidence_id),
                "span": item.span,
                "sourceUrl": item.source_url,
                "publisher": item.publisher,
            }
            for item in report.citations
        ],
        "createdAt": report.created_at.isoformat(),
    }


def safe_terminal_view(state: dict[str, Any]) -> dict[str, str] | None:
    """Return a public terminal category only for a validated canonical block reason."""

    for item in reversed(state.get("node_results", [])):
        if not isinstance(item, str) or ":block:" not in item:
            continue
        raw_reason = item.split(":block:", 1)[1]
        try:
            reason = ReasonCode(raw_reason)
        except ValueError:
            return None
        return {
            "kind": "terminal",
            "reasonCode": reason.value,
            "message": "입력 내용을 안전하게 처리할 수 없어 검토를 종료했습니다.",
        }
    return None


async def build_ui_result(runtime: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Join persisted canonical artifacts without creating semantic conclusions."""

    review_store = runtime.deps.review_store
    evidence_store = runtime.deps.evidence_store
    report_id = state.get("report_id")
    if not report_id:
        raise RuntimeError("published report is missing")
    report_body = await review_store.get_report(report_id)
    if report_body is None:
        raise RuntimeError("published report is missing")
    report = ReportArtifact.model_validate(report_body)

    claims = await review_store.get_claims(list(state.get("claim_ids", [])))
    evaluations = await review_store.get_claim_evaluations(
        list(state.get("claim_evaluation_ids", []))
    )
    evaluations_by_claim = {str(item.claim_id): item for item in evaluations}
    claim_views = [
        {
            "claimId": str(claim.claim_id),
            "slotId": claim.slot_id,
            "proposition": claim.normalized_proposition,
            "verifiable": claim.verifiable,
            "origin": claim.origin.value,
            "supersededBy": str(claim.superseded_by) if claim.superseded_by else None,
            "evaluation": (
                _evaluation(evaluations_by_claim[str(claim.claim_id)])
                if str(claim.claim_id) in evaluations_by_claim
                else None
            ),
        }
        for claim in claims
    ]

    queries = await evidence_store.get_queries(list(state.get("query_ids", [])))
    query_by_id = {str(item.query_id): item for item in queries}
    query_ids_by_evidence: dict[str, set[str]] = defaultdict(set)
    for query in queries:
        for evidence_id in await evidence_store.evidence_ids_for_queries([query.query_id]):
            query_ids_by_evidence[str(evidence_id)].add(str(query.query_id))

    evidence_ids = sorted(query_ids_by_evidence)
    evidence = await evidence_store.get_many(evidence_ids)
    stances_by_evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        for stance in await review_store.get_claim_evidence(state["run_id"], claim.claim_id):
            stances_by_evidence[str(stance.evidence_id)].append(
                {
                    "claimId": str(stance.claim_id),
                    "stance": stance.stance,
                    "stanceSource": stance.stance_source,
                    "queryId": str(stance.query_id) if stance.query_id else None,
                }
            )

    evidence_views = []
    for item in evidence:
        related_query_ids = sorted(query_ids_by_evidence[str(item.evidence_id)])
        related_queries = [query_by_id[value] for value in related_query_ids]
        related_claim_ids = sorted(
            {str(query.claim_id) for query in related_queries if query.claim_id is not None}
        )
        roles = sorted(
            {
                "PRIMARY" if query.intent in {"verify", "counter"} else "CORROBORATIVE"
                for query in related_queries
                if query.scope == "claim"
            }
        )
        evidence_views.append(
            {
                "evidenceId": str(item.evidence_id),
                "sourceType": item.source_type,
                "sourceRef": item.source_ref,
                "publisher": item.publisher,
                "publishedAt": item.published_at.isoformat() if item.published_at else None,
                "sourceUrl": item.source_url,
                "rawSpan": item.raw_span,
                "spanScope": item.span_scope,
                "relatedQueryIds": related_query_ids,
                "relatedClaimIds": related_claim_ids,
                "roles": roles,
                "stances": sorted(
                    stances_by_evidence[str(item.evidence_id)],
                    key=lambda value: (value["claimId"], value["stance"]),
                ),
                # Temporary compatibility fields for the existing Phase 1 page.
                "source": item.publisher or item.source_type.upper(),
                "excerpt": item.raw_span,
                "url": item.source_url,
            }
        )

    findings = await review_store.get_findings(list(state.get("finding_ids", [])))
    finding_views = [
        {
            "findingId": str(item.finding_id),
            "slotId": item.slot_id,
            "kind": item.kind,
            "citations": [_citation(value) for value in item.citations],
            "claimEvaluationId": (
                str(item.claim_evaluation_id) if item.claim_evaluation_id else None
            ),
            "createdAt": item.created_at.isoformat(),
        }
        for item in findings
    ]

    oppose_body = state.get("oppose")
    opposing_search = None
    if oppose_body is not None:
        oppose = OpposeBlock.model_validate(oppose_body)
        opposing_search = {
            "status": oppose.status,
            "count": oppose.count,
            "queries": oppose.queries,
            "reason": oppose.reason.value if oppose.reason else None,
        }

    provider_collections = {}
    for provider, body in state.get("collections", {}).items():
        collection = CollectionResult.model_validate(body)
        provider_collections[provider] = {
            "source": collection.source,
            "status": collection.status.value,
            "reasonCode": collection.reason_code.value if collection.reason_code else None,
            "itemsFetched": collection.items_fetched,
            "itemsAdopted": collection.items_adopted,
            "itemsDeduped": collection.items_deduped,
            "queriesRun": collection.queries_run,
        }

    projections = await load_current_slot_projections(
        state["run_id"],
        input_id=state.get("input_id"),
        review_store=review_store,
    )
    observations = await review_store.get_slot_observations(state["run_id"])
    observation_by_id = {str(item.observation_id): item for item in observations}
    judgment_slots = []
    for projection in projections:
        sources = sorted(
            {
                observation_by_id[value].origin.value
                for value in projection.observation_ids
                if value in observation_by_id
            }
        )
        judgment_slots.append(
            {
                "slotId": projection.slot_id,
                "status": projection.status.value,
                "responseState": projection.response_state.value,
                "observationIds": list(projection.observation_ids),
                "values": list(projection.values),
                "issueIds": list(projection.issue_ids),
                "sources": sources,
            }
        )

    report_view = _report(report)
    stock = state.get("stock") or {}
    return {
        "stock": {
            "code": stock.get("code"),
            "name": stock.get("name"),
            "market": stock.get("market"),
        },
        "judgmentSlots": judgment_slots,
        "claims": claim_views,
        "evidence": evidence_views,
        "findings": finding_views,
        "opposingSearch": opposing_search,
        "providerCollections": provider_collections,
        "report": report_view,
        "finalSummary": "\n\n".join(item.text for item in report.rendered_slots),
        "banners": list(report.banners),
        "degraded": bool(report.banners),
    }


__all__ = ["build_ui_result", "safe_terminal_view"]
