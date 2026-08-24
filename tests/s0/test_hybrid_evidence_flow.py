from dataclasses import replace

import pytest
from langgraph.types import Command

import app.orchestration.nodes.s0 as nodes_module
from app.contexts.budget import ctx_chars
from app.domain.evidence_requirement import EvidenceCategory
from app.domain.routing import RoutingOutcome
from app.orchestration.drafts import EvidenceIntentDraft, EvidenceRequirementDraft
from app.orchestration.nodes.s0 import make_nodes
from app.schemas.frozen import Usage
from tests.s0.runtime_fixtures import deps
from tests.s0.test_evidence_eligibility import claim, seed_claims


class IntentGateway:
    def __init__(self, drafts=None, error=None):
        self.drafts = list(drafts or [])
        self.error = error
        self.calls = []

    async def invoke(self, slot, prompt_version, input_view, output_schema):
        self.calls.append((slot, prompt_version, input_view, output_schema))
        if self.error is not None:
            raise self.error
        return self.drafts.pop(0), Usage(
            model_slot=slot,
            prompt_tokens=0,
            output_tokens=0,
            ctx_chars=ctx_chars(input_view),
        )


def draft(category, **kwargs):
    return EvidenceIntentDraft(
        requirements=[EvidenceRequirementDraft(category=category, **kwargs)]
    )


@pytest.mark.asyncio
async def test_n5_uses_small_intent_model_and_adds_three_baseline_queries():
    gateway = IntentGateway([
        draft(EvidenceCategory.PRICE_MOVEMENT, topic_terms=["주가"])
    ])
    runtime_deps = replace(deps(), model_gateway=gateway)
    state = await seed_claims(runtime_deps, [
        claim(1, verifiable=True, proposition="최근 주가가 많이 올랐다")
    ])

    patch = await make_nodes(runtime_deps)["n5"](state)
    queries = await runtime_deps.evidence_store.get_queries(patch["query_ids"])

    assert [(call[0], call[1]) for call in gateway.calls] == [("SMALL", "n5/v1")]
    assert {(q.provider, q.endpoint) for q in queries if q.scope == "stock"} == {
        ("dart", "disclosure_list"),
        ("kiwoom", "daily_price_history"),
        ("naver", "news_search"),
    }
    assert any(q.scope == "claim" and q.provider == "kiwoom" and q.intent == "verify" for q in queries)
    assert patch["node_results"] == ["n5:ok"]
    assert patch["counters"] == {"llm_calls": 1}


@pytest.mark.asyncio
async def test_n5_baseline_does_not_mask_missing_primary():
    gateway = IntentGateway([
        draft(EvidenceCategory.FINANCIAL_PERFORMANCE, topic_terms=["영업이익"])
    ])
    runtime_deps = replace(deps(), model_gateway=gateway)
    state = await seed_claims(runtime_deps, [
        claim(1, verifiable=True, proposition="영업이익이 증가했다")
    ])

    patch = await make_nodes(runtime_deps)["n5"](state)
    queries = await runtime_deps.evidence_store.get_queries(patch["query_ids"])

    assert len([q for q in queries if q.scope == "stock"]) == 3
    assert not any(q.scope == "claim" and q.intent in {"verify", "counter"} for q in queries)
    assert patch["node_results"] == ["n5:missing"]


@pytest.mark.asyncio
async def test_n5_model_failure_uses_narrow_legacy_fallback():
    gateway = IntentGateway(error=RuntimeError("offline"))
    runtime_deps = replace(deps(), model_gateway=gateway)
    state = await seed_claims(runtime_deps, [
        claim(1, verifiable=True, proposition="최근 주가가 많이 올랐다")
    ])

    patch = await make_nodes(runtime_deps)["n5"](state)
    queries = await runtime_deps.evidence_store.get_queries(patch["query_ids"])

    assert any(q.scope == "claim" and q.provider == "kiwoom" for q in queries)
    assert patch["node_results"] == ["n5:ok"]


@pytest.mark.asyncio
async def test_n5_zero_claims_still_plans_baseline_without_model_call():
    gateway = IntentGateway([])
    runtime_deps = replace(deps(), model_gateway=gateway)
    state = await seed_claims(runtime_deps, [])

    patch = await make_nodes(runtime_deps)["n5"](state)
    queries = await runtime_deps.evidence_store.get_queries(patch["query_ids"])

    assert len(queries) == 3
    assert all(q.scope == "stock" and q.intent == "context" for q in queries)
    assert gateway.calls == []
    assert patch["node_results"] == ["n5:ok"]


@pytest.mark.asyncio
async def test_context_only_intake_routes_to_n5(monkeypatch):
    runtime_deps = deps()
    state = await seed_claims(runtime_deps, [])
    state["input_id"] = "01ARZ3NDEKTSV4RRFFQ69G0001"

    class Result:
        routing_outcome = RoutingOutcome.CONTEXT_ONLY
        persisted_claim_ids = ()
        question_payload = None

    async def fake_process(*args, **kwargs):
        return Result()

    monkeypatch.setattr(nodes_module, "process_intake_review", fake_process)
    command = await make_nodes(runtime_deps)["intake_review"](state)
    assert isinstance(command, Command)
    assert command.goto == "n5"
