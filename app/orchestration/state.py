"""LangGraph ReviewState와 누적 채널 리듀서."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict


def add_unique(left: list[str] | None, right: list[str] | None) -> list[str]:
    """최초 도착 순서를 보존하며 문자열 ID를 중복 제거한다."""
    result = list(left or [])
    seen = set(result)
    for value in right or []:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _record_id(record: dict) -> object:
    """레코드의 대표 ``*_id`` 값을 반환한다."""
    if "conflict_id" in record:
        return record["conflict_id"]
    for key, value in record.items():
        if key.endswith("_id"):
            return value
    raise ValueError("병합할 레코드에 *_id 키가 없다")


def add_unique_by_id(
    left: list[dict] | None,
    right: list[dict] | None,
) -> list[dict]:
    """논리 ID별 한 레코드만 유지하고 같은 ID는 나중 값으로 교체한다."""
    result = [dict(record) for record in left or []]
    positions = {_record_id(record): index for index, record in enumerate(result)}
    for record in right or []:
        copied = dict(record)
        record_id = _record_id(copied)
        if record_id in positions:
            result[positions[record_id]] = copied
        else:
            positions[record_id] = len(result)
            result.append(copied)
    return result


def merge_by_slot_id(
    left: list[dict] | None,
    right: list[dict] | None,
) -> list[dict]:
    """slot_id별로 병합하고 오른쪽에 있는 필드만 덮어쓴다."""
    result = [dict(slot) for slot in left or []]
    positions = {slot["slot_id"]: index for index, slot in enumerate(result)}
    for slot in right or []:
        copied = dict(slot)
        slot_id = copied["slot_id"]
        if slot_id in positions:
            result[positions[slot_id]] = {**result[positions[slot_id]], **copied}
        else:
            positions[slot_id] = len(result)
            result.append(copied)
    return result


def merge_dict(left: dict | None, right: dict | None) -> dict:
    """M1: provider별 값은 오른쪽 값으로 덮어쓴다."""
    return {**(left or {}), **(right or {})}


def sum_counters(left: dict | None, right: dict | None) -> dict:
    """양쪽에 없는 카운터 값을 0으로 보고 합산한다."""
    left_values = left or {}
    right_values = right or {}
    return {
        key: left_values.get(key, 0) + right_values.get(key, 0)
        for key in left_values.keys() | right_values.keys()
    }


class ReviewState(TypedDict):
    """DDR v2.2의 승인된 19채널 체크포인트 상태."""

    run_id: str
    thread_id: str
    as_of: str
    snapshot_version: int
    input_id: str | None
    stock: dict | None
    user_action: dict | None
    slots: Annotated[list[dict], merge_by_slot_id]
    claim_ids: Annotated[list[str], add_unique]
    conflicts: Annotated[list[dict], add_unique_by_id]
    query_ids: Annotated[list[str], add_unique]
    collections: Annotated[dict, merge_dict]
    claim_evaluation_ids: Annotated[list[str], add_unique]
    finding_ids: Annotated[list[str], add_unique]
    oppose: dict | None
    report_id: str | None
    node_results: Annotated[list[str], operator.add]
    counters: Annotated[dict, sum_counters]
    started_at: str
