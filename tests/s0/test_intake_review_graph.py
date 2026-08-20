from __future__ import annotations

import pytest
from langgraph.types import Command

from app.domain.intake import (
    FreeTextInput,
    HybridIntake,
    IntakeMode,
    ResponseState,
    StructuredAnswer,
    TargetSecurityInput,
)
from app.domain.semantic import SemanticKind
from app.domain.stock_scope import InstrumentCandidate
from app.orchestration.checkpoint import MeasuringInMemorySaver
from app.orchestration.drafts import SemanticExtractionDraft, SemanticUnitDraft
from app.orchestration.graph import VERTICES, build_graph
from app.orchestration.runtime import ReviewRequestContext
from app.schemas.frozen import SourceTrace, Usage
from tests.s0.fakes import FixtureStockResolver
from tests.s0.runtime_fixtures import RAW, FlowGateway, deps, initial_state


def config(name: str):
    return {"configurable": {"thread_id": name}}


class AdaptiveGateway(FlowGateway):
    async def invoke(self, slot, prompt_version, input_view, output_schema):
        if output_schema is not SemanticExtractionDraft:
            return await super().invoke(slot, prompt_version, input_view, output_schema)
        self.calls.append(("n3", input_view))
        segment = next(
            (item for item in input_view.segments if item.locked_slot_id is None),
            input_view.segments[0],
        )
        locked = segment.locked_slot_id
        if locked is None:
            units = [SemanticUnitDraft(
                segment_id=segment.segment_id,
                slot_id=4,
                text_span=segment.text,
                span_offset=(0, len(segment.text)),
                normalized_proposition="영업이익 증가",
                proposed_value=None,
                semantic_kind=SemanticKind.EXTERNAL_ASSERTION,
            )]
        else:
            value, kind = {
                1: ("CONSIDER_ENTRY", SemanticKind.USER_PREFERENCE),
                2: ("NOT_HOLDING", SemanticKind.USER_STATE),
                3: ("LONG", SemanticKind.USER_PREFERENCE),
                8: (None, SemanticKind.DECISION_RULE),
            }[locked]
            units = [SemanticUnitDraft(
                segment_id=segment.segment_id,
                slot_id=locked,
                text_span=segment.text,
                span_offset=(0, len(segment.text)),
                normalized_proposition=None,
                proposed_value=value,
                semantic_kind=kind,
            )]
        return SemanticExtractionDraft(units=units), Usage(
            model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1
        )


def answer_interrupt(paused):
    questions = paused["__interrupt__"][0].value["questions"]
    answer_by_slot = {
        1: "진입을 고려합니다",
        2: "아직 보유하지 않았습니다",
        3: "장기로 봅니다",
        8: "전제가 바뀌면 재검토합니다",
    }
    return {
        "answers": [
            {"ask_id": item["ask_id"], "answer": answer_by_slot[item["slot_id"]]}
            for item in questions
        ]
    }


@pytest.mark.asyncio
async def test_production_topology는_legacy_n3_n4_n3b를_intake_review로_교체한다():
    assert "intake_review" in VERTICES
    assert {"n3", "n4", "n3b"}.isdisjoint(VERTICES)


@pytest.mark.asyncio
async def test_compiled_graph는_두번_interrupt_resume후_evidence로_진행한다():
    resolver = FixtureStockResolver(
        {},
        exact_rows={
            "005930": [
                InstrumentCandidate(
                    code="005930", name="삼성전자", market="KOSPI", asset_type="COMMON_STOCK"
                )
            ]
        },
    )
    runtime_deps = deps(gateway=AdaptiveGateway(), resolver=resolver)
    graph = build_graph(runtime_deps, checkpointer=MeasuringInMemorySaver())
    cfg = config("adaptive-two-turn")

    intake = HybridIntake(
        schema_version="hybrid_intake/v1",
        mode=IntakeMode.HYBRID,
        target=TargetSecurityInput(
            selected_code="005930", source=SourceTrace.SURVEY
        ),
        structured=(
            StructuredAnswer(
                slot_id=5, value="기업가치 상승", source=SourceTrace.SURVEY,
                response_state=ResponseState.ANSWERED
            ),
            StructuredAnswer(
                slot_id=8, value="전제가 바뀌면 재검토", source=SourceTrace.SURVEY,
                response_state=ResponseState.ANSWERED
            ),
        ),
        free_text=(FreeTextInput(text=RAW, source=SourceTrace.CHAT_EXPLICIT),),
    )
    first = await graph.ainvoke(
        initial_state(), cfg, context=ReviewRequestContext(intake=intake)
    )
    assert first.get("__interrupt__"), first
    second = await graph.ainvoke(Command(resume=answer_interrupt(first)), cfg)
    assert second["__interrupt__"]
    result = await graph.ainvoke(Command(resume=answer_interrupt(second)), cfg)

    assert result["report_id"]
    assert "intake_review:ready_for_evidence" in result["node_results"]
    assert all("n3b:" not in item and "n4:" not in item for item in result["node_results"])
    assert result["counters"]["hitl_reask"] == 2
