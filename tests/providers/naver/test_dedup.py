import importlib
from datetime import UTC, datetime, timedelta

import pytest


def _modules():
    try:
        return (
            importlib.import_module("providers.naver.dedup"),
            importlib.import_module("providers.naver.models"),
        )
    except ModuleNotFoundError as exc:
        pytest.fail(f"providers.naver dedup modules are not implemented: {exc}")


def _record(models, *, title, snippet, url, minutes=0):
    return models.NaverNewsRecord(
        title=title,
        snippet=snippet,
        link=url,
        original_link=url,
        publisher="example.com",
        published_at=datetime(2026, 8, 18, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes),
    )


def test_same_url_is_deduplicated():
    mod, models = _modules()
    a = _record(models, title="삼성전자 HBM 확대", snippet="HBM 공급 확대", url="https://x/a?tracking=1")
    b = _record(models, title="삼성전자 HBM 확대", snippet="HBM 공급 확대", url="https://x/a?tracking=2")
    assert len(mod.deduplicate_records([a, b])) == 1


def test_similar_reprint_with_same_event_number_is_deduplicated():
    mod, models = _modules()
    a = _record(
        models,
        title="삼성전자 HBM 투자 465억 확대",
        snippet="삼성전자가 HBM 관련 투자 465억 규모 계획을 밝혔다",
        url="https://a.example/1",
    )
    b = _record(
        models,
        title="삼성전자, HBM 투자 465억 확대",
        snippet="삼성전자가 HBM 관련 투자 465억 규모의 계획을 공개했다",
        url="https://b.example/2",
        minutes=20,
    )
    assert len(mod.deduplicate_records([a, b])) == 1


def test_similar_titles_with_conflicting_event_numbers_are_kept_separate():
    mod, models = _modules()
    a = _record(
        models,
        title="삼성전자 영업익 465억 증가",
        snippet="삼성전자 영업익이 465억 증가했다",
        url="https://a.example/1",
    )
    b = _record(
        models,
        title="삼성전자 영업익 5900억 증가",
        snippet="삼성전자 영업익이 5900억 증가했다",
        url="https://b.example/2",
        minutes=20,
    )
    assert len(mod.deduplicate_records([a, b])) == 2
