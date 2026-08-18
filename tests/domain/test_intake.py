import pytest
from pydantic import ValidationError

import app.domain.intake as intake_module
from app.domain.intake import (
    FreeTextInput,
    HybridIntake,
    IntakeMode,
    ResponseState,
    StructuredAnswer,
    TargetSecurityInput,
)
from app.schemas.frozen import SourceTrace


def answer(slot_id: int, value: str) -> StructuredAnswer:
    return StructuredAnswer(
        slot_id=slot_id,
        value=value,
        source=SourceTrace.SURVEY,
        response_state=ResponseState.ANSWERED,
    )


@pytest.mark.parametrize(
    "mode",
    [IntakeMode.SURVEY_FIRST, IntakeMode.CHAT_FIRST, IntakeMode.HYBRID],
)
def test_세_입력_mode가_동일한_HybridIntake를_사용한다(mode):
    value = HybridIntake(schema_version="hybrid_intake/v1", mode=mode)

    assert value.mode is mode
    assert value.structured == ()
    assert value.free_text == ()


def test_mode는_후속_입력_방식을_제한하지_않는다():
    survey_first = HybridIntake(
        schema_version="hybrid_intake/v1",
        mode=IntakeMode.SURVEY_FIRST,
        free_text=(FreeTextInput(text="추가 채팅", source=SourceTrace.CHAT_EXPLICIT),),
    )
    chat_first = HybridIntake(
        schema_version="hybrid_intake/v1",
        mode=IntakeMode.CHAT_FIRST,
        structured=(answer(1, "CONSIDER_ENTRY"),),
    )

    assert survey_first.free_text[0].text == "추가 채팅"
    assert chat_first.structured[0].slot_id == 1


def test_target은_optional_explicit_candidate이고_Core_Slot이_아니다():
    without_target = HybridIntake(
        schema_version="hybrid_intake/v1", mode=IntakeMode.HYBRID
    )
    target = TargetSecurityInput(
        selected_code="005930",
        name="삼성전자",
        market="KOSPI",
        source=SourceTrace.SURVEY,
    )
    with_target = HybridIntake(
        schema_version="hybrid_intake/v1", mode=IntakeMode.HYBRID, target=target
    )

    assert without_target.target is None
    assert with_target.target == target
    assert not hasattr(target, "slot_id")


def test_provenance는_기존_SourceTrace를_직접_재사용한다():
    structured = answer(2, "NOT_HOLDING")
    free_text = FreeTextInput(text="판단 이유", source=SourceTrace.CHAT_EXPLICIT)

    assert structured.source is SourceTrace.SURVEY
    assert free_text.source is SourceTrace.CHAT_EXPLICIT
    assert not hasattr(intake_module, "InputSource")


def test_ResponseState_UNKNOWN은_SourceTrace_UNKNOWN과_다른_계약이다():
    assert ResponseState.UNKNOWN.value == SourceTrace.UNKNOWN.value
    assert ResponseState.UNKNOWN is not SourceTrace.UNKNOWN


def test_S3_UNDECIDED_value와_UNKNOWN_response_state를_구분한다():
    value = StructuredAnswer(
        slot_id=3,
        value="UNDECIDED",
        source=SourceTrace.SURVEY,
        response_state=ResponseState.ANSWERED,
    )
    unknown = StructuredAnswer(
        slot_id=3,
        value=None,
        source=SourceTrace.SURVEY,
        response_state=ResponseState.UNKNOWN,
    )

    assert value.value == "UNDECIDED"
    assert unknown.value is None
    assert value.response_state is not unknown.response_state


def test_동일_slot_id_structured_answer는_거부한다():
    with pytest.raises(ValidationError, match="duplicate slot_id"):
        HybridIntake(
            schema_version="hybrid_intake/v1",
            mode=IntakeMode.HYBRID,
            structured=(answer(1, "CONSIDER_ENTRY"), answer(1, "WAIT")),
        )


def test_응답상태와_value_presence가_일치해야_한다():
    with pytest.raises(ValidationError, match="ANSWERED requires value"):
        StructuredAnswer(
            slot_id=1,
            value=None,
            source=SourceTrace.SURVEY,
            response_state=ResponseState.ANSWERED,
        )
    with pytest.raises(ValidationError, match="must not carry value"):
        StructuredAnswer(
            slot_id=1,
            value="WAIT",
            source=SourceTrace.SURVEY,
            response_state=ResponseState.USER_DECLINED,
        )


def test_StructuredAnswer는_registry에_없는_canonical_value를_거부한다():
    with pytest.raises(ValidationError, match="invalid value for slot 1"):
        answer(1, "ENTRY")


def test_contract는_extra와_mutation을_거부한다():
    with pytest.raises(ValidationError):
        FreeTextInput(text="입력", source=SourceTrace.CHAT_EXPLICIT, extra_field=True)

    value = answer(1, "WAIT")
    with pytest.raises(ValidationError):
        value.value = "HOLD"


def test_동일_input의_JSON_serialization은_결정적이다():
    value = HybridIntake(
        schema_version="hybrid_intake/v1",
        mode=IntakeMode.HYBRID,
        target=TargetSecurityInput(
            selected_code="005930", source=SourceTrace.SURVEY
        ),
        structured=(answer(1, "WAIT"), answer(3, "UNDECIDED")),
        free_text=(
            FreeTextInput(text="추가 설명", source=SourceTrace.CHAT_EXPLICIT),
        ),
    )

    assert value.model_dump_json() == value.model_dump_json()
    assert HybridIntake.model_validate_json(value.model_dump_json()) == value


def test_raw_email과_전화번호는_Phase_A_FreeText에_허용된다():
    raw = "문의 test@example.com / 010-1234-5678"

    value = FreeTextInput(text=raw, source=SourceTrace.CHAT_EXPLICIT)

    assert value.text == raw
