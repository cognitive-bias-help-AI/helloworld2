"""Pure n3-ready classification of model-adjacent failures."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.assemblers.semantic_extraction import SemanticAssemblyError

MAX_MODEL_ATTEMPTS = 2


class ModelFailureFamily(StrEnum):
    PRE_MODEL_FAILURE = "PRE_MODEL_FAILURE"
    MODEL_CALL_FAILURE = "MODEL_CALL_FAILURE"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    SEMANTIC_OUTPUT_INVALID = "SEMANTIC_OUTPUT_INVALID"
    DETERMINISTIC_TERMINAL = "DETERMINISTIC_TERMINAL"


class ModelFailureCause(StrEnum):
    VIEW_BUDGET_EXCEEDED = "VIEW_BUDGET_EXCEEDED"
    LOCAL_VIEW_INVALID = "LOCAL_VIEW_INVALID"
    MODEL_GATEWAY_ERROR = "MODEL_GATEWAY_ERROR"
    DRAFT_SCHEMA_INVALID = "DRAFT_SCHEMA_INVALID"
    SEMANTIC_ASSEMBLY_ERROR = "SEMANTIC_ASSEMBLY_ERROR"
    SEMANTIC_CAPACITY_EXCEEDED = "SEMANTIC_CAPACITY_EXCEEDED"
    STORE_PERSISTENCE_ERROR = "STORE_PERSISTENCE_ERROR"


class _ModelFailureContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        arbitrary_types_allowed=True,
    )


class ModelFailure(_ModelFailureContract):
    """One already-observed failure, without retry execution behavior."""

    cause: ModelFailureCause
    semantic_error: SemanticAssemblyError | None = None

    @model_validator(mode="after")
    def enforce_semantic_error_ownership(self):
        semantic_cause = self.cause is ModelFailureCause.SEMANTIC_ASSEMBLY_ERROR
        if semantic_cause != (self.semantic_error is not None):
            raise ValueError("semantic_error is required only for semantic assembly failure")
        return self


class ModelFailureDecision(_ModelFailureContract):
    family: ModelFailureFamily
    model_attempted: bool
    retry_allowed: bool
    terminal: bool
    max_model_attempts: int = Field(default=MAX_MODEL_ATTEMPTS, ge=1)

    @model_validator(mode="after")
    def enforce_retry_terminal_consistency(self):
        if self.retry_allowed == self.terminal:
            raise ValueError("retry_allowed and terminal must be opposites")
        return self


_PRE_MODEL_CAUSES = {
    ModelFailureCause.VIEW_BUDGET_EXCEEDED,
    ModelFailureCause.LOCAL_VIEW_INVALID,
}


def classify_model_failure(
    failure: ModelFailure,
    *,
    completed_model_attempts: int,
) -> ModelFailureDecision:
    """Decide one bounded model-retry candidate without invoking anything."""

    if completed_model_attempts < 0:
        raise ValueError("completed_model_attempts must be non-negative")

    cause = failure.cause
    if cause in _PRE_MODEL_CAUSES:
        family = ModelFailureFamily.PRE_MODEL_FAILURE
        model_attempted = False
        retry_candidate = False
    elif cause is ModelFailureCause.MODEL_GATEWAY_ERROR:
        family = ModelFailureFamily.MODEL_CALL_FAILURE
        model_attempted = True
        retry_candidate = True
    elif cause is ModelFailureCause.DRAFT_SCHEMA_INVALID:
        family = ModelFailureFamily.MODEL_OUTPUT_INVALID
        model_attempted = True
        retry_candidate = True
    elif cause is ModelFailureCause.SEMANTIC_ASSEMBLY_ERROR:
        model_attempted = True
        retry_candidate = failure.semantic_error.retryable
        family = (
            ModelFailureFamily.SEMANTIC_OUTPUT_INVALID
            if retry_candidate
            else ModelFailureFamily.DETERMINISTIC_TERMINAL
        )
    else:
        family = ModelFailureFamily.DETERMINISTIC_TERMINAL
        model_attempted = True
        retry_candidate = False

    retry_allowed = retry_candidate and completed_model_attempts < MAX_MODEL_ATTEMPTS
    return ModelFailureDecision(
        family=family,
        model_attempted=model_attempted,
        retry_allowed=retry_allowed,
        terminal=not retry_allowed,
    )
