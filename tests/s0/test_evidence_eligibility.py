from __future__ import annotations

import copy

import pytest

import app.orchestration.graph as graph_module
from app.contexts.views import IntegrationView
from app.orchestration.drafts import FindingDraft
from app.orchestration.nodes.s0 import make_nodes
from app.schemas.frozen import (
    Claim,
    ClaimEvaluationDraft,
    Evidence,
    EvidenceQueryLink,
    Query,
    ReasonCode,
    SourceTrace,
    Usage,
)
from tests.s0.runtime_fixtures import NOW, FlowGateway, deps, initial_state


def uid(n: int) -> str:
    return f"01ARZ3NDEKTSV4RRFFQ69G{n:04d}"


def claim(
    n: int,
    *,
    verifiable: bool,
    slot_id: int = 1,
    proposition: str | None = None,
) -> Claim:
    text = proposition or f"claim-{n}"
    return Claim(
        claim_id=uid(9000 + n),
        slot_id=slot_id,
        user_text_span=text,
        span_offset=(0, len(text)),
        normalized_proposition=text,
        verifiable=verifiable,
        origin=SourceTrace.SURVEY,
        created_at=NOW,
    )


def query(n: int, claim_id: str) -> Query:
    return Query(
        query_id=uid(8000 + n),
        scope="claim",
        claim_id=claim_id,
        intent="verify",
        provider="dart",
        endpoint="disclosure",
        params={"stock_code": "005930"},
        created_at=NOW,
    )


def evidence(n: int) -> Evidence:
    return Evidence(
        evidence_id=uid(7000 + n),
        source_type="dart",
        source_ref=f"ref-{n}",
        fetched_at=NOW,
        raw_span=f"evidence-{n}",
        span_scope="structured_field",
        content_sha256=f"{n:064x}",
        provider_request_id=uid(6000 + n),
        as_of=NOW,
    )


async def seed_claims(runtime_deps, claims: list[Claim]) -> dict:
    await runtime_deps.review_store.put_claims("run-s0", claims)
    return initial_state() | {
        "stock": {"code": "005930"},
        "claim_ids": [item.claim_id for item in claims],
    }


async def seed_queries_and_evidence(runtime_deps, pairs):
    queries = [item[1] for item in pairs]
    await runtime_deps.evidence_store.put_queries("run-s0", queries)
    evidences = [item[2] for item in pairs if item[2] is not None]
    if evidences:
        await runtime_deps.evidence_store.put_many("run-s0", evidences)
        await runtime_deps.evidence_store.link(
            [
                EvidenceQueryLink(evidence_id=item[2].evidence_id, query_id=item[1].query_id)
                for item in pairs
                if item[2] is not None
            ]
        )
    return [item.query_id for item in queries]


@pytest.mark.asyncio
async def test_n5_filters_canonical_non_verifiable_claims_before_query_construction():
    runtime_deps = deps()
    a = claim(1, verifiable=True)
    b = claim(2, verifiable=False, proposition="NON_VERIFIABLE_SECRET")
    c = claim(3, verifiable=True)
    state = await seed_claims(runtime_deps, [a, b, c])
    before = copy.deepcopy(await runtime_deps.review_store.get_claims(state["claim_ids"]))

    patch = await make_nodes(runtime_deps)["n5"](state)
    queries = await runtime_deps.evidence_store.get_queries(patch["query_ids"])

    assert [item.claim_id for item in queries] == [a.claim_id, c.claim_id]
    assert all(item.provider == "dart" and item.params == {"stock_code": "005930"} for item in queries)
    assert "NON_VERIFIABLE_SECRET" not in str([item.model_dump() for item in queries])
    assert await runtime_deps.review_store.get_claims(state["claim_ids"]) == before
    assert patch["node_results"] == ["n5:ok"]
    assert runtime_deps.model_gateway.calls == []


@pytest.mark.asyncio
async def test_n5_all_false_returns_empty_query_ids_without_provider_candidate():
    runtime_deps = deps()
    claims = [claim(1, verifiable=False), claim(2, verifiable=False)]
    state = await seed_claims(runtime_deps, claims)

    patch = await make_nodes(runtime_deps)["n5"](state)

    assert patch == {"query_ids": [], "node_results": ["n5:ok"]}
    assert runtime_deps.evidence_store._queries == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["missing", "duplicate"])
async def test_n5_reference_corruption_blocks_without_query_write(corruption):
    runtime_deps = deps()
    item = claim(1, verifiable=True)
    state = await seed_claims(runtime_deps, [item])
    if corruption == "missing":
        state["claim_ids"] = [uid(9999)]
    else:
        state["claim_ids"] = [item.claim_id, item.claim_id]

    patch = await make_nodes(runtime_deps)["n5"](state)

    assert patch == {"node_results": ["n5:block:contract_violation"]}
    assert runtime_deps.evidence_store._queries == {}


@pytest.mark.asyncio
async def test_n7_calls_llm_only_for_evidenced_claims_and_preserves_query_lineage():
    runtime_deps = deps()
    a = claim(1, verifiable=True)
    b = claim(2, verifiable=False)
    c = claim(3, verifiable=True)
    state = await seed_claims(runtime_deps, [a, b, c])
    qa, qc = query(1, a.claim_id), query(3, c.claim_id)
    ea, ec = evidence(1), evidence(3)
    state["query_ids"] = await seed_queries_and_evidence(
        runtime_deps, [(a, qa, ea), (c, qc, ec)]
    )

    patch = await make_nodes(runtime_deps)["n7"](state)

    calls = [view for node, view in runtime_deps.model_gateway.calls if node == "n7"]
    assert [view.claim.claim_id for view in calls] == [a.claim_id, c.claim_id]
    assert patch == {"node_results": ["n7:ok"], "counters": {"llm_calls": 2}}
    assert (await runtime_deps.review_store.get_claim_evidence("run-s0", a.claim_id))[0].query_id == qa.query_id
    assert await runtime_deps.review_store.get_claim_evidence("run-s0", b.claim_id) == []
    assert (await runtime_deps.review_store.get_claim_evidence("run-s0", c.claim_id))[0].query_id == qc.query_id


@pytest.mark.asyncio
async def test_n7_skips_non_verifiable_and_no_evidence_without_counter_patch():
    runtime_deps = deps()
    a = claim(1, verifiable=False)
    b = claim(2, verifiable=True)
    state = await seed_claims(runtime_deps, [a, b])
    qb = query(2, b.claim_id)
    state["query_ids"] = await seed_queries_and_evidence(runtime_deps, [(b, qb, None)])

    patch = await make_nodes(runtime_deps)["n7"](state)

    assert patch == {"node_results": ["n7:ok"]}
    assert runtime_deps.model_gateway.calls == []
    assert await runtime_deps.review_store.get_claim_evidence("run-s0", a.claim_id) == []
    assert await runtime_deps.review_store.get_claim_evidence("run-s0", b.claim_id) == []


class EmptySafeGateway(FlowGateway):
    async def invoke(self, slot, prompt_version, input_view, output_schema):
        if output_schema is ClaimEvaluationDraft and not input_view.evidence:
            self.calls.append(("n8", input_view))
            return ClaimEvaluationDraft(
                citations=[],
                support_evidence_ids=[],
                oppose_evidence_ids=[],
                unknown_evidence_ids=[],
                verdict="unverifiable",
                missing_dimensions=[],
                uncertainty_codes=[],
            ), Usage(model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1)
        if output_schema is FindingDraft and (
            not input_view.evaluations
            or all(not item.citations for item in input_view.evaluations)
        ):
            self.calls.append(("n9", input_view))
            evaluation_id = (
                None
                if not input_view.evaluations
                else input_view.evaluations[0].claim_evaluation_id
            )
            return FindingDraft(
                slot_id=2,
                kind="missing" if evaluation_id is None else "unverified",
                citations=[],
                claim_evaluation_id=evaluation_id,
            ), Usage(model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1)
        return await super().invoke(slot, prompt_version, input_view, output_schema)


@pytest.mark.asyncio
async def test_n8_mixed_claims_skip_non_verifiable_and_rule_fallback_no_evidence():
    runtime_deps = deps(gateway=EmptySafeGateway())
    a = claim(1, verifiable=True)
    b = claim(2, verifiable=False)
    c = claim(3, verifiable=True, slot_id=3)
    state = await seed_claims(runtime_deps, [a, b, c])
    qa, qc, ea = query(1, a.claim_id), query(3, c.claim_id), evidence(1)
    state["query_ids"] = await seed_queries_and_evidence(
        runtime_deps, [(a, qa, ea), (c, qc, None)]
    )
    n7_patch = await make_nodes(runtime_deps)["n7"](state)

    patch = await make_nodes(runtime_deps)["n8"](state | n7_patch)
    evaluations = await runtime_deps.review_store.get_claim_evaluations(
        patch["claim_evaluation_ids"]
    )

    assert [item.claim_id for item in evaluations] == [a.claim_id, c.claim_id]
    assert evaluations[0].verdict == "support"
    assert evaluations[1].verdict == "unverifiable"
    assert evaluations[1].unknown_evidence_ids == []
    assert evaluations[1].uncertainty_codes == [ReasonCode.COVERAGE_TRUNCATED]
    assert [node for node, _ in runtime_deps.model_gateway.calls].count("n8") == 1
    assert patch["counters"] == {"llm_calls": 1}
    assert patch["node_results"] == ["n8:partial"]


class AlwaysIncompleteEvaluation(FlowGateway):
    async def invoke(self, slot, prompt_version, input_view, output_schema):
        if output_schema is ClaimEvaluationDraft:
            self.calls.append(("n8", input_view))
            return ClaimEvaluationDraft(
                citations=[],
                support_evidence_ids=[],
                oppose_evidence_ids=[],
                unknown_evidence_ids=[],
                verdict="unverifiable",
                missing_dimensions=[],
                uncertainty_codes=[],
            ), Usage(model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1)
        return await super().invoke(slot, prompt_version, input_view, output_schema)


@pytest.mark.asyncio
async def test_n8_counter_matches_all_retry_invocations_across_claims():
    gateway = AlwaysIncompleteEvaluation()
    runtime_deps = deps(gateway=gateway)
    a, c = claim(1, verifiable=True), claim(3, verifiable=True)
    state = await seed_claims(runtime_deps, [a, c])
    qa, qc, ea, ec = query(1, a.claim_id), query(3, c.claim_id), evidence(1), evidence(3)
    state["query_ids"] = await seed_queries_and_evidence(
        runtime_deps, [(a, qa, ea), (c, qc, ec)]
    )
    n7_patch = await make_nodes(runtime_deps)["n7"](state)

    patch = await make_nodes(runtime_deps)["n8"](state | n7_patch)

    assert [node for node, _ in gateway.calls].count("n8") == 4
    assert patch["counters"] == {"llm_calls": 4}
    assert patch["node_results"] == ["n8:partial"]


@pytest.mark.asyncio
async def test_n9_mixed_uses_llm_only_for_evidence_backed_and_builds_no_evidence_finding():
    runtime_deps = deps(gateway=EmptySafeGateway())
    a = claim(1, verifiable=True)
    b = claim(2, verifiable=False)
    c = claim(3, verifiable=True, slot_id=3)
    state = await seed_claims(runtime_deps, [a, b, c])
    qa, qc, ea = query(1, a.claim_id), query(3, c.claim_id), evidence(1)
    state["query_ids"] = await seed_queries_and_evidence(
        runtime_deps, [(a, qa, ea), (c, qc, None)]
    )
    n7_patch = await make_nodes(runtime_deps)["n7"](state)
    n8_patch = await make_nodes(runtime_deps)["n8"](state | n7_patch)

    patch = await make_nodes(runtime_deps)["n9"](state | n7_patch | n8_patch)
    findings = await runtime_deps.review_store.get_findings(patch["finding_ids"])

    n9_views = [view for node, view in runtime_deps.model_gateway.calls if node == "n9"]
    assert len(n9_views) == 1 and isinstance(n9_views[0], IntegrationView)
    assert [item.claim_id for item in n9_views[0].evaluations] == [a.claim_id]
    deterministic = next(item for item in findings if item.slot_id == 3)
    c_evaluation = next(
        item
        for item in await runtime_deps.review_store.get_claim_evaluations(
            n8_patch["claim_evaluation_ids"]
        )
        if item.claim_id == c.claim_id
    )
    assert (deterministic.kind, deterministic.citations) == ("unverified", [])
    assert deterministic.claim_evaluation_id == c_evaluation.claim_evaluation_id
    assert patch["node_results"] == ["n9:ok"]
    assert patch["counters"] == {"llm_calls": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["non_verifiable_only", "no_evidence_only"])
async def test_n9_zero_evidence_backed_is_deterministic_terminal(case):
    runtime_deps = deps(gateway=EmptySafeGateway())
    item = claim(1, verifiable=case == "no_evidence_only", slot_id=2)
    state = await seed_claims(runtime_deps, [item])
    if case == "no_evidence_only":
        q = query(1, item.claim_id)
        state["query_ids"] = await seed_queries_and_evidence(runtime_deps, [(item, q, None)])
    n8_patch = await make_nodes(runtime_deps)["n8"](state)

    patch = await make_nodes(runtime_deps)["n9"](state | n8_patch)

    findings = await runtime_deps.review_store.get_findings(patch["finding_ids"])
    assert patch["node_results"] == ["n9:block:evidence_insufficient"]
    assert "counters" not in patch
    assert [node for node, _ in runtime_deps.model_gateway.calls if node == "n9"] == []
    if case == "no_evidence_only":
        assert len(findings) == 1
        assert (findings[0].slot_id, findings[0].kind, findings[0].citations) == (
            2,
            "unverified",
            [],
        )
    else:
        assert findings == []


def fake_graph_nodes(block_at: str):
    nodes = {}

    for name in graph_module.VERTICES:
        async def node(state, runtime=None, *, current=name):
            if current == "n3":
                return {"claim_ids": [uid(9001)], "node_results": ["n3:ok"]}
            if current == block_at:
                reason = "contract_violation" if current == "n5" else "evidence_insufficient"
                return {"node_results": [f"{current}:block:{reason}"]}
            return {"node_results": [f"{current}:ok"]}

        nodes[name] = node
    return nodes


@pytest.mark.asyncio
@pytest.mark.parametrize(("block_at", "not_reached"), [("n5", "n6"), ("n9", "n11")])
async def test_graph_routes_n5_and_n9_blocks_directly_to_n12(monkeypatch, block_at, not_reached):
    monkeypatch.setattr(graph_module, "make_nodes", lambda deps: fake_graph_nodes(block_at))

    result = await graph_module.build_graph(object()).ainvoke(initial_state())

    assert f"{block_at}:block:" in "|".join(result["node_results"])
    assert f"{not_reached}:ok" not in result["node_results"]
    assert result["node_results"][-1] == "n12:ok"
