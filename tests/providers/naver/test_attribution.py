import importlib

import pytest


def _module():
    try:
        return importlib.import_module("providers.naver.attribution")
    except ModuleNotFoundError as exc:
        pytest.fail(f"providers.naver.attribution is not implemented: {exc}")


def _models():
    try:
        return importlib.import_module("providers.naver.models")
    except ModuleNotFoundError as exc:
        pytest.fail(f"providers.naver.models is not implemented: {exc}")


def test_accepts_real_company_article():
    mod, models = _module(), _models()
    profile = models.NaverEntityProfile(code="005930", name="삼성전자")
    result = mod.judge_attribution(
        "삼성전자, HBM4 공급 확대",
        "삼성전자(005930)가 하반기 HBM4 공급을 늘린다.",
        profile,
    )
    assert result.is_relevant is True
    assert result.reason.startswith("matched:")


def test_rejects_affiliate_only_article_for_parent_name():
    mod, models = _module(), _models()
    profile = models.NaverEntityProfile(
        code="000880",
        name="한화",
        affiliates=("한화오션", "한화솔루션"),
    )
    result = mod.judge_attribution("한화오션, 수주 확대", "한화오션이 계약을 따냈다", profile)
    assert result.is_relevant is False
    assert result.reason == "affiliate_only"


def test_masks_email_domain_before_matching_naver_company():
    mod, models = _module(), _models()
    profile = models.NaverEntityProfile(code="035420", name="NAVER", aliases=("네이버",))
    result = mod.judge_attribution(
        "기자 연락처 안내",
        "문의는 reporter@naver.com 으로 보내주세요.",
        profile,
    )
    assert result.is_relevant is False


def test_rejects_market_roundup_when_only_code_is_mentioned():
    mod, models = _module(), _models()
    profile = models.NaverEntityProfile(code="005930", name="삼성전자")
    result = mod.judge_attribution(
        "이번 주 외국인 매수 상위 종목",
        "일진전기(103590), 삼성전자(005930), SK하이닉스(000660) 등이 포함됐다.",
        profile,
    )
    assert result.is_relevant is False
    assert result.reason.startswith("market_roundup:")


def test_fifth_position_alpha_code_participates_in_boundary_aware_roundup_detection():
    mod, models = _module(), _models()
    profile = models.NaverEntityProfile(code="0126Z0", name="삼성에피스홀딩스")
    result = mod.judge_attribution(
        "이번 주 외국인 매수 상위 종목",
        "삼성에피스홀딩스(0126Z0), 삼성전자(005930), SK하이닉스(000660)가 포함됐다.",
        profile,
    )
    assert result.is_relevant is False
    assert result.reason == "market_roundup:3codes"
