"""P0-2 ReviewState와 리듀서 계약 회귀."""

from __future__ import annotations

import copy
import operator
from typing import get_args, get_origin, get_type_hints

import pytest

from app.orchestration.state import (
    ReviewState,
    add_unique,
    add_unique_by_id,
    merge_by_slot_id,
    merge_dict,
    sum_counters,
)


@pytest.mark.parametrize(
    "left,right,expected",
    [
        (None, ["A", "B"], ["A", "B"]),
        (["A", "B"], None, ["A", "B"]),
        (["A", "B"], ["B", "C", "A", "D"], ["A", "B", "C", "D"]),
    ],
)
def test_add_unique는_최초_도착_순서로_중복을_제거한다(left, right, expected):
    assert add_unique(left, right) == expected


def test_add_unique는_입력을_변경하지_않는다():
    left = ["A", "B"]
    right = ["B", "C"]
    before = copy.deepcopy((left, right))

    add_unique(left, right)

    assert (left, right) == before


def test_add_unique의_I2는_집합_의미로_순서에_독립적이다():
    chunks = [["A", "B"], ["B", "C"], ["D", "A"]]
    orders = [chunks, list(reversed(chunks)), [chunks[1], chunks[2], chunks[0]]]

    results = []
    for order in orders:
        value = None
        for chunk in order:
            value = add_unique(value, chunk)
        results.append(set(value or []))

    assert results == [{"A", "B", "C", "D"}] * 3


def test_add_unique_by_id는_같은_ID의_나중_레코드로_교체한다():
    left = [
        {"conflict_id": "C1", "resolved_claim_id": None},
        {"conflict_id": "C2", "resolved_claim_id": None},
    ]
    right = [{"conflict_id": "C1", "resolved_claim_id": "CLAIM1"}]

    assert add_unique_by_id(left, right) == [
        {"conflict_id": "C1", "resolved_claim_id": "CLAIM1"},
        {"conflict_id": "C2", "resolved_claim_id": None},
    ]


def test_add_unique_by_id는_None을_항등원으로_받고_입력을_변경하지_않는다():
    records = [{"conflict_id": "C1", "resolved_claim_id": None}]
    before = copy.deepcopy(records)

    assert add_unique_by_id(None, records) == records
    assert add_unique_by_id(records, None) == records
    assert records == before


def test_merge_by_slot_id는_같은_슬롯의_필드만_덮어쓴다():
    left = [
        {"slot_id": 1, "status": "filled", "value": "old"},
        {"slot_id": 2, "status": "filled", "value": "keep"},
    ]
    right = [{"slot_id": 1, "status": "confirmed"}]

    assert merge_by_slot_id(left, right) == [
        {"slot_id": 1, "status": "confirmed", "value": "old"},
        {"slot_id": 2, "status": "filled", "value": "keep"},
    ]


def test_merge_by_slot_id는_None을_항등원으로_받고_입력을_변경하지_않는다():
    slots = [{"slot_id": 1, "status": "filled"}]
    before = copy.deepcopy(slots)

    assert merge_by_slot_id(None, slots) == slots
    assert merge_by_slot_id(slots, None) == slots
    assert slots == before


def test_merge_dict는_M1에_따라_동일_provider를_right_overwrite한다():
    left = {
        "dart": {"items_fetched": 4},
        "naver": {"items_fetched": 2},
    }
    right = {"dart": {"items_fetched": 7}}

    assert merge_dict(left, right) == {
        "dart": {"items_fetched": 7},
        "naver": {"items_fetched": 2},
    }


def test_merge_dict는_None을_항등원으로_받고_입력을_변경하지_않는다():
    collections = {"dart": {"items_fetched": 4}}
    before = copy.deepcopy(collections)

    assert merge_dict(None, collections) == collections
    assert merge_dict(collections, None) == collections
    assert collections == before


def test_sum_counters는_없는_키를_0으로_취급한다():
    assert sum_counters(
        {"total_llm_calls": 2, "graph_recollect": 1},
        {"total_llm_calls": 3, "hitl_reask": 1},
    ) == {
        "total_llm_calls": 5,
        "graph_recollect": 1,
        "hitl_reask": 1,
    }


def test_sum_counters는_None을_항등원으로_받고_입력을_변경하지_않는다():
    counters = {"total_llm_calls": 2}
    before = copy.deepcopy(counters)

    assert sum_counters(None, counters) == counters
    assert sum_counters(counters, None) == counters
    assert counters == before


def test_sum_counters는_교환법칙과_결합법칙을_만족한다():
    a = {"x": 1, "y": 2}
    b = {"x": 3, "z": 4}
    c = {"y": 5, "z": 6}

    assert sum_counters(a, b) == sum_counters(b, a)
    assert sum_counters(sum_counters(a, b), c) == sum_counters(a, sum_counters(b, c))


def test_ReviewState는_승인된_19채널만_갖는다():
    expected = {
        "run_id",
        "thread_id",
        "as_of",
        "snapshot_version",
        "input_id",
        "stock",
        "user_action",
        "slots",
        "claim_ids",
        "conflicts",
        "query_ids",
        "collections",
        "claim_evaluation_ids",
        "finding_ids",
        "oppose",
        "report_id",
        "node_results",
        "counters",
        "started_at",
    }

    assert set(ReviewState.__annotations__) == expected
    assert len(ReviewState.__annotations__) == 19


def test_ReviewState에는_금지된_대용량_참조채널이_없다():
    channels = set(ReviewState.__annotations__)

    assert "evidence_ids" not in channels
    assert "claim_evidence_keys" not in channels


@pytest.mark.parametrize(
    "channel,reducer",
    [
        ("slots", merge_by_slot_id),
        ("claim_ids", add_unique),
        ("conflicts", add_unique_by_id),
        ("query_ids", add_unique),
        ("collections", merge_dict),
        ("claim_evaluation_ids", add_unique),
        ("finding_ids", add_unique),
        ("node_results", operator.add),
        ("counters", sum_counters),
    ],
)
def test_ReviewState_누적채널은_승인된_리듀서를_사용한다(channel, reducer):
    hints = get_type_hints(ReviewState, include_extras=True)
    annotation = hints[channel]

    assert get_origin(annotation) is not None
    assert get_args(annotation)[-1] is reducer
