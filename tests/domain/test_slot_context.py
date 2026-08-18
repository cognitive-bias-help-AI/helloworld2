from hashlib import sha256

import pytest
from pydantic import ValidationError

from app.domain.intake import ResponseState
from app.domain.semantic_source import SemanticTextRef
from app.domain.slot_context import (
    ExtractionMethod,
    SlotValueObservation,
    build_slot_observation,
    observation_content_sha256,
)
from app.schemas.frozen import SourceTrace


def test_Survey_enum_observation은_DIRECT이고_text_ref가_없다():
    item = build_slot_observation(
        "run-1",
        slot_id=3,
        response_state=ResponseState.ANSWERED,
        origin=SourceTrace.SURVEY,
        extraction_method=ExtractionMethod.DIRECT,
        value="LONG",
        text_ref=None,
    )

    assert item.schema_version == "slot_observation/v1"
    assert item.slot_id == 3
    assert item.value == "LONG"
    assert item.origin is SourceTrace.SURVEY
    assert item.extraction_method is ExtractionMethod.DIRECT
    assert item.text_ref is None


def test_Survey_text_observation은_본문을_복제하지_않고_whole_segment를_가리킨다():
    item = build_slot_observation(
        "run-1",
        slot_id=4,
        response_state=ResponseState.ANSWERED,
        origin=SourceTrace.SURVEY,
        extraction_method=ExtractionMethod.DIRECT,
        value=None,
        text_ref=SemanticTextRef(
            segment_id="structured:4", local_start=0, local_end=8
        ),
    )

    assert item.value is None
    assert item.text_ref == SemanticTextRef(
        segment_id="structured:4", local_start=0, local_end=8
    )
    assert "text" not in SlotValueObservation.model_fields
    assert "text_span" not in SlotValueObservation.model_fields


def test_Chat_enum_observation은_CHAT_EXPLICIT과_LLM과_source_span을_함께_보존한다():
    item = build_slot_observation(
        "run-1",
        slot_id=3,
        response_state=ResponseState.ANSWERED,
        origin=SourceTrace.CHAT_EXPLICIT,
        extraction_method=ExtractionMethod.LLM,
        value="LONG",
        text_ref=SemanticTextRef(
            segment_id="free_text:0", local_start=0, local_end=8
        ),
    )

    assert (item.origin, item.extraction_method) == (
        SourceTrace.CHAT_EXPLICIT,
        ExtractionMethod.LLM,
    )
    assert item.text_ref.segment_id == "free_text:0"


def test_Observation_ID와_hash는_동일_run_semantic_body에서_결정적이다():
    kwargs = {
        "slot_id": 3,
        "response_state": ResponseState.ANSWERED,
        "origin": SourceTrace.SURVEY,
        "extraction_method": ExtractionMethod.DIRECT,
        "value": "LONG",
        "text_ref": None,
    }

    first = build_slot_observation("run-1", **kwargs)
    second = build_slot_observation("run-1", **kwargs)
    other_run = build_slot_observation("run-2", **kwargs)

    assert first == second
    assert first.observation_id != other_run.observation_id
    assert len(first.observation_id) == 26
    assert first.observation_id.startswith("01")
    assert observation_content_sha256(first) == observation_content_sha256(second)
    expected = "01" + sha256(
        f"run-1|{observation_content_sha256(first)}".encode()
    ).hexdigest().upper()[:24]
    assert first.observation_id == expected


def test_same_value라도_provenance가_다르면_서로_다른_observation이다():
    survey = build_slot_observation(
        "run-1",
        slot_id=3,
        response_state=ResponseState.ANSWERED,
        origin=SourceTrace.SURVEY,
        extraction_method=ExtractionMethod.DIRECT,
        value="LONG",
        text_ref=None,
    )
    chat = build_slot_observation(
        "run-1",
        slot_id=3,
        response_state=ResponseState.ANSWERED,
        origin=SourceTrace.CHAT_EXPLICIT,
        extraction_method=ExtractionMethod.LLM,
        value="LONG",
        text_ref=SemanticTextRef(
            segment_id="free_text:0", local_start=0, local_end=4
        ),
    )

    assert survey.value == chat.value
    assert survey.observation_id != chat.observation_id
    assert observation_content_sha256(survey) != observation_content_sha256(chat)


def test_slot_observation은_registry_value와_text_reference_policy를_검증한다():
    with pytest.raises(ValidationError, match="invalid value for slot 3"):
        build_slot_observation(
            "run-1",
            slot_id=3,
            response_state=ResponseState.ANSWERED,
            origin=SourceTrace.SURVEY,
            extraction_method=ExtractionMethod.DIRECT,
            value="UNKNOWN",
            text_ref=None,
        )

    with pytest.raises(ValidationError, match="text slot requires text_ref"):
        build_slot_observation(
            "run-1",
            slot_id=4,
            response_state=ResponseState.ANSWERED,
            origin=SourceTrace.SURVEY,
            extraction_method=ExtractionMethod.DIRECT,
            value=None,
            text_ref=None,
        )

    with pytest.raises(ValidationError, match="LLM observation requires text_ref"):
        build_slot_observation(
            "run-1",
            slot_id=3,
            response_state=ResponseState.ANSWERED,
            origin=SourceTrace.CHAT_EXPLICIT,
            extraction_method=ExtractionMethod.LLM,
            value="LONG",
            text_ref=None,
        )


def test_slot_observation은_extra와_mutation을_거부한다():
    item = build_slot_observation(
        "run-1",
        slot_id=3,
        response_state=ResponseState.ANSWERED,
        origin=SourceTrace.SURVEY,
        extraction_method=ExtractionMethod.DIRECT,
        value="LONG",
        text_ref=None,
    )

    with pytest.raises(ValidationError):
        SlotValueObservation(**item.model_dump(), extra_field=True)
    with pytest.raises(ValidationError):
        item.value = "SHORT"


def test_non_answered_observation은_value를_가질_수_없다():
    with pytest.raises(ValidationError, match="must not carry value"):
        build_slot_observation(
            "run-1",
            slot_id=8,
            response_state=ResponseState.USER_DECLINED,
            origin=SourceTrace.SURVEY,
            extraction_method=ExtractionMethod.DIRECT,
            value="조건",
            text_ref=None,
        )
