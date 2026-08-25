from app.domain.slots import SLOT_REGISTRY
from app.prompts.registry import system_for


def test_n3_prompt_documents_all_semantic_kinds_and_slot_compatibility():
    prompt = system_for("n3/v2")

    for kind in (
        "user_state", "user_preference", "external_assertion", "external_expectation",
        "decision_rule", "information_checked", "subjective_concern",
    ):
        assert kind in prompt
    assert "6 information_checked" in prompt
    assert "7 counter_evidence_concerns" in prompt
    assert "실적과 뉴스를 확인했다" in prompt
    assert "HBM 경쟁력 회복이 늦을까 걱정된다" in prompt


def test_n3_prompt_renders_allowed_values_from_slot_registry():
    prompt = system_for("n3/v2")
    expected = [
        value
        for slot in SLOT_REGISTRY
        if slot.allowed_values
        for value in slot.allowed_values
    ]
    assert all(value in prompt for value in expected)


def test_n3_corrective_prompt_does_not_hard_code_failure_category():
    prompt = system_for("n3/v2/corrective")
    assert "실패 category는 incompatible_slot_kind다" not in prompt
    assert "correction" in prompt
