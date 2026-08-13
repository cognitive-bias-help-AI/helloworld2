from dataclasses import replace
from datetime import UTC, datetime

import pytest

from tests.adapters.cases import (
    ALL_ADAPTER_CASES,
    ALL_ERROR_CASES,
    RawSpanMetric,
    coverage_ratio,
    nearest_rank_p95,
    validate_registry,
)


def test_registered_cases_are_valid():
    validate_registry(ALL_ADAPTER_CASES, ALL_ERROR_CASES)
    assert {case.adapter.name for case in ALL_ADAPTER_CASES} == {"dart", "naver", "kiwoom"}


@pytest.mark.parametrize(
    ("values", "expected"),
    [([], None), (list(range(1, 20)), 19), (list(range(1, 21)), 19), ([3, 1, 2], 3)],
)
def test_nearest_rank_p95_boundary(values, expected):
    assert nearest_rank_p95(values) == expected


def test_raw_span_metric은_20건부터_review_eligibility를_표시한다():
    provisional = RawSpanMetric.from_lengths("news", [251] * 19)
    review = RawSpanMetric.from_lengths("news", [251] * 20)
    assert (provisional.count, provisional.p95_chars, provisional.review_required) == (19, 251, False)
    assert (review.count, review.p95_chars, review.review_required) == (20, 251, True)


@pytest.mark.parametrize(
    ("values", "expected"),
    [([True], 1.0), ([True] * 9 + [False], 0.9), ([True] * 8 + [False] * 2, 0.8)],
)
def test_normalized_coverage는_eligible만_계산한다(values, expected):
    assert coverage_ratio(values) == expected


def test_normalized_coverage는_vacuous_pass를_거부한다():
    with pytest.raises(ValueError, match="eligible"):
        coverage_ratio([])


def test_registry는_naive_collected_at을_거부한다():
    case = ALL_ADAPTER_CASES[0]
    invalid = replace(case, collected_at=datetime(2026, 8, 13))
    cases = (invalid, *ALL_ADAPTER_CASES[1:])
    with pytest.raises(ValueError, match="collected_at"):
        validate_registry(cases, ALL_ERROR_CASES)


def test_registry의_기준시각은_고정_aware다():
    assert all(case.collected_at.tzinfo is not None for case in ALL_ADAPTER_CASES)
    assert datetime(2026, 8, 13, tzinfo=UTC) <= ALL_ADAPTER_CASES[0].collected_at
