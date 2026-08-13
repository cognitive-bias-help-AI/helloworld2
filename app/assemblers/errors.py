"""Typed failures returned by pure assemblers."""

from typing import Literal

from app.schemas.frozen import ReasonCode

AssemblyErrorKind = Literal[
    "coverage_mismatch", "unknown_reference", "duplicate_reference", "contract_violation"
]

_REASONS: dict[AssemblyErrorKind, ReasonCode] = {
    "coverage_mismatch": ReasonCode.COVERAGE_TRUNCATED,
    "unknown_reference": ReasonCode.CONTRACT_VIOLATION,
    "duplicate_reference": ReasonCode.CONTRACT_VIOLATION,
    "contract_violation": ReasonCode.CONTRACT_VIOLATION,
}


class AssemblyError(ValueError):
    def __init__(self, kind: AssemblyErrorKind, *, retryable: bool, detail: str = "") -> None:
        self.kind = kind
        self.reason_code = _REASONS[kind]
        self.retryable = retryable
        super().__init__(detail or kind)
