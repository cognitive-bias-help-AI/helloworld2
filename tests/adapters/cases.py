"""Typed registry and reusable helpers for the ProviderAdapter contract suite."""

from __future__ import annotations

import ast
import inspect
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from app.gateway.adapters.mock import MockAdapter
from app.gateway.protocols import ProviderAdapter
from app.schemas.frozen import Query, ReasonCode

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
SOURCE_THRESHOLDS = {"news": 250, "dart": 150, "quote": 100}
FORBIDDEN_IMPORTS = ("app.models", "app.prompts", "app.orchestration", "app.contexts")
SECRET_KEYS = {
    "authorization", "api_key", "apikey", "appkey", "appsecret",
    "client_secret", "access_token", "secret_key",
}
PLACEHOLDERS = {"", "<REDACTED>", "test-placeholder"}


@dataclass(frozen=True)
class DraftExpectation:
    source_ref: str
    expected_span_scope: Literal["headline_snippet", "full_text", "structured_field"]
    expects_normalized_value: bool


@dataclass(frozen=True)
class AdapterContractCase:
    case_id: str
    adapter: ProviderAdapter
    query: Query
    raw: dict
    collected_at: datetime
    expectations: tuple[DraftExpectation, ...]
    fixture_paths: tuple[Path, ...]


@dataclass(frozen=True)
class AdapterErrorCase:
    case_id: str
    adapter: ProviderAdapter
    raw: dict
    expected_reason_code: ReasonCode
    expected_retryable: bool
    hint_required: bool = False


@dataclass(frozen=True)
class RawSpanMetric:
    source_type: str
    count: int
    p95_chars: int | None
    provisional_threshold: int
    review_required: bool

    @classmethod
    def from_lengths(cls, source_type: str, lengths: list[int]) -> RawSpanMetric:
        p95 = nearest_rank_p95(lengths)
        threshold = SOURCE_THRESHOLDS[source_type]
        return cls(
            source_type=source_type,
            count=len(lengths),
            p95_chars=p95,
            provisional_threshold=threshold,
            review_required=len(lengths) >= 20 and p95 is not None and p95 > threshold,
        )


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _query(provider: str, collected_at: datetime) -> Query:
    return Query(
        query_id={
            "dart": "01K5ZTQ9X7WPCVN2M4H8JRAB1D",
            "naver": "01K5ZTQ9X7WPCVN2M4H8JRAB2D",
            "kiwoom": "01K5ZTQ9X7WPCVN2M4H8JRAB3D",
        }[provider],
        scope="stock",
        intent="context",
        provider=provider,
        endpoint=f"/{provider}",
        params={"code": "005930"},
        created_at=collected_at,
    )


def _success_case(provider: str) -> AdapterContractCase:
    directory = FIXTURES / provider
    raw_path = directory / "success.json"
    metadata_path = directory / "metadata.json"
    error_path = directory / "errors.json"
    metadata = _load(metadata_path)
    collected_at = datetime.fromisoformat(metadata["collected_at"])
    expectations = tuple(DraftExpectation(**item) for item in metadata["expectations"])
    return AdapterContractCase(
        case_id=f"mock-{provider}-success",
        adapter=MockAdapter(provider),
        query=_query(provider, collected_at),
        raw=_load(raw_path),
        collected_at=collected_at,
        expectations=expectations,
        fixture_paths=(raw_path, metadata_path, error_path),
    )


ALL_ADAPTER_CASES = tuple(_success_case(provider) for provider in ("dart", "naver", "kiwoom"))


def _error_cases() -> tuple[AdapterErrorCase, ...]:
    cases = []
    reason_codes = {
        "upstream_5xx": ReasonCode.UPSTREAM_5XX,
        "rate_limit": ReasonCode.RATE_LIMIT,
        "auth_failed": ReasonCode.AUTH_FAILED,
        "upstream_timeout": ReasonCode.UPSTREAM_TIMEOUT,
    }
    for provider in ("dart", "naver", "kiwoom"):
        adapter = MockAdapter(provider)
        for item in _load(FIXTURES / provider / "errors.json"):
            cases.append(
                AdapterErrorCase(
                    case_id=f"mock-{provider}-{item['case_id']}",
                    adapter=adapter,
                    raw=item["raw"],
                    expected_reason_code=reason_codes[item["reason_code"]],
                    expected_retryable=item["retryable"],
                    hint_required=item.get("hint_required", False),
                )
            )
    return tuple(cases)


ALL_ERROR_CASES = _error_cases()


def validate_registry(
    cases: tuple[AdapterContractCase, ...] | list[AdapterContractCase],
    error_cases: tuple[AdapterErrorCase, ...] | list[AdapterErrorCase],
) -> None:
    ids = [case.case_id for case in cases]
    error_ids = [case.case_id for case in error_cases]
    if len(ids) != len(set(ids)) or len(error_ids) != len(set(error_ids)):
        raise ValueError("case_id는 unique여야 함")
    if {case.adapter.name for case in cases} != {"dart", "naver", "kiwoom"}:
        raise ValueError("Phase 0 registry는 세 provider를 모두 포함해야 함")
    for case in cases:
        if case.collected_at.utcoffset() is None:
            raise ValueError("collected_at은 timezone-aware여야 함")
        if not isinstance(case.adapter, ProviderAdapter):
            raise ValueError("adapter가 ProviderAdapter shape를 만족하지 않음")
        if not inspect.iscoroutinefunction(case.adapter.acall):
            raise ValueError("ProviderAdapter.acall은 async여야 함")
        expectations_by_source_ref(case.expectations)


def expectations_by_source_ref(
    expectations: tuple[DraftExpectation, ...],
) -> dict[str, str]:
    result = {item.source_ref: item.expected_span_scope for item in expectations}
    if len(result) != len(expectations):
        raise ValueError("expectation source_ref는 unique여야 함")
    return result


def nearest_rank_p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def raw_span_metrics(cases: tuple[AdapterContractCase, ...]) -> dict[str, RawSpanMetric]:
    lengths = {source_type: [] for source_type in SOURCE_THRESHOLDS}
    for case in cases:
        for draft in case.adapter.parse_response(case.raw.copy(), case.query):
            lengths[draft.source_type].append(len(draft.raw_span))
    return {
        source_type: RawSpanMetric.from_lengths(source_type, values)
        for source_type, values in lengths.items()
        if values
    }


def coverage_ratio(populated_eligible: list[bool]) -> float:
    if not populated_eligible:
        raise ValueError("eligible Draft가 없어 normalized coverage를 평가할 수 없음")
    return sum(populated_eligible) / len(populated_eligible)


def normalized_coverage_by_source_type(
    cases: tuple[AdapterContractCase, ...],
) -> dict[str, float]:
    values: dict[str, list[bool]] = {"dart": [], "quote": []}
    for case in cases:
        drafts = {
            draft.source_ref: draft
            for draft in case.adapter.parse_response(case.raw.copy(), case.query)
        }
        for expectation in case.expectations:
            if not expectation.expects_normalized_value:
                continue
            draft = drafts.get(expectation.source_ref)
            if draft is None:
                raise ValueError("eligible expectation에 대응하는 Draft가 없음")
            if draft.source_type in values:
                values[draft.source_type].append(bool(draft.normalized_value))
    return {source_type: coverage_ratio(items) for source_type, items in values.items()}


def assert_no_forbidden_imports(adapter: ProviderAdapter) -> None:
    module = inspect.getmodule(adapter.__class__)
    path = Path(inspect.getsourcefile(adapter.__class__) or "")
    if module is None or not path.is_file():
        raise AssertionError("adapter source module을 찾을 수 없음")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = [name for name in imported if name.startswith(FORBIDDEN_IMPORTS)]
    if forbidden:
        raise AssertionError(f"Adapter forbidden direct imports: {forbidden}")


def _assert_safe_value(key: str, value) -> None:
    normalized_key = key.lower()
    if normalized_key in SECRET_KEYS and str(value) not in PLACEHOLDERS:
        raise AssertionError(f"fixture secret-looking value at key: {key}")
    if isinstance(value, str):
        bearer = re.search(r"Bearer\s+([^\s]+)", value, re.IGNORECASE)
        if bearer and bearer.group(1) not in PLACEHOLDERS:
            raise AssertionError("fixture contains Bearer credential")
        query_secret = re.search(
            r"(?:api_key|apikey|access_token)=([^&\s]+)", value, re.IGNORECASE
        )
        if query_secret and query_secret.group(1) not in PLACEHOLDERS:
            raise AssertionError("fixture URL contains credential")


def _walk_fixture(value) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_safe_value(str(key), item)
            _walk_fixture(item)
    elif isinstance(value, list):
        for item in value:
            _walk_fixture(item)
    elif isinstance(value, str):
        _assert_safe_value("", value)


def assert_no_fixture_secrets(paths: tuple[Path, ...]) -> None:
    for path in paths:
        _walk_fixture(_load(path))
