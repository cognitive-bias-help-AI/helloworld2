import pytest

from app.assemblers.semantic_extraction import SemanticAssemblyError
from app.orchestration.model_failure import (
    MAX_MODEL_ATTEMPTS,
    ModelFailure,
    ModelFailureCause,
    ModelFailureFamily,
    classify_model_failure,
)


def decide(cause, *, attempts, semantic_error=None):
    return classify_model_failure(
        ModelFailure(cause=cause, semantic_error=semantic_error),
        completed_model_attempts=attempts,
    )


@pytest.mark.parametrize(
    "cause",
    [
        ModelFailureCause.VIEW_BUDGET_EXCEEDED,
        ModelFailureCause.LOCAL_VIEW_INVALID,
    ],
)
def test_pre_model_failure는_model_attempt나_retry를_기록하지_않는다(cause):
    result = decide(cause, attempts=0)

    assert result.family is ModelFailureFamily.PRE_MODEL_FAILURE
    assert not result.model_attempted
    assert not result.retry_allowed
    assert result.terminal


@pytest.mark.parametrize(
    "cause,expected_family",
    [
        (ModelFailureCause.MODEL_GATEWAY_ERROR, ModelFailureFamily.MODEL_CALL_FAILURE),
        (ModelFailureCause.DRAFT_SCHEMA_INVALID, ModelFailureFamily.MODEL_OUTPUT_INVALID),
    ],
)
def test_gateway와_draft_failure는_첫_model_attempt_후_한번만_retry한다(
    cause, expected_family
):
    result = decide(cause, attempts=1)

    assert result.family is expected_family
    assert result.model_attempted
    assert result.retry_allowed
    assert not result.terminal


def test_retryable_semantic_error는_assembler_metadata를_존중한다():
    error = SemanticAssemblyError(
        "invalid_span_bounds", family="span", retryable=True
    )

    result = decide(
        ModelFailureCause.SEMANTIC_ASSEMBLY_ERROR,
        attempts=1,
        semantic_error=error,
    )

    assert result.family is ModelFailureFamily.SEMANTIC_OUTPUT_INVALID
    assert result.model_attempted
    assert result.retry_allowed


def test_terminal_semantic_error는_model_retry를_허용하지_않는다():
    error = SemanticAssemblyError(
        "unknown_projection_version", family="contract", retryable=False
    )

    result = decide(
        ModelFailureCause.SEMANTIC_ASSEMBLY_ERROR,
        attempts=1,
        semantic_error=error,
    )

    assert result.family is ModelFailureFamily.DETERMINISTIC_TERMINAL
    assert result.model_attempted
    assert not result.retry_allowed
    assert result.terminal


@pytest.mark.parametrize(
    "cause",
    [
        ModelFailureCause.SEMANTIC_CAPACITY_EXCEEDED,
        ModelFailureCause.STORE_PERSISTENCE_ERROR,
    ],
)
def test_capacity와_store_failure는_model_retry를_허용하지_않는다(cause):
    result = decide(cause, attempts=1)

    assert result.family is ModelFailureFamily.DETERMINISTIC_TERMINAL
    assert result.model_attempted
    assert not result.retry_allowed
    assert result.terminal


def test_total_model_attempt가_2에_도달하면_추가_retry를_막는다():
    result = decide(
        ModelFailureCause.MODEL_GATEWAY_ERROR,
        attempts=MAX_MODEL_ATTEMPTS,
    )

    assert result.model_attempted
    assert not result.retry_allowed
    assert result.terminal


def test_failure_classification은_입력순서와_무관하게_결정적이다():
    failures = [
        ModelFailure(cause=ModelFailureCause.MODEL_GATEWAY_ERROR),
        ModelFailure(cause=ModelFailureCause.DRAFT_SCHEMA_INVALID),
        ModelFailure(cause=ModelFailureCause.SEMANTIC_CAPACITY_EXCEEDED),
    ]

    first = {item.cause: classify_model_failure(item, completed_model_attempts=1) for item in failures}
    second = {
        item.cause: classify_model_failure(item, completed_model_attempts=1)
        for item in reversed(failures)
    }

    assert first == second


def test_semantic_error_metadata는_semantic_failure에만_허용된다():
    with pytest.raises(ValueError, match="semantic_error"):
        ModelFailure(
            cause=ModelFailureCause.MODEL_GATEWAY_ERROR,
            semantic_error=SemanticAssemblyError("span", retryable=True),
        )
