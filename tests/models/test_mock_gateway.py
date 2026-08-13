import pytest

from app.contexts.budget import ctx_chars
from app.contexts.views import GuardScanView
from app.models.mock import MockModelGateway
from app.orchestration.drafts import (
    AskBackDraft,
    FindingDraft,
    GuardScanResult,
    GuardVerdictDraft,
    RenderDraft,
    SlotExtractionDraft,
)
from app.schemas.frozen import ClaimEvaluation, ClaimEvaluationDraft, ClaimStanceDraft, Finding


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        GuardScanResult(), SlotExtractionDraft(claims=[]), AskBackDraft(questions=[]),
        ClaimStanceDraft(stances=[]),
        ClaimEvaluationDraft(citations=[], support_evidence_ids=[], oppose_evidence_ids=[],
                             unknown_evidence_ids=[], verdict="unverifiable",
                             missing_dimensions=[], uncertainty_codes=[]),
        FindingDraft(slot_id=1, kind="missing", citations=[]),
        GuardVerdictDraft(violations=[]), RenderDraft(slots=[]),
    ],
)
async def test_MockModelGateway는_정확히_8종_Draft를_반환하고_Usage를_계산한다(response):
    view = GuardScanView(masked_input="검토")
    gateway = MockModelGateway({type(response): response})
    actual, usage = await gateway.invoke("SMALL", "n1/v1", view, type(response))
    assert actual == response and actual is not response
    assert usage.model_slot == "SMALL" and usage.ctx_chars == ctx_chars(view)
    assert usage.prompt_tokens == usage.output_tokens == 0


@pytest.mark.asyncio
async def test_MockModelGateway는_canonical_schema와_BaseModel아닌_input을_거부한다():
    gateway = MockModelGateway({})
    view = GuardScanView(masked_input="검토")
    with pytest.raises(ValueError, match="허용되지 않은 output_schema"):
        await gateway.invoke("LARGE", "n8/v1", view, ClaimEvaluation)
    with pytest.raises(ValueError, match="허용되지 않은 output_schema"):
        await gateway.invoke("LARGE", "n9/v1", view, Finding)
    with pytest.raises(TypeError, match="BaseModel"):
        await gateway.invoke("SMALL", "n1/v1", {"masked_input": "검토"}, GuardScanResult)


@pytest.mark.asyncio
async def test_MockModelGateway는_fixture_누락과_타입불일치를_거부한다():
    view = GuardScanView(masked_input="검토")
    with pytest.raises(ValueError, match="fixture"):
        await MockModelGateway({}).invoke("SMALL", "n1/v1", view, GuardScanResult)
    gateway = MockModelGateway({GuardScanResult: AskBackDraft(questions=[])})
    with pytest.raises(TypeError, match="fixture"):
        await gateway.invoke("SMALL", "n1/v1", view, GuardScanResult)
