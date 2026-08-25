from datetime import UTC, datetime

import pytest

from app.domain.intake import FreeTextInput, HybridIntake, IntakeMode
from app.domain.semantic import SemanticKind
from app.domain.semantic_source import build_semantic_segments
from app.orchestration.drafts import SemanticExtractionDraft, SemanticUnitDraft
from app.orchestration.intake_review_runtime import _invoke_and_assemble
from app.schemas.frozen import SourceTrace, Usage

TEXT = "실적과 뉴스를 확인했다"


def segments():
    intake = HybridIntake(
        schema_version="hybrid_intake/v1",
        mode=IntakeMode.CHAT_FIRST,
        free_text=(FreeTextInput(text=TEXT, source=SourceTrace.CHAT_EXPLICIT),),
    )
    return build_semantic_segments(intake.model_dump(mode="json", exclude={"schema_version"}), "semantic_projection/v1")


def draft(kind: SemanticKind) -> SemanticExtractionDraft:
    return SemanticExtractionDraft(units=[SemanticUnitDraft(
        segment_id="free_text:0", slot_id=6, text_span=TEXT,
        span_offset=(0, len(TEXT)), normalized_proposition=None,
        proposed_value=("NEWS",), semantic_kind=kind,
    )])


class Gateway:
    def __init__(self, drafts):
        self.drafts = list(drafts)
        self.calls = []

    async def invoke(self, slot, prompt_version, input_view, output_schema):
        self.calls.append((slot, prompt_version, input_view))
        return self.drafts.pop(0), Usage(
            model_slot=slot, prompt_tokens=0, output_tokens=0,
            ctx_chars=len(input_view.model_dump_json()),
        )


@pytest.mark.asyncio
async def test_incompatible_semantics_receive_corrective_retry_and_assemble(monkeypatch, capsys):
    monkeypatch.setenv("REVIEW_DEBUG_LOGS", "1")
    gateway = Gateway([draft(SemanticKind.USER_PREFERENCE), draft(SemanticKind.INFORMATION_CHECKED)])

    result = await _invoke_and_assemble(
        run_id="run-1", segments=segments(), structured_answers=(),
        existing_verifiable_claim_count=0, run_started_at=datetime.now(UTC),
        model_gateway=gateway,
    )

    assert result.status.value == "SUCCESS"
    assert [call[1] for call in gateway.calls] == ["n3/v2", "n3/v2/corrective"]
    diagnostic = capsys.readouterr().err
    assert 'category="incompatible_slot_kind"' in diagnostic
    assert 'slot_id=6' in diagnostic
    assert 'semantic_kind="USER_PREFERENCE"' in diagnostic
    assert TEXT not in diagnostic
    correction = gateway.calls[1][2].correction
    assert correction.category == "incompatible_slot_kind"
    assert correction.slot_id == 6
    assert correction.semantic_kind == "USER_PREFERENCE"
    assert correction.segment_id == "free_text:0"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_category", ["invalid_proposed_value", "missing_proposed_value"])
async def test_corrective_retry_carries_actual_value_failure_category(monkeypatch, bad_category):
    first = draft(SemanticKind.INFORMATION_CHECKED)
    if bad_category == "invalid_proposed_value":
        first = first.model_copy(update={"units": [first.units[0].model_copy(update={"proposed_value": ("INVALID",)})]})
    else:
        first = first.model_copy(update={"units": [first.units[0].model_copy(update={"proposed_value": None})]})
    gateway = Gateway([first, draft(SemanticKind.INFORMATION_CHECKED)])
    await _invoke_and_assemble(
        run_id="run-1", segments=segments(), structured_answers=(),
        existing_verifiable_claim_count=0, run_started_at=datetime.now(UTC),
        model_gateway=gateway,
    )
    assert gateway.calls[1][2].correction.category == bad_category


@pytest.mark.asyncio
async def test_two_incompatible_semantics_fail_closed_after_one_corrective_retry():
    gateway = Gateway([draft(SemanticKind.USER_PREFERENCE), draft(SemanticKind.USER_PREFERENCE)])

    with pytest.raises(Exception) as raised:
        await _invoke_and_assemble(
            run_id="run-1", segments=segments(), structured_answers=(),
            existing_verifiable_claim_count=0, run_started_at=datetime.now(UTC),
            model_gateway=gateway,
        )

    assert getattr(raised.value, "category", None) == "incompatible_slot_kind"
    assert len(gateway.calls) == 2
