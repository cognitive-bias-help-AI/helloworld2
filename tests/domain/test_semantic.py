from importlib import import_module

import pytest


def semantic_module():
    return import_module("app.domain.semantic")


def test_SemanticKind는_승인된_7개_값만_갖는다():
    semantic = semantic_module()

    assert {item.value for item in semantic.SemanticKind} == {
        "EXTERNAL_ASSERTION",
        "EXTERNAL_EXPECTATION",
        "USER_STATE",
        "USER_PREFERENCE",
        "DECISION_RULE",
        "INFORMATION_CHECKED",
        "SUBJECTIVE_CONCERN",
    }


@pytest.mark.parametrize(
    "slot_id,allowed",
    [
        (1, {"USER_PREFERENCE"}),
        (2, {"USER_STATE"}),
        (3, {"USER_PREFERENCE"}),
        (
            4,
            {
                "USER_PREFERENCE",
                "EXTERNAL_ASSERTION",
                "EXTERNAL_EXPECTATION",
            },
        ),
        (5, {"USER_PREFERENCE", "EXTERNAL_EXPECTATION"}),
        (6, {"INFORMATION_CHECKED"}),
        (
            7,
            {
                "SUBJECTIVE_CONCERN",
                "EXTERNAL_ASSERTION",
                "EXTERNAL_EXPECTATION",
            },
        ),
        (
            8,
            {
                "DECISION_RULE",
                "EXTERNAL_ASSERTION",
                "EXTERNAL_EXPECTATION",
            },
        ),
    ],
)
def test_slot_kind_allowlist는_승인된_조합만_반환한다(slot_id, allowed):
    semantic = semantic_module()

    assert {
        item.value for item in semantic.allowed_semantic_kinds(slot_id)
    } == allowed


@pytest.mark.parametrize(
    "slot_id,allowed_kind,rejected_kind",
    [
        (1, "USER_PREFERENCE", "EXTERNAL_ASSERTION"),
        (2, "USER_STATE", "USER_PREFERENCE"),
        (3, "USER_PREFERENCE", "USER_STATE"),
        (4, "EXTERNAL_ASSERTION", "DECISION_RULE"),
        (5, "EXTERNAL_EXPECTATION", "EXTERNAL_ASSERTION"),
        (6, "INFORMATION_CHECKED", "EXTERNAL_ASSERTION"),
        (7, "SUBJECTIVE_CONCERN", "USER_STATE"),
        (8, "DECISION_RULE", "SUBJECTIVE_CONCERN"),
    ],
)
def test_각_slot은_허용_kind와_거부_kind를_구분한다(
    slot_id, allowed_kind, rejected_kind
):
    semantic = semantic_module()
    kind = semantic.SemanticKind

    assert semantic.is_semantic_kind_allowed(slot_id, kind(allowed_kind)) is True
    assert semantic.is_semantic_kind_allowed(slot_id, kind(rejected_kind)) is False


@pytest.mark.parametrize(
    "slot_id,kind,eligible,reason",
    [
        (1, "USER_PREFERENCE", False, "context_only"),
        (2, "USER_STATE", False, "context_only"),
        (3, "USER_PREFERENCE", False, "context_only"),
        (4, "EXTERNAL_ASSERTION", True, "eligible_external_proposition"),
        (4, "USER_PREFERENCE", False, "context_only"),
        (5, "EXTERNAL_EXPECTATION", True, "eligible_external_proposition"),
        (6, "INFORMATION_CHECKED", False, "context_only"),
        (7, "SUBJECTIVE_CONCERN", False, "context_only"),
        (7, "EXTERNAL_ASSERTION", True, "eligible_external_proposition"),
        (8, "DECISION_RULE", False, "context_only"),
        (8, "EXTERNAL_ASSERTION", True, "eligible_external_proposition"),
    ],
)
def test_Claim_eligibility는_slot_kind_policy가_결정한다(
    slot_id, kind, eligible, reason
):
    semantic = semantic_module()

    result = semantic.evaluate_claim_eligibility(
        slot_id, semantic.SemanticKind(kind)
    )

    assert result.eligible is eligible
    assert result.reason == reason


def test_호환되지_않는_kind는_Claim_eligibility에서도_거부한다():
    semantic = semantic_module()

    result = semantic.evaluate_claim_eligibility(
        5, semantic.SemanticKind.EXTERNAL_ASSERTION
    )

    assert result.eligible is False
    assert result.reason == "incompatible_slot_kind"


def test_eligible_unit_0개면_materializable_candidate가_없다():
    semantic = semantic_module()

    result = semantic.evaluate_slot_claim_cardinality(
        4, [semantic.SemanticKind.USER_PREFERENCE]
    )

    assert result.eligible_unit_count == 0
    assert result.ambiguous is False
    assert result.materializable_index is None


def test_eligible_unit_1개면_그_index만_materializable하다():
    semantic = semantic_module()

    result = semantic.evaluate_slot_claim_cardinality(
        4,
        [
            semantic.SemanticKind.USER_PREFERENCE,
            semantic.SemanticKind.EXTERNAL_ASSERTION,
        ],
    )

    assert result.eligible_unit_count == 1
    assert result.ambiguous is False
    assert result.materializable_index == 1


def test_같은_slot의_eligible_unit_2개는_ambiguous이고_선택하지_않는다():
    semantic = semantic_module()

    result = semantic.evaluate_slot_claim_cardinality(
        4,
        [
            semantic.SemanticKind.EXTERNAL_ASSERTION,
            semantic.SemanticKind.EXTERNAL_EXPECTATION,
        ],
    )

    assert result.eligible_unit_count == 2
    assert result.ambiguous is True
    assert result.materializable_index is None


def test_cardinality는_incompatible_kind를_context처럼_삼키지_않는다():
    semantic = semantic_module()

    with pytest.raises(ValueError, match="incompatible semantic kind"):
        semantic.evaluate_slot_claim_cardinality(
            5, [semantic.SemanticKind.EXTERNAL_ASSERTION]
        )


def test_structured_slot_lock은_LLM의_slot_변경을_거부한다():
    semantic = semantic_module()

    assert semantic.validate_locked_slot(4, 4) == 4
    with pytest.raises(ValueError, match="locked slot mismatch"):
        semantic.validate_locked_slot(4, 7)
