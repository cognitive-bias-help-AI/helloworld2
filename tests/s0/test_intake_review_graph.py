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
from app.orchestration.drafts import (
    GuardScanResult,
    SemanticExtractionDraft,
    SemanticUnitDraft,
)
from app.orchestration.graph import VERTICES, build_graph
from app.orchestration.runtime import ReviewRequestContext
from app.schemas.frozen import ReasonCode, SourceTrace, Usage
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


class InputInsufficientContextGateway(FlowGateway):
    async def invoke(self, slot, prompt_version, input_view, output_schema):
        if output_schema is GuardScanResult:
            self.calls.append(("n1", input_view))
            return GuardScanResult(reason_code=ReasonCode.INPUT_INSUFFICIENT), Usage(
                model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1
            )
        if output_schema is SemanticExtractionDraft:
            self.calls.append(("n3", input_view))
            return SemanticExtractionDraft(units=[]), Usage(
                model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=1
            )
        return await super().invoke(slot, prompt_version, input_view, output_schema)


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
async def test_input_insufficient와_명시적_무응답은_context_only_report를_생성한다():
    resolver = FixtureStockResolver(
        {},
        exact_rows={
            "005930": [
                InstrumentCandidate(
                    code="005930",
                    name="삼성전자",
                    market="KOSPI",
                    asset_type="COMMON_STOCK",
                )
            ]
        },
    )
    gateway = InputInsufficientContextGateway()
    runtime_deps = deps(gateway=gateway, resolver=resolver)
    intake = HybridIntake(
        schema_version="hybrid_intake/v1",
        mode=IntakeMode.HYBRID,
        target=TargetSecurityInput(selected_code="005930", source=SourceTrace.SURVEY),
        structured=tuple(
            StructuredAnswer(
                slot_id=slot_id,
                value=None,
                source=SourceTrace.SURVEY,
                response_state=ResponseState.USER_DECLINED,
            )
            for slot_id in range(1, 9)
        ),
        free_text=(
            FreeTextInput(text="추가 판단 근거 없음", source=SourceTrace.CHAT_EXPLICIT),
        ),
    )

    result = await build_graph(runtime_deps).ainvoke(
        initial_state(), context=ReviewRequestContext(intake=intake)
    )

    assert "n1:input_insufficient" in result["node_results"]
    assert "n2:ok" in result["node_results"]
    assert "intake_review:context_only" in result["node_results"]
    assert result["claim_ids"] == []
    assert len(result["query_ids"]) == 3
    assert set(result["collections"]) == {"dart", "kiwoom", "naver"}
    assert result["report_id"]
    assert await runtime_deps.review_store.get_report(result["report_id"]) is not None


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
    first_resume = answer_interrupt(first)
    second = await graph.ainvoke(Command(resume=first_resume), cfg)
    assert second["__interrupt__"]
    second_resume = answer_interrupt(second)
    result = await graph.ainvoke(Command(resume=second_resume), cfg)

    assert result["report_id"]
    assert "intake_review:ready_for_evidence" in result["node_results"]
    assert all("n3b:" not in item and "n4:" not in item for item in result["node_results"])
    assert result["counters"]["hitl_reask"] == 2
    asked_slots = {
        item["slot_id"]
        for paused in (first, second)
        for item in paused["__interrupt__"][0].value["questions"]
    }
    resumed_ask_ids = {
        item["ask_id"]
        for payload in (first_resume, second_resume)
        for item in payload["answers"]
    }
    ask_records = await runtime_deps.review_store.get_ask_records("run-s0")
    resume_sources = await runtime_deps.review_store.get_resume_sources("run-s0")
    observations = await runtime_deps.review_store.get_slot_observations("run-s0")

    assert asked_slots == {1, 2, 3}
    assert resumed_ask_ids == {item.ask_id for item in ask_records}
    assert {item.slot_id for item in resume_sources} == asked_slots
    assert {
        item.slot_id for item in observations if item.origin is SourceTrace.USER_CONFIRMED
    } == asked_slots
    slot3 = [item for item in observations if item.slot_id == 3]
    assert len(slot3) == 1
    assert slot3[0].value == "LONG"
    semantic_fingerprints = [
        tuple(
            (segment.segment_id, segment.locked_slot_id, segment.text)
            for segment in input_view.segments
        )
        for node, input_view in runtime_deps.model_gateway.calls
        if node == "n3"
    ]
    assert len(semantic_fingerprints) == len(set(semantic_fingerprints))


@pytest.mark.asyncio
async def test_compiled_graph는_HITL_REASK_LIMIT_이후_세번째_interrupt를_금지한다():
    resolver = FixtureStockResolver(
        {},
        exact_rows={
            "005930": [
                InstrumentCandidate(
                    code="005930",
                    name="삼성전자",
                    market="KOSPI",
                    asset_type="COMMON_STOCK",
                )
            ]
        },
    )
    runtime_deps = deps(gateway=AdaptiveGateway(), resolver=resolver)
    graph = build_graph(runtime_deps, checkpointer=MeasuringInMemorySaver())
    cfg = config("adaptive-hitl-limit")
    intake = HybridIntake(
        schema_version="hybrid_intake/v1",
        mode=IntakeMode.HYBRID,
        target=TargetSecurityInput(selected_code="005930", source=SourceTrace.SURVEY),
        free_text=(FreeTextInput(text=RAW, source=SourceTrace.CHAT_EXPLICIT),),
    )

    first = await graph.ainvoke(
        initial_state(), cfg, context=ReviewRequestContext(intake=intake)
    )
    second = await graph.ainvoke(Command(resume=answer_interrupt(first)), cfg)
    result = await graph.ainvoke(Command(resume=answer_interrupt(second)), cfg)

    assert "__interrupt__" not in result
    assert "intake_review:ready_for_evidence" in result["node_results"]
    assert result["counters"]["hitl_reask"] == 2
    records = await runtime_deps.review_store.get_ask_records("run-s0")
    assert len({item.ask_key.rsplit(":ask:", 1)[0] for item in records}) == 2
