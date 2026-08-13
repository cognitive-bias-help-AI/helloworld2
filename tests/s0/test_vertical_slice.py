import pytest
from langgraph.types import Command

from app.orchestration.checkpoint import MeasuringInMemorySaver
from app.orchestration.drafts import SlotExtractionDraft
from app.orchestration.graph import build_graph
from app.orchestration.runtime import ReviewRequestContext
from app.schemas.frozen import ClaimStanceDraft, StockCandidate
from tests.s0.fakes import FixtureStockResolver
from tests.s0.runtime_fixtures import RAW, FlowGateway, deps, initial_state


def config(name):
    return {"configurable": {"thread_id": name}}


@pytest.mark.asyncio
async def test_stock_HITL_interrupt_checkpoint_resume_membership():
    resolver = FixtureStockResolver(
        {
            RAW: [
                StockCandidate(
                    code="005930", name="삼성전자", market="KOSPI", match_kind="exact_name", score=1
                ),
                StockCandidate(
                    code="000660", name="SK하이닉스", market="KOSPI", match_kind="prefix", score=0.5
                ),
            ]
        }
    )
    runtime_deps = deps(resolver=resolver)
    graph = build_graph(runtime_deps, checkpointer=MeasuringInMemorySaver())
    cfg = config("stock-hitl")
    paused = await graph.ainvoke(initial_state(), cfg, context=ReviewRequestContext(raw_text=RAW))
    assert paused["__interrupt__"]

    result = await graph.ainvoke(Command(resume={"selected_code": "005930"}), cfg)
    assert result["stock"]["code"] == "005930"
    assert result["node_results"][-1] == "n12:end"


class MissingThenFlow(FlowGateway):
    async def invoke(self, slot, prompt_version, input_view, output_schema):
        if output_schema is SlotExtractionDraft:
            self.calls.append(("n3", input_view))
            from app.schemas.frozen import Usage

            return SlotExtractionDraft(claims=[]), Usage(
                model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1
            )
        return await super().invoke(slot, prompt_version, input_view, output_schema)


@pytest.mark.asyncio
async def test_slot_HITL_resume_n3b_USER_CONFIRMED():
    runtime_deps = deps(gateway=MissingThenFlow())
    graph = build_graph(runtime_deps, checkpointer=MeasuringInMemorySaver())
    cfg = config("slot-hitl")
    paused = await graph.ainvoke(initial_state(), cfg, context=ReviewRequestContext(raw_text=RAW))
    assert paused["__interrupt__"]

    result = await graph.ainvoke(Command(resume={"answer": "영업이익이 증가했다"}), cfg)
    claims = await runtime_deps.review_store.get_claims(result["claim_ids"])
    assert claims[-1].origin.value == "user_confirmed"
    assert "n3b:ok" in result["node_results"]
    assert result["report_id"]


class TwiceInvalidStance(FlowGateway):
    def __init__(self):
        super().__init__()
        self.invalid_calls = 0

    async def invoke(self, slot, prompt_version, input_view, output_schema):
        if output_schema is ClaimStanceDraft:
            self.invalid_calls += 1
            from app.schemas.frozen import Usage

            return ClaimStanceDraft(stances=[]), Usage(
                model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1
            )
        return await super().invoke(slot, prompt_version, input_view, output_schema)


@pytest.mark.asyncio
async def test_degraded_two_failures_then_rule_fallback_and_no_third_call():
    gateway = TwiceInvalidStance()
    runtime_deps = deps(gateway=gateway)
    graph = build_graph(runtime_deps, checkpointer=MeasuringInMemorySaver())
    result = await graph.ainvoke(
        initial_state(), config("degraded"), context=ReviewRequestContext(raw_text=RAW)
    )
    report = await runtime_deps.review_store.get_report(result["report_id"])
    links = await runtime_deps.review_store.get_claim_evidence(
        result["run_id"], result["claim_ids"][0]
    )
    assert gateway.invalid_calls == 2
    assert links and all(item.stance_source == "rule" for item in links)
    assert "n7:partial" in result["node_results"]
    assert report["banners"] == ["COVERAGE_TRUNCATED"]
