import json

import pytest
from langgraph.runtime import Runtime

from app.contexts.views import GuardScanView
from app.domain.intake import (
    FreeTextInput,
    HybridIntake,
    IntakeMode,
    ResponseState,
    StructuredAnswer,
    TargetSecurityInput,
)
from app.orchestration.nodes.s0 import make_nodes
from app.orchestration.runtime import ReviewRequestContext
from app.schemas.frozen import SourceTrace
from tests.s0.runtime_fixtures import deps, initial_state


def _answer(slot_id: int, value: str) -> StructuredAnswer:
    return StructuredAnswer(
        slot_id=slot_id,
        value=value,
        source=SourceTrace.SURVEY,
        response_state=ResponseState.ANSWERED,
    )


def _intake(
    *,
    mode: IntakeMode = IntakeMode.HYBRID,
    target: TargetSecurityInput | None = None,
    structured: tuple[StructuredAnswer, ...] = (),
    free_text: tuple[FreeTextInput, ...] = (),
) -> HybridIntake:
    return HybridIntake(
        schema_version="hybrid_intake/v1",
        mode=mode,
        target=target,
        structured=structured,
        free_text=free_text,
    )


async def _run_n0(context: ReviewRequestContext):
    runtime_deps = deps()
    patch = await make_nodes(runtime_deps)["n0"](
        initial_state(), Runtime(context=context)
    )
    body = await runtime_deps.review_store.get_input(patch["input_id"])
    return patch, body


def test_runtime_context는_legacy_raw_text와_HybridIntake를_각각_허용한다():
    legacy = ReviewRequestContext(raw_text="hello")
    hybrid = ReviewRequestContext(intake=_intake())

    assert (legacy.raw_text, legacy.intake) == ("hello", None)
    assert hybrid.raw_text is None
    assert hybrid.intake.mode is IntakeMode.HYBRID


def test_runtime_context는_입력이_없거나_두_종류가_동시에_있으면_거부한다():
    with pytest.raises(ValueError, match="exactly one"):
        ReviewRequestContext()

    with pytest.raises(ValueError, match="exactly one"):
        ReviewRequestContext(raw_text="hello", intake=_intake())


def test_mode는_HybridIntake의_후속_입력종류를_제한하지_않는다():
    survey_with_chat = ReviewRequestContext(
        intake=_intake(
            mode=IntakeMode.SURVEY_FIRST,
            free_text=(
                FreeTextInput(text="추가 채팅", source=SourceTrace.CHAT_EXPLICIT),
            ),
        )
    )
    chat_with_survey = ReviewRequestContext(
        intake=_intake(
            mode=IntakeMode.CHAT_FIRST,
            structured=(_answer(1, "CONSIDER_ENTRY"),),
        )
    )

    assert survey_with_chat.intake.free_text[0].text == "추가 채팅"
    assert chat_with_survey.intake.structured[0].value == "CONSIDER_ENTRY"


@pytest.mark.asyncio
async def test_legacy_n0는_기존_masked_input과_sanitized_snapshot을_저장한다():
    raw = "삼성전자 user@example.com 010-1234-5678"

    patch, body = await _run_n0(ReviewRequestContext(raw_text=raw))

    assert body["masked_input"] == "삼성전자 [EMAIL] [PHONE]"
    assert body["schema_version"] == "hybrid_intake/v1"
    assert body["masked_intake"]["mode"] == "CHAT_FIRST"
    assert body["masked_intake"]["free_text"] == [
        {"text": "삼성전자 [EMAIL] [PHONE]", "source": "chat_explicit"}
    ]
    assert raw not in json.dumps(body, ensure_ascii=False)
    assert set(patch) == {"input_id", "node_results"}


@pytest.mark.asyncio
async def test_Hybrid_n0는_arbitrary_text만_scrub하고_contract_values를_보존한다():
    intake = _intake(
        target=TargetSecurityInput(
            selected_code="005930",
            name="삼성전자",
            market="KOSPI",
            source=SourceTrace.SURVEY,
        ),
        structured=(
            _answer(1, "CONSIDER_ENTRY"),
            _answer(2, "NOT_HOLDING"),
            _answer(3, "LONG"),
            _answer(4, "담당자 user@example.com 010-1234-5678"),
        ),
        free_text=(
            FreeTextInput(text="첫째 user@example.com", source=SourceTrace.SURVEY),
            FreeTextInput(
                text="둘째 010-1234-5678", source=SourceTrace.CHAT_EXPLICIT
            ),
        ),
    )

    patch, body = await _run_n0(ReviewRequestContext(intake=intake))

    masked = body["masked_intake"]
    assert masked["target"] == {
        "selected_code": "005930",
        "name": "삼성전자",
        "market": "KOSPI",
        "source": "survey",
    }
    assert [item["value"] for item in masked["structured"][:3]] == [
        "CONSIDER_ENTRY",
        "NOT_HOLDING",
        "LONG",
    ]
    assert masked["structured"][3]["value"] == "담당자 [EMAIL] [PHONE]"
    assert [item["source"] for item in masked["free_text"]] == [
        "survey",
        "chat_explicit",
    ]
    assert body["masked_input"] == "첫째 [EMAIL]\n둘째 [PHONE]"
    serialized = json.dumps(body, ensure_ascii=False)
    assert "user@example.com" not in serialized
    assert "010-1234-5678" not in serialized
    assert set(patch) == {"input_id", "node_results"}


@pytest.mark.asyncio
async def test_structured_only_HybridIntake는_n0_저장까지_성공한다():
    intake = _intake(
        mode=IntakeMode.SURVEY_FIRST,
        structured=(_answer(1, "WAIT"), _answer(2, "HOLDING")),
    )

    patch, body = await _run_n0(ReviewRequestContext(intake=intake))

    assert patch["input_id"]
    assert body["masked_input"] == ""
    assert [item["value"] for item in body["masked_intake"]["structured"]] == [
        "WAIT",
        "HOLDING",
    ]


@pytest.mark.asyncio
async def test_n1은_enriched_body에서_masked_input만_Guard_View로_투영한다():
    runtime_deps = deps()
    body = {
        "schema_version": "hybrid_intake/v1",
        "masked_intake": {"mode": "CHAT_FIRST", "free_text": []},
        "masked_input": "삼성전자",
    }
    input_id = await runtime_deps.review_store.put_input("run-s0", body)
    state = initial_state() | {"input_id": input_id}

    patch = await make_nodes(runtime_deps)["n1"](state)

    node, view = runtime_deps.model_gateway.calls[-1]
    assert node == "n1"
    assert isinstance(view, GuardScanView)
    assert view.model_dump() == {"masked_input": "삼성전자"}
    assert patch == {"node_results": ["n1:ok"], "counters": {"llm_calls": 1}}
