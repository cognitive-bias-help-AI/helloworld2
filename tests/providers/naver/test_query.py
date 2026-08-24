import importlib

import pytest


def _module():
    try:
        return importlib.import_module("providers.naver.query")
    except ModuleNotFoundError as exc:
        pytest.fail(f"providers.naver.query is not implemented: {exc}")


def test_curation_adaptive_uses_code_only_for_curated_stock():
    mod = _module()
    assert mod.build_search_terms("001680", "대상", strategy="curation_adaptive") == (
        "001680",
    )


def test_curation_adaptive_widens_for_uncurated_stock():
    mod = _module()
    assert mod.build_search_terms("005490", "POSCO홀딩스", strategy="curation_adaptive") == (
        "POSCO홀딩스",
        "005490",
    )


def test_supplied_queries_override_strategy_and_deduplicate_in_order():
    mod = _module()
    assert mod.build_search_terms(
        "005930",
        "삼성전자",
        strategy="code_only",
        supplied_queries=("삼성전자 HBM", "005930", "삼성전자 HBM"),
    ) == ("삼성전자 HBM", "005930")


def test_build_query_params_expands_one_http_request_per_query():
    mod = _module()
    params = mod.build_query_params(
        "005490",
        "POSCO홀딩스",
        strategy="curation_adaptive",
        display=30,
        sort="date",
    )
    assert params == (
        {
            "stock_code": "005490",
            "stock_name": "POSCO홀딩스",
            "query": "POSCO홀딩스",
            "display": 30,
            "sort": "date",
        },
        {
            "stock_code": "005490",
            "stock_name": "POSCO홀딩스",
            "query": "005490",
            "display": 30,
            "sort": "date",
        },
    )


def test_query_validation_rejects_invalid_krx_code_and_display():
    mod = _module()
    with pytest.raises(ValueError, match="KRX"):
        mod.build_query_params("5930", "삼성전자")
    with pytest.raises(ValueError, match="display"):
        mod.build_query_params("005930", "삼성전자", display=101)


def test_query_planning_accepts_fifth_position_alpha_krx_code():
    mod = _module()
    params = mod.build_query_params("0126Z0", "삼성에피스홀딩스")
    assert params[0]["stock_code"] == "0126Z0"
