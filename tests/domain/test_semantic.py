from importlib import import_module

import pytest


def semantic_module():
    return import_module("app.domain.semantic")


def candidate(semantic, index, slot_id, start, end):
    return semantic.ClaimMaterializationCandidate(
        original_index=index,
        slot_id=slot_id,
        global_span_start=start,
        global_span_end=end,
    )


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


def test_eligible_candidate_0개면_materialization_plan이_비어있다():
    semantic = semantic_module()

    result = semantic.plan_claim_materialization([], existing_count=0)

    assert result.materializable_indices == ()
    assert result.eligible_count == 0
    assert result.existing_count == 0
    assert result.capacity == 8
    assert result.capacity_exceeded is False


def test_S4의_같은_slot_external_unit_2개를_모두_materialize한다():
    semantic = semantic_module()
    kinds = [
        semantic.SemanticKind.EXTERNAL_ASSERTION,
        semantic.SemanticKind.EXTERNAL_ASSERTION,
    ]

    assert all(semantic.evaluate_claim_eligibility(4, item).eligible for item in kinds)
    result = semantic.plan_claim_materialization(
        [candidate(semantic, 0, 4, 0, 11), candidate(semantic, 1, 4, 13, 24)],
        existing_count=0,
    )

    assert result.materializable_indices == (0, 1)
    assert result.eligible_count == 2
    assert result.capacity_exceeded is False
    assert not hasattr(result, "ambiguous")


def test_S5의_external_expectation_2개를_모두_materialize한다():
    semantic = semantic_module()
    kind = semantic.SemanticKind.EXTERNAL_EXPECTATION

    assert semantic.evaluate_claim_eligibility(5, kind).eligible is True
    result = semantic.plan_claim_materialization(
        [candidate(semantic, 4, 5, 20, 25), candidate(semantic, 3, 5, 10, 15)],
        existing_count=0,
    )

    assert result.materializable_indices == (3, 4)


@pytest.mark.parametrize(
    "slot_id,context_kind,external_kind",
    [
        (7, "SUBJECTIVE_CONCERN", "EXTERNAL_ASSERTION"),
        (8, "DECISION_RULE", "EXTERNAL_ASSERTION"),
    ],
)
def test_context_unit은_Claim_capacity를_소비하지_않는다(
    slot_id, context_kind, external_kind
):
    semantic = semantic_module()

    context = semantic.evaluate_claim_eligibility(
        slot_id, semantic.SemanticKind(context_kind)
    )
    external = semantic.evaluate_claim_eligibility(
        slot_id, semantic.SemanticKind(external_kind)
    )
    result = semantic.plan_claim_materialization(
        [candidate(semantic, 1, slot_id, 10, 20)], existing_count=0
    )

    assert context.eligible is False
    assert external.eligible is True
    assert result.eligible_count == 1
    assert result.materializable_indices == (1,)


def test_global_8개는_global_span_order로_모두_materialize한다():
    semantic = semantic_module()
    candidates = [
        candidate(semantic, index, 4 + index % 2, index * 10, index * 10 + 5)
        for index in reversed(range(8))
    ]

    result = semantic.plan_claim_materialization(candidates, existing_count=0)

    assert result.materializable_indices == tuple(range(8))
    assert result.eligible_count == 8
    assert result.capacity_exceeded is False


def test_global_9개는_일부를_선택하지_않고_batch_전체를_거부한다():
    semantic = semantic_module()
    candidates = [
        candidate(semantic, index, 4, index * 10, index * 10 + 5)
        for index in range(9)
    ]

    result = semantic.plan_claim_materialization(candidates, existing_count=0)

    assert result.materializable_indices == ()
    assert result.eligible_count == 9
    assert result.capacity_exceeded is True


@pytest.mark.parametrize(
    "existing,new_count,expected_indices,exceeded",
    [
        (3, 5, (0, 1, 2, 3, 4), False),
        (7, 1, (0,), False),
        (7, 2, (), True),
    ],
)
def test_existing_count를_포함해_global_capacity를_atomic하게_적용한다(
    existing, new_count, expected_indices, exceeded
):
    semantic = semantic_module()
    candidates = [
        candidate(semantic, index, 4, index * 10, index * 10 + 5)
        for index in range(new_count)
    ]

    result = semantic.plan_claim_materialization(
        candidates, existing_count=existing
    )

    assert result.materializable_indices == expected_indices
    assert result.existing_count == existing
    assert result.capacity_exceeded is exceeded


def test_exact_same_global_span_candidate는_capacity_계산전에_거부한다():
    semantic = semantic_module()

    with pytest.raises(ValueError, match="duplicate global span"):
        semantic.plan_claim_materialization(
            [candidate(semantic, 0, 4, 10, 20), candidate(semantic, 1, 5, 10, 20)],
            existing_count=0,
        )


def test_candidate_global_span은_정방향이어야_한다():
    semantic = semantic_module()

    with pytest.raises(ValueError, match="global_span_end"):
        candidate(semantic, 0, 4, 10, 10)


def test_duplicate_original_index는_거부한다():
    semantic = semantic_module()

    with pytest.raises(ValueError, match="duplicate original index"):
        semantic.plan_claim_materialization(
            [candidate(semantic, 0, 4, 0, 5), candidate(semantic, 0, 4, 10, 15)],
            existing_count=0,
        )


@pytest.mark.parametrize(
    "fields,message",
    [
        (
            {
                "materializable_indices": (),
                "eligible_count": 1,
                "existing_count": 0,
                "capacity_exceeded": False,
            },
            "select every eligible candidate",
        ),
        (
            {
                "materializable_indices": (0,),
                "eligible_count": 1,
                "existing_count": 8,
                "capacity_exceeded": True,
            },
            "cannot select candidates",
        ),
        (
            {
                "materializable_indices": (),
                "eligible_count": 1,
                "existing_count": 8,
                "capacity_exceeded": False,
            },
            "does not match",
        ),
    ],
)
def test_materialization_plan은_atomic_result_invariant를_강제한다(fields, message):
    semantic = semantic_module()

    with pytest.raises(ValueError, match=message):
        semantic.ClaimMaterializationPlan(**fields)


def test_existing_count는_non_negative여야_한다():
    semantic = semantic_module()

    with pytest.raises(ValueError, match="existing_count"):
        semantic.plan_claim_materialization([], existing_count=-1)


def test_structured_slot_lock은_LLM의_slot_변경을_거부한다():
    semantic = semantic_module()

    assert semantic.validate_locked_slot(4, 4) == 4
    with pytest.raises(ValueError, match="locked slot mismatch"):
        semantic.validate_locked_slot(4, 7)
