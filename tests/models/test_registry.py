"""모델 슬롯 정본 회귀.

이 테스트가 고정하는 것은 "값이 예쁜가" 가 아니라
**틀린 값이 조용히 프로덕션까지 가는 경로**다. 3건이다.

  1. SMALL 에 effort 가 실리면 요청이 400 이다 — Haiku 4.5 는 effort 미지원
  2. Sonnet 5 단가를 도입가($2/$10)로 두면 2026-08-31 이후 예산이 1.5배 틀린다
  3. 캐시 최소 프리픽스 미달이면 cache_control 이 아무 일도 안 한다. 에러도 안 난다
"""
from __future__ import annotations

import pytest

from app.models import registry as R


def test_세_슬롯이_전부_있다():
    assert set(R.MODEL_REGISTRY) == {"SMALL", "MID", "LARGE"}


def test_모델_id는_문서가_정한_값이다():
    assert R.MODEL_REGISTRY["SMALL"].model_id == "claude-haiku-4-5-20251001"
    assert R.MODEL_REGISTRY["MID"].model_id == "claude-sonnet-5"
    assert R.MODEL_REGISTRY["LARGE"].model_id == "claude-opus-5"


def test_opus5와_sonnet5에는_날짜_스냅샷을_붙이지_않는다():
    """claude-opus-5 / claude-sonnet-5 는 이게 완전한 ID 다.
    날짜를 붙이면 404 다 (DDR C-5 교정)."""
    for slot in ("MID", "LARGE"):
        assert not R.MODEL_REGISTRY[slot].model_id[-1].isdigit() or \
               "-20" not in R.MODEL_REGISTRY[slot].model_id


# ── 1. SMALL 에 effort 를 실으면 400 ────────────────────────────────
def test_small_슬롯은_effort가_None이다():
    assert R.MODEL_REGISTRY["SMALL"].reasoning_effort is None


@pytest.mark.parametrize("prompt_version", ["n1/v1", "n3/v1", "n4/v1", "n7/v1"])
def test_small_노드는_effort를_돌려주지_않는다(prompt_version):
    assert R.effort_for("SMALL", prompt_version) is None


def test_small에_노드_override를_넣으면_즉시_터진다(monkeypatch):
    """설정 실수가 런타임 400 이 아니라 여기서 잡혀야 한다."""
    monkeypatch.setitem(R.NODE_EFFORT, "n7", "high")
    with pytest.raises(ValueError, match="effort 를 지원하지 않는다"):
        R.effort_for("SMALL", "n7/v1")


# ── 노드별 effort override ─────────────────────────────────────────
@pytest.mark.parametrize(
    "prompt_version,expected",
    [("n8/v1", "high"), ("n9/v1", "medium"), ("n10/v1", "low")],
)
def test_large_노드별_effort(prompt_version, expected):
    assert R.effort_for("LARGE", prompt_version) == expected


def test_override가_없는_large노드는_슬롯_기본값을_쓴다():
    assert R.effort_for("LARGE", "n99/v1") == "high"


def test_mid는_low다():
    """n11 은 판단이 아니라 한국어 서술이다. 결론은 n9 가 이미 냈다."""
    assert R.effort_for("MID", "n11/v1") == "low"


def test_effort는_허용된_5단계뿐이다():
    assert set(R.NODE_EFFORT.values()) <= set(R._EFFORT_LEVELS)


# ── 2. Sonnet 5 도입가 함정 ────────────────────────────────────────
def test_sonnet5는_정가로_등록돼_있다():
    """$2/$10 은 2026-08-31 만료되는 도입가다. 정가는 $3/$15."""
    mid = R.MODEL_REGISTRY["MID"]
    assert mid.price_in_krw_per_1m == int(3.0 * R.USD_KRW)
    assert mid.price_out_krw_per_1m == int(15.0 * R.USD_KRW)
    intro_in = int(R.SONNET5_INTRO_PRICE_USD[0] * R.USD_KRW)
    assert mid.price_in_krw_per_1m != intro_in, "도입가가 등록됐다 — 9월 1일에 1.5배 틀어진다"


def test_환율은_fx_yaml에서_온다():
    """코드에 박히면 3개월 뒤 비용 리포트가 조용히 틀린다."""
    assert R.USD_KRW > 0
    assert R._FX_PATH.exists()


def test_원화_단가가_usd에_비례한다():
    for slot, model_id in R.MODEL_BY_SLOT.items():
        pin, pout, _pcw, pcr = R._PRICE_USD[model_id]
        spec = R.MODEL_REGISTRY[slot]
        assert spec.price_in_krw_per_1m == int(pin * R.USD_KRW)
        assert spec.price_out_krw_per_1m == int(pout * R.USD_KRW)
        assert spec.price_cached_in_krw_per_1m == int(pcr * R.USD_KRW)


# ── 3. 캐시 최소 프리픽스 ──────────────────────────────────────────
def test_n7은_캐시에_걸리지_않는다():
    """n7 예산 전체(ctx 4,000 + sys 1,800 = 5,800자 ≈ 3,867tok)를 다 써도
    Haiku 4.5 최소치 4,096 을 못 넘는다. 캐시 프리픽스 재배치(T3 §1.5)는
    SMALL 슬롯에서 원리적으로 불가능하다."""
    n7_system_tokens = int(1800 / 1.5)
    assert not R.caches_prefix("SMALL", n7_system_tokens)
    n7_whole_budget_tokens = int((4000 + 1800) / 1.5)
    assert not R.caches_prefix("SMALL", n7_whole_budget_tokens)


def test_n8은_캐시에_걸린다():
    """n8 은 run 비용의 54% 다. 여기서는 캐시 재배치가 유효하다."""
    assert R.caches_prefix("LARGE", int(2500 / 1.5))


def test_thinking_기본값():
    """Opus 5 · Sonnet 5 는 thinking 생략 시 adaptive 가 돈다.
    thinking 토큰은 출력으로 과금되고 max_tokens 상한을 함께 먹는다."""
    assert R.THINKING_DEFAULT_ON["claude-opus-5"] is True
    assert R.THINKING_DEFAULT_ON["claude-sonnet-5"] is True
    assert R.THINKING_DEFAULT_ON["claude-haiku-4-5-20251001"] is False
