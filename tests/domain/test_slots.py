import pytest

from app.domain.slots import (
    SLOT_REGISTRY,
    AskPolicy,
    EvidencePolicy,
    PreferredInput,
    allowed_values_for,
    get_slot_definition,
    validate_slot_value,
)


def test_registry는_정확히_8개이고_ID와_code가_유일하다():
    assert len(SLOT_REGISTRY) == 8
    assert [item.slot_id for item in SLOT_REGISTRY] == list(range(1, 9))
    assert len({item.slot_id for item in SLOT_REGISTRY}) == 8
    assert len({item.code for item in SLOT_REGISTRY}) == 8


def test_target_security는_Core_Slot_registry에_없다():
    codes = {item.code for item in SLOT_REGISTRY}

    assert not {"target_security", "stock", "ticker"} & codes


@pytest.mark.parametrize(
    "slot_id,required,blocking,preferred,ask,evidence",
    [
        (1, True, True, PreferredInput.STRUCTURED, AskPolicy.ALWAYS_IF_MISSING, EvidencePolicy.NONE),
        (2, True, True, PreferredInput.STRUCTURED, AskPolicy.ALWAYS_IF_MISSING, EvidencePolicy.NONE),
        (3, True, False, PreferredInput.STRUCTURED, AskPolicy.CONDITIONAL, EvidencePolicy.NONE),
        (4, True, True, PreferredInput.FREE_TEXT, AskPolicy.ALWAYS_IF_MISSING, EvidencePolicy.CLAIM_DEPENDENT),
        (5, False, False, PreferredInput.FREE_TEXT, AskPolicy.CONDITIONAL, EvidencePolicy.CLAIM_DEPENDENT),
        (6, False, False, PreferredInput.HYBRID, AskPolicy.USUALLY_SKIP, EvidencePolicy.NONE),
        (7, False, False, PreferredInput.FREE_TEXT, AskPolicy.USUALLY_SKIP, EvidencePolicy.SYSTEM_OPPOSING_SEARCH),
        (8, False, False, PreferredInput.HYBRID, AskPolicy.ONCE_RECOMMENDED, EvidencePolicy.NONE),
    ],
)
def test_slot별_승인된_policy가_고정된다(
    slot_id, required, blocking, preferred, ask, evidence
):
    slot = get_slot_definition(slot_id)

    assert (slot.required, slot.blocking) == (required, blocking)
    assert slot.preferred_input is preferred
    assert slot.ask_policy is ask
    assert slot.evidence_policy is evidence
    assert slot.allow_llm_extraction is True
    assert slot.default_verifiable is False


def test_S1_S2_S3와_S6의_canonical_values가_구분된다():
    assert allowed_values_for(1) == (
        "CONSIDER_ENTRY",
        "HOLD",
        "CONSIDER_EXIT",
        "WAIT",
    )
    assert allowed_values_for(2) == ("HOLDING", "NOT_HOLDING")
    assert allowed_values_for(3) == ("SHORT", "MEDIUM", "LONG", "UNDECIDED")
    assert "UNDECIDED" not in allowed_values_for(1)
    assert "UNDECIDED" not in allowed_values_for(2)
    assert allowed_values_for(6) == (
        "FINANCIALS",
        "DISCLOSURE",
        "NEWS",
        "PRICE_CHART",
        "INDUSTRY",
        "OTHER",
        "NONE_CHECKED",
    )


@pytest.mark.parametrize(
    "slot_id,value",
    [(1, "WAIT"), (2, "HOLDING"), (3, "UNDECIDED"), (4, "HBM 수요 증가"), (6, ("NEWS", "DISCLOSURE"))],
)
def test_slot_value_validation은_승인된_shape를_반환한다(slot_id, value):
    assert validate_slot_value(slot_id, value) == value


@pytest.mark.parametrize(
    "slot_id,value",
    [(1, "ENTRY"), (2, "UNDECIDED"), (3, "UNKNOWN"), (4, ""), (6, ("NEWS", "INVALID")), (9, "value")],
)
def test_invalid_slot_value는_거부한다(slot_id, value):
    with pytest.raises(ValueError):
        validate_slot_value(slot_id, value)


def test_registry_iteration과_lookup은_결정적이다():
    first = [(item.slot_id, item.code) for item in SLOT_REGISTRY]
    second = [(item.slot_id, item.code) for item in SLOT_REGISTRY]

    assert first == second
    assert get_slot_definition(1) is get_slot_definition(1)
