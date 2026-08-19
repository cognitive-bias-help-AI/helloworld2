from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from app.assemblers.semantic_extraction import (
    SemanticAssemblyError,
    SemanticAssemblyStatus,
    assemble_semantic_extraction,
)
from app.domain.intake import ResponseState, StructuredAnswer
from app.domain.semantic import SemanticKind
from app.domain.semantic_source import SemanticTextSegment
from app.domain.slot_context import ExtractionMethod
from app.orchestration.drafts import SemanticExtractionDraft, SemanticUnitDraft
from app.schemas.frozen import SourceTrace

NOW = datetime(2026, 8, 18, tzinfo=UTC)
VERSION = "semantic_projection/v1"


def segment(
    segment_id: str,
    text: str,
    *,
    start: int = 0,
    slot_id: int | None = None,
    origin: SourceTrace = SourceTrace.CHAT_EXPLICIT,
) -> SemanticTextSegment:
    return SemanticTextSegment(
        segment_id=segment_id,
        origin=origin,
        locked_slot_id=slot_id,
        text=text,
        anchor_start=start,
        anchor_end=start + len(text),
    )


def answer(
    slot_id: int,
    value=None,
    *,
    state: ResponseState = ResponseState.ANSWERED,
    source: SourceTrace = SourceTrace.SURVEY,
) -> StructuredAnswer:
    return StructuredAnswer(
        slot_id=slot_id,
        value=value,
        source=source,
        response_state=state,
    )


def unit(
    *,
    segment_id: str = "free_text:0",
    slot_id: int,
    text_span: str,
    span: tuple[int, int],
    kind: SemanticKind,
    proposed_value=None,
    proposition: str | None = None,
) -> SemanticUnitDraft:
    return SemanticUnitDraft(
        segment_id=segment_id,
        slot_id=slot_id,
        text_span=text_span,
        span_offset=span,
        normalized_proposition=(
            text_span
            if proposition is None
            and kind
            in {SemanticKind.EXTERNAL_ASSERTION, SemanticKind.EXTERNAL_EXPECTATION}
            else proposition
        ),
        proposed_value=proposed_value,
        semantic_kind=kind,
    )


def assemble(
    draft: SemanticExtractionDraft | None,
    *,
    segments=(),
    answers=(),
    existing=0,
    started_at=NOW,
    version=VERSION,
    run_id="run-1",
):
    return assemble_semantic_extraction(
        draft,
        run_id=run_id,
        projection_version=version,
        segments=segments,
        structured_answers=answers,
        existing_verifiable_claim_count=existing,
        run_started_at=started_at,
    )


def test_structured_enum_only는_draft없이_DIRECT_observation을_만든다():
    result = assemble(
        None,
        answers=(
            answer(1, "CONSIDER_ENTRY"),
            answer(2, "NOT_HOLDING"),
            answer(3, "LONG"),
        ),
    )

    assert result.status is SemanticAssemblyStatus.SUCCESS
    assert [(item.slot_id, item.value) for item in result.observations] == [
        (1, "CONSIDER_ENTRY"),
        (2, "NOT_HOLDING"),
        (3, "LONG"),
    ]
    assert all(
        item.extraction_method is ExtractionMethod.DIRECT
        and item.text_ref is None
        for item in result.observations
    )
    assert result.claims == ()


def test_structured_S6_categories는_DIRECT_value로_검증된다():
    result = assemble(None, answers=(answer(6, ("NEWS", "FINANCIALS")),))

    item = result.observations[0]
    assert (item.slot_id, item.value, item.text_ref) == (
        6,
        ("NEWS", "FINANCIALS"),
        None,
    )


@pytest.mark.parametrize("slot_id", [4, 5, 7, 8])
def test_structured_text_slots는_whole_segment_DIRECT_reference를_사용한다(slot_id):
    text = f"slot {slot_id} text"
    source = segment(
        f"structured:{slot_id}",
        text,
        slot_id=slot_id,
        origin=SourceTrace.SURVEY,
    )

    result = assemble(
        SemanticExtractionDraft(units=[]),
        segments=(source,),
        answers=(answer(slot_id, text),),
    )

    item = result.observations[0]
    assert item.value is None
    assert item.text_ref.model_dump() == {
        "segment_id": f"structured:{slot_id}",
        "local_start": 0,
        "local_end": len(text),
    }


@pytest.mark.parametrize(
    "state",
    [ResponseState.UNKNOWN, ResponseState.UNDECIDED, ResponseState.USER_DECLINED],
)
def test_non_answered_structured_state는_value와_text_ref가_없다(state):
    result = assemble(None, answers=(answer(8, state=state),))

    item = result.observations[0]
    assert (item.response_state, item.value, item.text_ref) == (state, None, None)


def test_structured_S4는_DIRECT_whole과_LLM_external_observation과_Claim을_만든다():
    text = "HBM 공급이 부족하다"
    source = segment(
        "structured:4", text, slot_id=4, origin=SourceTrace.SURVEY
    )
    draft = SemanticExtractionDraft(
        units=[
            unit(
                segment_id="structured:4",
                slot_id=4,
                text_span="공급이 부족하다",
                span=(4, 12),
                kind=SemanticKind.EXTERNAL_ASSERTION,
                proposition="LLM paraphrase는 정본이 아니다",
            )
        ]
    )

    result = assemble(draft, segments=(source,), answers=(answer(4, text),))

    assert result.status is SemanticAssemblyStatus.SUCCESS
    assert len(result.observations) == 2
    direct, extracted = result.observations
    assert direct.extraction_method is ExtractionMethod.DIRECT
    assert direct.text_ref.model_dump() == {
        "segment_id": "structured:4",
        "local_start": 0,
        "local_end": 12,
    }
    assert extracted.extraction_method is ExtractionMethod.LLM
    assert extracted.text_ref.model_dump() == {
        "segment_id": "structured:4",
        "local_start": 4,
        "local_end": 12,
    }
    claim = result.claims[0]
    assert claim.user_text_span == claim.normalized_proposition == "공급이 부족하다"
    assert claim.span_offset == (4, 12)
    assert claim.verifiable is True
    assert claim.origin is SourceTrace.SURVEY


def test_chat_USER_STATE는_observation만_만든다():
    text = "아직 보유하지 않았다"
    draft = SemanticExtractionDraft(
        units=[
            unit(
                slot_id=2,
                text_span=text,
                span=(0, len(text)),
                kind=SemanticKind.USER_STATE,
                proposed_value="NOT_HOLDING",
            )
        ]
    )

    result = assemble(draft, segments=(segment("free_text:0", text),))

    assert [(item.slot_id, item.value) for item in result.observations] == [
        (2, "NOT_HOLDING")
    ]
    assert result.claims == ()


def test_chat_external은_source_origin의_Claim을_만든다():
    text = "HBM 수요가 증가한다"
    draft = SemanticExtractionDraft(
        units=[
            unit(
                slot_id=4,
                text_span=text,
                span=(0, len(text)),
                kind=SemanticKind.EXTERNAL_ASSERTION,
            )
        ]
    )

    result = assemble(draft, segments=(segment("free_text:0", text),))

    assert len(result.observations) == len(result.claims) == 1
    assert result.claims[0].origin is SourceTrace.CHAT_EXPLICIT
    assert result.claims[0].origin is not SourceTrace.LLM_EXTRACTION


def test_context_only_USER_PREFERENCE는_Claim을_만들지_않는다():
    text = "장기로 볼 생각이다"
    draft = SemanticExtractionDraft(
        units=[
            unit(
                slot_id=3,
                text_span=text,
                span=(0, len(text)),
                kind=SemanticKind.USER_PREFERENCE,
                proposed_value="LONG",
            )
        ]
    )

    result = assemble(draft, segments=(segment("free_text:0", text),))

    assert result.status is SemanticAssemblyStatus.SUCCESS
    assert result.observations[0].value == "LONG"
    assert result.claims == ()


@pytest.mark.parametrize(
    "slot_id,kind,proposed_value",
    [
        (1, SemanticKind.USER_PREFERENCE, "WAIT"),
        (2, SemanticKind.USER_STATE, "HOLDING"),
        (3, SemanticKind.USER_PREFERENCE, "SHORT"),
        (6, SemanticKind.INFORMATION_CHECKED, ("NEWS",)),
        (7, SemanticKind.SUBJECTIVE_CONCERN, None),
        (8, SemanticKind.DECISION_RULE, None),
    ],
)
def test_context_kind의_proposed_value_mapping은_registry를_따른다(
    slot_id, kind, proposed_value
):
    text = "사용자 맥락"
    draft = SemanticExtractionDraft(
        units=[
            unit(
                slot_id=slot_id,
                text_span=text,
                span=(0, len(text)),
                kind=kind,
                proposed_value=proposed_value,
            )
        ]
    )

    result = assemble(draft, segments=(segment("free_text:0", text),))

    assert result.status is SemanticAssemblyStatus.SUCCESS
    assert result.observations[0].value == proposed_value
    assert result.claims == ()


@pytest.mark.parametrize(
    "slot_id,kind,proposed_value,category",
    [
        (2, SemanticKind.USER_STATE, None, "missing_proposed_value"),
        (3, SemanticKind.USER_PREFERENCE, "YEARLY", "invalid_proposed_value"),
        (4, SemanticKind.USER_PREFERENCE, "copied text", "unexpected_proposed_value"),
        (6, SemanticKind.INFORMATION_CHECKED, "INVALID", "invalid_proposed_value"),
    ],
)
def test_invalid_proposed_value_mapping은_fail_closed다(
    slot_id, kind, proposed_value, category
):
    text = "사용자 맥락"
    draft = SemanticExtractionDraft(
        units=[
            unit(
                slot_id=slot_id,
                text_span=text,
                span=(0, len(text)),
                kind=kind,
                proposed_value=proposed_value,
            )
        ]
    )

    with pytest.raises(SemanticAssemblyError) as caught:
        assemble(draft, segments=(segment("free_text:0", text),))

    assert caught.value.category == category


def test_empty_units는_text가_있어도_valid_success다():
    result = assemble(
        SemanticExtractionDraft(units=[]),
        segments=(segment("free_text:0", "잘 모르겠다"),),
    )

    assert result.status is SemanticAssemblyStatus.SUCCESS
    assert result.observations == result.claims == ()


def test_same_S4_different_spans는_Claim_두_개를_global_order로_만든다():
    text = "수요가 증가하고 공급은 부족하다"
    draft = SemanticExtractionDraft(
        units=[
            unit(
                slot_id=4,
                text_span="공급은 부족하다",
                span=(9, 17),
                kind=SemanticKind.EXTERNAL_ASSERTION,
            ),
            unit(
                slot_id=4,
                text_span="수요가 증가하고",
                span=(0, 8),
                kind=SemanticKind.EXTERNAL_ASSERTION,
            ),
        ]
    )

    result = assemble(draft, segments=(segment("free_text:0", text),))

    assert [item.user_text_span for item in result.claims] == [
        "수요가 증가하고",
        "공급은 부족하다",
    ]


@pytest.mark.parametrize(
    "existing,count,status,claim_count",
    [
        (7, 1, SemanticAssemblyStatus.SUCCESS, 1),
        (7, 2, SemanticAssemblyStatus.CAPACITY_EXCEEDED, 0),
    ],
)
def test_global_Claim_capacity는_all_or_none이다(existing, count, status, claim_count):
    text = "A B"
    units = [
        unit(
            slot_id=4,
            text_span=letter,
            span=(index * 2, index * 2 + 1),
            kind=SemanticKind.EXTERNAL_ASSERTION,
        )
        for index, letter in enumerate(("A", "B")[:count])
    ]

    result = assemble(
        SemanticExtractionDraft(units=units),
        segments=(segment("free_text:0", text),),
        existing=existing,
    )

    assert result.status is status
    assert len(result.claims) == claim_count
    if status is SemanticAssemblyStatus.CAPACITY_EXCEEDED:
        assert result.observations == result.claims == ()
        assert result.capacity_plan.capacity_exceeded is True


@pytest.mark.parametrize(
    "draft,segments,error_category",
    [
        (
            SemanticExtractionDraft(
                units=[
                    unit(
                        slot_id=4,
                        text_span="오답",
                        span=(0, 2),
                        kind=SemanticKind.EXTERNAL_ASSERTION,
                    )
                ]
            ),
            (segment("free_text:0", "정답"),),
            "span_mismatch",
        ),
        (
            SemanticExtractionDraft(
                units=[
                    unit(
                        segment_id="structured:4",
                        slot_id=7,
                        text_span="근거",
                        span=(0, 2),
                        kind=SemanticKind.EXTERNAL_ASSERTION,
                    )
                ]
            ),
            (segment("structured:4", "근거", slot_id=4, origin=SourceTrace.SURVEY),),
            "locked_slot_mismatch",
        ),
    ],
)
def test_invalid_unit은_typed_error로_전체를_거부한다(draft, segments, error_category):
    answers = (answer(4, "근거"),) if segments[0].segment_id == "structured:4" else ()
    with pytest.raises(SemanticAssemblyError) as caught:
        assemble(draft, segments=segments, answers=answers)

    assert caught.value.category == error_category


def test_exact_duplicate와_same_identity_inconsistency를_구분한다():
    text = "HBM 수요 증가"
    base = unit(
        slot_id=4,
        text_span=text,
        span=(0, len(text)),
        kind=SemanticKind.EXTERNAL_ASSERTION,
        proposition="초안 A",
    )
    duplicate = SemanticExtractionDraft(units=[base, base.model_copy(deep=True)])
    inconsistent = SemanticExtractionDraft(
        units=[base, base.model_copy(update={"normalized_proposition": "초안 B"})]
    )

    with pytest.raises(SemanticAssemblyError) as exact:
        assemble(duplicate, segments=(segment("free_text:0", text),))
    with pytest.raises(SemanticAssemblyError) as conflict:
        assemble(inconsistent, segments=(segment("free_text:0", text),))

    assert exact.value.category == "duplicate_unit"
    assert conflict.value.category == "inconsistent_unit"


def test_same_span_context와_external은_허용하고_공유_observation과_Claim을_만든다():
    text = "HBM 수요가 증가한다"
    draft = SemanticExtractionDraft(
        units=[
            unit(
                slot_id=4,
                text_span=text,
                span=(0, len(text)),
                kind=SemanticKind.USER_PREFERENCE,
            ),
            unit(
                slot_id=4,
                text_span=text,
                span=(0, len(text)),
                kind=SemanticKind.EXTERNAL_ASSERTION,
            ),
        ]
    )

    result = assemble(draft, segments=(segment("free_text:0", text),))

    assert result.status is SemanticAssemblyStatus.SUCCESS
    assert len(result.observations) == 1
    assert len(result.claims) == 1


def test_same_span_same_Slot의_external_kind_둘은_contract_failure다():
    text = "실적이 증가한다"
    draft = SemanticExtractionDraft(
        units=[
            unit(
                slot_id=4,
                text_span=text,
                span=(0, len(text)),
                kind=SemanticKind.EXTERNAL_ASSERTION,
            ),
            unit(
                slot_id=4,
                text_span=text,
                span=(0, len(text)),
                kind=SemanticKind.EXTERNAL_EXPECTATION,
            ),
        ]
    )

    with pytest.raises(SemanticAssemblyError) as caught:
        assemble(draft, segments=(segment("free_text:0", text),))

    assert caught.value.category == "same_span_external_claim"


def test_free_text_same_span_different_Slot은_Conflict가_아닌_AMBIGUOUS다():
    text = "장기로 기대한다"
    draft = SemanticExtractionDraft(
        units=[
            unit(
                slot_id=4,
                text_span=text,
                span=(0, len(text)),
                kind=SemanticKind.USER_PREFERENCE,
            ),
            unit(
                slot_id=5,
                text_span=text,
                span=(0, len(text)),
                kind=SemanticKind.USER_PREFERENCE,
            ),
        ]
    )

    result = assemble(draft, segments=(segment("free_text:0", text),))

    assert result.status is SemanticAssemblyStatus.AMBIGUOUS
    assert result.observations == result.claims == ()
    assert result.ambiguities[0].slot_ids == (4, 5)
    assert "Conflict" not in type(result.ambiguities[0]).__name__


@pytest.mark.parametrize("left,right", [((0, 8), (4, 12)), ((0, 12), (4, 8))])
def test_partial과_nested_overlap은_독립_grounding이면_허용한다(left, right):
    text = "ABCDEFGHIJKL"
    draft = SemanticExtractionDraft(
        units=[
            unit(
                slot_id=4,
                text_span=text[left[0] : left[1]],
                span=left,
                kind=SemanticKind.EXTERNAL_ASSERTION,
            ),
            unit(
                slot_id=4,
                text_span=text[right[0] : right[1]],
                span=right,
                kind=SemanticKind.EXTERNAL_ASSERTION,
            ),
        ]
    )

    result = assemble(draft, segments=(segment("free_text:0", text),))

    assert result.status is SemanticAssemblyStatus.SUCCESS
    assert len(result.claims) == 2


def test_draft_order와_retry는_canonical_outputs와_ID를_바꾸지_않는다():
    text = "A B C"
    values = [
        unit(
            slot_id=4,
            text_span="C",
            span=(4, 5),
            kind=SemanticKind.EXTERNAL_ASSERTION,
        ),
        unit(
            slot_id=4,
            text_span="A",
            span=(0, 1),
            kind=SemanticKind.EXTERNAL_ASSERTION,
        ),
        unit(
            slot_id=3,
            text_span="B",
            span=(2, 3),
            kind=SemanticKind.USER_PREFERENCE,
            proposed_value="LONG",
        ),
    ]
    source = (segment("free_text:0", text),)

    first = assemble(SemanticExtractionDraft(units=values), segments=source)
    shuffled = assemble(
        SemanticExtractionDraft(units=list(reversed(values))), segments=source
    )
    retry = assemble(SemanticExtractionDraft(units=values), segments=source)

    assert first == shuffled == retry
    assert [item.user_text_span for item in first.claims] == ["A", "C"]


def test_Claim_ID는_semantic_body_hash이고_timestamp와_LLM_paraphrase를_제외한다():
    text = "HBM 공급 부족"
    source = (segment("free_text:0", text),)
    first = assemble(
        SemanticExtractionDraft(
            units=[
                unit(
                    slot_id=4,
                    text_span=text,
                    span=(0, len(text)),
                    kind=SemanticKind.EXTERNAL_ASSERTION,
                    proposition="paraphrase one",
                )
            ]
        ),
        segments=source,
    )
    second = assemble(
        SemanticExtractionDraft(
            units=[
                unit(
                    slot_id=4,
                    text_span=text,
                    span=(0, len(text)),
                    kind=SemanticKind.EXTERNAL_ASSERTION,
                    proposition="paraphrase two",
                )
            ]
        ),
        segments=source,
    )
    body = {
        "normalized_proposition": text,
        "origin": "chat_explicit",
        "projection_version": VERSION,
        "segment_id": "free_text:0",
        "slot_id": 4,
        "span_offset": [0, len(text)],
        "user_text_span": text,
        "verifiable": True,
    }
    encoded = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = sha256(encoded.encode("utf-8")).hexdigest()
    expected_id = "01" + sha256(f"run-1|{digest}".encode()).hexdigest().upper()[:24]

    assert first.claims[0].claim_id == expected_id
    assert first.claims[0].claim_id == second.claims[0].claim_id
    assert first.claims[0].normalized_proposition == text

    later = assemble(
        SemanticExtractionDraft(
            units=[
                unit(
                    slot_id=4,
                    text_span=text,
                    span=(0, len(text)),
                    kind=SemanticKind.EXTERNAL_ASSERTION,
                    proposition="paraphrase one",
                )
            ]
        ),
        segments=source,
        started_at=datetime(2026, 8, 19, tzinfo=UTC),
    )
    assert later.claims[0].claim_id == first.claims[0].claim_id
    assert later.claims[0].created_at != first.claims[0].created_at


def test_run_started_at은_aware여야_하고_batch_Claim에_그대로_적용된다():
    text = "A B"
    draft = SemanticExtractionDraft(
        units=[
            unit(
                slot_id=4,
                text_span=letter,
                span=(index * 2, index * 2 + 1),
                kind=SemanticKind.EXTERNAL_ASSERTION,
            )
            for index, letter in enumerate(("A", "B"))
        ]
    )
    source = (segment("free_text:0", text),)

    with pytest.raises(SemanticAssemblyError) as caught:
        assemble(draft, segments=source, started_at=datetime(2026, 8, 18))
    result = assemble(draft, segments=source)

    assert caught.value.category == "naive_run_started_at"
    assert {item.created_at for item in result.claims} == {NOW}


def test_projection_version과_missing_draft는_fail_closed다():
    source = (segment("free_text:0", "문장"),)

    with pytest.raises(SemanticAssemblyError) as version:
        assemble(SemanticExtractionDraft(units=[]), version="latest")
    with pytest.raises(SemanticAssemblyError) as missing:
        assemble(None, segments=source)

    assert version.value.category == "unknown_projection_version"
    assert missing.value.category == "missing_semantic_draft"


def test_unknown_segment_incompatible_kind와_negative_existing_count는_거부된다():
    text = "문장"
    unknown = SemanticExtractionDraft(
        units=[
            unit(
                segment_id="free_text:9",
                slot_id=4,
                text_span=text,
                span=(0, len(text)),
                kind=SemanticKind.EXTERNAL_ASSERTION,
            )
        ]
    )
    incompatible = SemanticExtractionDraft(
        units=[
            unit(
                slot_id=2,
                text_span=text,
                span=(0, len(text)),
                kind=SemanticKind.EXTERNAL_ASSERTION,
            )
        ]
    )
    source = (segment("free_text:0", text),)

    with pytest.raises(SemanticAssemblyError) as unknown_error:
        assemble(unknown, segments=source)
    with pytest.raises(SemanticAssemblyError) as kind_error:
        assemble(incompatible, segments=source)
    with pytest.raises(SemanticAssemblyError) as count_error:
        assemble(SemanticExtractionDraft(units=[]), existing=-1)

    assert unknown_error.value.category == "unknown_segment"
    assert kind_error.value.category == "incompatible_slot_kind"
    assert count_error.value.category == "negative_existing_claim_count"


def test_external_blank_proposition과_non_user_origin은_방어적으로_거부된다():
    text = "외부 주장"
    blank = SemanticUnitDraft.model_construct(
        segment_id="free_text:0",
        slot_id=4,
        text_span=text,
        span_offset=(0, len(text)),
        normalized_proposition=None,
        proposed_value=None,
        semantic_kind=SemanticKind.EXTERNAL_ASSERTION,
    )
    draft = SemanticExtractionDraft.model_construct(units=[blank])

    with pytest.raises(SemanticAssemblyError) as blank_error:
        assemble(draft, segments=(segment("free_text:0", text),))
    with pytest.raises(SemanticAssemblyError) as origin_error:
        assemble(
            SemanticExtractionDraft(
                units=[
                    unit(
                        slot_id=4,
                        text_span=text,
                        span=(0, len(text)),
                        kind=SemanticKind.EXTERNAL_ASSERTION,
                    )
                ]
            ),
            segments=(
                segment(
                    "free_text:0",
                    text,
                    origin=SourceTrace.LLM_EXTRACTION,
                ),
            ),
        )

    assert blank_error.value.category == "blank_external_proposition"
    assert origin_error.value.category == "invalid_claim_origin"


def test_structured_text_answer는_exact_corresponding_segment가_필수다():
    with pytest.raises(SemanticAssemblyError) as missing:
        assemble(None, answers=(answer(4, "주된 이유"),))

    assert missing.value.category == "missing_structured_text_segment"


def test_masked_PII와_emoji는_sanitized_codepoint_span만_Claim에_남긴다():
    text = "X [EMAIL] 😀"
    draft = SemanticExtractionDraft(
        units=[
            unit(
                slot_id=4,
                text_span="😀",
                span=(10, 11),
                kind=SemanticKind.EXTERNAL_ASSERTION,
            )
        ]
    )

    result = assemble(draft, segments=(segment("free_text:0", text),))

    dumped = result.model_dump_json()
    assert result.claims[0].span_offset == (10, 11)
    assert result.claims[0].user_text_span == "😀"
    assert "user@example.com" not in dumped
    assert "010-1234-5678" not in dumped


def test_multi_segment_emoji_global_offset은_Python_codepoint를_사용한다():
    first = segment(
        "structured:4", "X", slot_id=4, origin=SourceTrace.SURVEY
    )
    second = segment("free_text:0", "A😀B", start=2)
    draft = SemanticExtractionDraft(
        units=[
            unit(
                slot_id=4,
                text_span="😀",
                span=(1, 2),
                kind=SemanticKind.EXTERNAL_ASSERTION,
            )
        ]
    )

    result = assemble(
        draft,
        segments=(first, second),
        answers=(answer(4, "X"),),
    )

    assert result.claims[0].span_offset == (3, 4)


def test_many_unique_context_units는_semantic_magic_bound없이_처리된다():
    text = "가" * 128
    draft = SemanticExtractionDraft(
        units=[
            unit(
                slot_id=4,
                text_span="가",
                span=(index, index + 1),
                kind=SemanticKind.USER_PREFERENCE,
            )
            for index in range(128)
        ]
    )

    result = assemble(draft, segments=(segment("free_text:0", text),))

    assert result.status is SemanticAssemblyStatus.SUCCESS
    assert len(result.observations) == 128
    assert result.claims == ()


def test_semantic_assembler는_Store_State_Model_Graph를_import하지_않는다():
    path = Path("app/assemblers/semantic_extraction.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert not any(
        name.startswith(
            (
                "app.store",
                "app.orchestration.state",
                "app.models",
                "app.orchestration.graph",
                "langgraph",
            )
        )
        for name in imported
    )
