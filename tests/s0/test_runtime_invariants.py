import pytest

from app.contexts.budget import NODE_BUDGETS, ctx_chars, ctx_items
from app.domain.semantic import MAX_VERIFIABLE_CLAIMS
from app.orchestration.drafts import GuardScanResult
from app.orchestration.graph import build_graph
from app.orchestration.limits import (
    EXTERNAL_CALL_LIMIT,
    GRAPH_RECOLLECT_LIMIT,
    HITL_REASK_LIMIT,
    REWRITE_LIMIT,
    llm_call_limit,
    permits,
)
from app.orchestration.runtime import ReviewRequestContext
from app.orchestration.validators.citations import CitationContractViolation, validate_citations
from app.schemas.frozen import CitationRef
from tests.s0.runtime_fixtures import RAW, FlowGateway, complete_intake, deps, initial_state


def test_I6_exact_six_rule_constants():
    assert (HITL_REASK_LIMIT, GRAPH_RECOLLECT_LIMIT, REWRITE_LIMIT, EXTERNAL_CALL_LIMIT) == (
        2,
        1,
        2,
        25,
    )
    assert llm_call_limit(3) == 21
    assert llm_call_limit(MAX_VERIFIABLE_CLAIMS) == 41


@pytest.mark.asyncio
async def test_I3_7개_runtime_model_call이_existing_budget를_준수한다():
    gateway = FlowGateway()
    await build_graph(deps(gateway=gateway)).ainvoke(
        initial_state(), context=ReviewRequestContext(intake=complete_intake())
    )
    observations = gateway.calls
    assert {node for node, _ in observations} == set(NODE_BUDGETS) - {"n4"}
    for node, view in observations:
        budget = NODE_BUDGETS[node]
        assert ctx_chars(view) <= budget.chars
        assert budget.items is None or ctx_items(view) <= budget.items


@pytest.mark.parametrize(
    "limit", [HITL_REASK_LIMIT, GRAPH_RECOLLECT_LIMIT, REWRITE_LIMIT, EXTERNAL_CALL_LIMIT]
)
def test_I6_fixed_limits_allow_boundary_and_reject_one_more(limit):
    assert permits(limit - 1, limit)
    assert not permits(limit, limit)


@pytest.mark.parametrize("claims", [0, 1, 8])
def test_I6_llm_formula_boundary(claims):
    limit = llm_call_limit(claims)
    assert limit == 4 * claims + 9
    assert permits(limit - 1, limit)
    assert not permits(limit, limit)


@pytest.mark.asyncio
async def test_I7_unknown과_span_mismatch는_report_publish전에_거부된다():
    runtime_deps = deps()
    citation = CitationRef(evidence_id="01ARZ3NDEKTSV4RRFFQ69G5FAW", span="없는 문장")
    with pytest.raises(CitationContractViolation):
        validate_citations([citation], {})
    assert runtime_deps.review_store._reports == {}


class BlockingGateway(FlowGateway):
    async def invoke(self, slot, prompt_version, input_view, output_schema):
        if output_schema is GuardScanResult:
            from app.schemas.frozen import ReasonCode, Usage

            return GuardScanResult(reason_code=ReasonCode.PROMPT_INJECTION), Usage(
                model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1
            )
        return await super().invoke(slot, prompt_version, input_view, output_schema)


@pytest.mark.asyncio
async def test_I6_fatal_guard는_후속_call없이_n12로_직행한다():
    gateway = BlockingGateway()
    result = await build_graph(deps(gateway=gateway)).ainvoke(
        initial_state(), context=ReviewRequestContext(raw_text=RAW)
    )
    assert gateway.calls == []
    assert result["node_results"] == ["n0:ok", "n1:block:prompt_injection", "n12:end"]
