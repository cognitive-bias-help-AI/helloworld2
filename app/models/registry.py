"""제품 LLM 슬롯 정본 — 모델 ID · 단가 · effort.

DDR v2.2 §7.5 의 `ModelSpec` 을 채우는 유일한 자리다. 노드는 슬롯 이름만 알고,
어떤 모델이 그 슬롯에 앉아 있는지 모른다(`ModelGateway.invoke(slot=...)`).

━━ 슬롯 배치 (DDR CLAUDE_CODE_T3 §1.4 배치안 A) ━━━━━━━━━━━━━━━━━━━━━━━━
  SMALL  claude-haiku-4-5-20251001   n1 · n3 · n4 · n7
  MID    claude-sonnet-5             n11
  LARGE  claude-opus-5               n8 · n9 · n10

━━ 🔴 v2.3 교정 — 문서(model_cost.py v1) 대비 바뀐 것 ━━━━━━━━━━━━━━━━━━
  C-1  Sonnet 5 정가는 $3/$15 다. 문서의 $2/$10 은 2026-08-31 만료되는 도입가다
  C-2  캐시 최소 프리픽스가 모델마다 다르다. Haiku 4.5 는 4,096 토큰이라
       SMALL 슬롯은 어느 노드도 캐시에 걸리지 않는다 (CACHE_MIN_TOKENS 참조)
  C-3  Opus 5 · Sonnet 5 는 thinking 파라미터를 생략하면 adaptive 가 기본 ON 이고
       thinking 토큰은 출력으로 과금된다. Haiku 4.5 는 adaptive 가 없어 기본 OFF
  C-4  chars_per_token(r) 은 슬롯마다 다르다. 토크나이저가 다르기 때문이다.
       budget.py 에 r 을 1개 두면 안 된다 — T1-D 가 슬롯당 20건씩 잰다
  C-5  claude-opus-5 · claude-sonnet-5 에는 날짜 스냅샷 ID 가 없다.
       이게 완전한 ID 다. Haiku 만 날짜가 붙는다
"""
from __future__ import annotations

from pathlib import Path
from typing import Final

import yaml

from app.schemas.frozen import ModelSpec

Slot = str  # "SMALL" | "MID" | "LARGE"

# ══════════════════════════════════════════════════════════════════
# 환율 — config/fx.yaml 이 정본. 여기 하드코딩하지 않는다.
# ══════════════════════════════════════════════════════════════════
_FX_PATH = Path(__file__).resolve().parents[2] / "config" / "fx.yaml"


def _load_usd_krw() -> int:
    if not _FX_PATH.exists():
        raise RuntimeError(f"환율 파일이 없다: {_FX_PATH}. config/fx.yaml 을 만들어라.")
    data = yaml.safe_load(_FX_PATH.read_text(encoding="utf-8"))
    rate = data.get("usd_krw")
    if not isinstance(rate, int) or rate <= 0:
        raise RuntimeError(f"config/fx.yaml 의 usd_krw 가 양의 정수가 아니다: {rate!r}")
    return rate


USD_KRW: Final[int] = _load_usd_krw()

# ══════════════════════════════════════════════════════════════════
# 단가 (USD per 1M tokens) — platform.claude.com/docs/en/pricing 2026-08-13 조회
# ══════════════════════════════════════════════════════════════════
# (input, output, cache_write_5m, cache_read)
_PRICE_USD: Final[dict[str, tuple[float, float, float, float]]] = {
    "claude-haiku-4-5-20251001": (1.0, 5.0, 1.25, 0.10),
    "claude-sonnet-5":           (3.0, 15.0, 3.75, 0.30),
    "claude-opus-5":             (5.0, 25.0, 6.25, 0.50),
}

# 🔴 C-1. Sonnet 5 는 2026-08-31 까지 $2/$10 도입가가 적용된다.
#    위 표는 **정가**다. 도입가로 예산을 짜면 9월 1일에 1.5배가 된다.
#    이 상수는 지우지 말 것 — 왜 $2 가 아니라 $3 인지의 근거다.
SONNET5_INTRO_PRICE_USD: Final[tuple[float, float]] = (2.0, 10.0)
SONNET5_INTRO_ENDS: Final[str] = "2026-08-31"

# 🔴 C-2. 캐시가 걸리는 최소 프리픽스(토큰). 미달이면 조용히 캐시가 안 된다.
#    n7 은 system 고정부 1,800자 ≈ 1,200토큰이라 Haiku 최소치 4,096 에 미달한다.
#    n7 예산 전체(ctx 4,000 + sys 1,800 = 5,800자 ≈ 3,867토큰)를 다 써도 못 넘는다.
#    → SMALL 슬롯에서 캐시 프리픽스 재배치(T3 §1.5)는 원리적으로 불가능하다.
#      n8(비용의 54%)에서만 유효하므로 거기에만 적용한다.
CACHE_MIN_TOKENS: Final[dict[str, int]] = {
    "claude-haiku-4-5-20251001": 4096,
    "claude-sonnet-5": 1024,
    "claude-opus-5": 512,
}

# 🔴 C-3. thinking 파라미터를 생략했을 때의 기본 동작.
#    True 인 모델은 adaptive thinking 이 돌고 thinking 토큰이 출력으로 과금된다.
#    max_tokens 는 thinking + 응답 텍스트의 합에 걸리는 상한이므로
#    여유 없이 잡으면 응답이 중간에 잘린다.
THINKING_DEFAULT_ON: Final[dict[str, bool]] = {
    "claude-haiku-4-5-20251001": False,   # adaptive 미지원
    "claude-sonnet-5": True,
    "claude-opus-5": True,
}

# ══════════════════════════════════════════════════════════════════
# effort
# ══════════════════════════════════════════════════════════════════
_EFFORT_LEVELS: Final[tuple[str, ...]] = ("low", "medium", "high", "xhigh", "max")

# 🔴 SMALL(Haiku 4.5)은 effort 를 지원하지 않는다. 보내면 400 이다.
#    reasoning_effort 는 반드시 None 이고, 게이트웨이가 한 번 더 막는다.
_EFFORT_SUPPORTED: Final[dict[str, bool]] = {
    "claude-haiku-4-5-20251001": False,
    "claude-sonnet-5": True,
    "claude-opus-5": True,
}

# 슬롯 기본 effort. ModelSpec.reasoning_effort 에 들어간다.
_SLOT_EFFORT: Final[dict[Slot, str | None]] = {
    "SMALL": None,      # 미지원 — 강제
    "MID": "low",       # n11 은 판단이 아니라 한국어 서술. 결론은 n9 가 이미 냈다
    "LARGE": "high",    # 기본값
}

# 🔴 노드별 override — LARGE 3노드는 요구가 다르다.
#    frozen.py 의 ModelSpec 은 슬롯당 effort 1개만 담을 수 있으므로
#    노드 단위 조정은 여기서 한다. 스키마 변경 0건 = 3인 approve 불필요.
#
#    n8   high    지지·반대·무관을 동시에 놓고 partial_support↔contradicted 를 가르는
#                 이 시스템 최난도 추론. 여기서 틀리면 리포트가 거짓을 인쇄한다.
#                 xhigh 로 올리지 않는 이유: n8 은 packet 12건이 고정된 경계 있는
#                 분류·인용 작업이고, 누락은 assemble_claim_evaluation 의 union 검사가
#                 이미 잡는다. 모델 등급을 올려서 얻을 것이 조립기가 하는 일과 겹친다.
#                 🔴 S1 골든셋에서 medium 을 스윕한다. n8 이 run 비용의 54% 라
#                    한 단계 내리면 전체가 크게 움직인다.
#    n9   medium  ClaimEvaluation N건 → Finding 집계. 판단은 n8 이 이미 끝냈다
#    n10  low     금지 어휘·문형 대조. 규칙에 가깝다.
#                 thinking 을 끄지 않는 이유: Opus 5 는 thinking 을 끄면
#                 <thinking> 태그가 가시 응답으로 새는 사례가 있다.
#                 effort 를 내리는 쪽이 같은 절감을 더 안전하게 얻는다
NODE_EFFORT: Final[dict[str, str]] = {
    "n8": "high",
    "n9": "medium",
    "n10": "low",
}

MODEL_BY_SLOT: Final[dict[Slot, str]] = {
    "SMALL": "claude-haiku-4-5-20251001",
    "MID": "claude-sonnet-5",
    "LARGE": "claude-opus-5",
}

BASE_URL: Final[str] = "https://api.anthropic.com"


def _krw(usd_per_1m: float) -> int:
    return int(usd_per_1m * USD_KRW)


def _build(slot: Slot) -> ModelSpec:
    model_id = MODEL_BY_SLOT[slot]
    pin, pout, _pcw, pcr = _PRICE_USD[model_id]
    effort = _SLOT_EFFORT[slot]

    if effort is not None and effort not in _EFFORT_LEVELS:
        raise ValueError(f"{slot}: effort '{effort}' 는 허용값이 아니다 {_EFFORT_LEVELS}")
    if effort is not None and not _EFFORT_SUPPORTED[model_id]:
        raise ValueError(
            f"{slot}({model_id})는 effort 를 지원하지 않는다. reasoning_effort 는 None 이어야 한다"
        )

    # 🔴 price_cached_in_krw_per_1m 은 **캐시 읽기** 단가다(T1-D 의 cost 공식이 그렇다).
    #    캐시 쓰기 단가는 ModelSpec 에 자리가 없다 — Usage.cache_write_tokens 는
    #    관측용이고 비용 공식에 안 들어간다(DDR frozen.py Usage docstring).
    #    캐시 쓰기 비용을 회계에 넣으려면 ModelSpec 필드 추가 = 3인 approve 대상이다.
    return ModelSpec(
        slot=slot,
        model_id=model_id,
        base_url=BASE_URL,
        reasoning_effort=effort,
        price_in_krw_per_1m=_krw(pin),
        price_cached_in_krw_per_1m=_krw(pcr),
        price_out_krw_per_1m=_krw(pout),
    )


MODEL_REGISTRY: Final[dict[Slot, ModelSpec]] = {
    slot: _build(slot) for slot in ("SMALL", "MID", "LARGE")
}


def effort_for(slot: Slot, prompt_version: str) -> str | None:
    """이 호출에 실제로 실릴 effort.

    prompt_version 은 "n8/v1" 형태다(DDR §7.5). 접두사로 노드 override 를 찾고,
    없으면 슬롯 기본값을 쓴다.

    노드에서 effort 를 인자로 넘기지 않는 이유: 그러면 노드마다 값이 갈라지고
    "이 판정이 어떤 설정에서 나왔는가" 를 사후에 추적할 수 없게 된다.
    prompt_version 과 effort 가 한 자리에서 결정돼야 골든셋 회귀가 성립한다.
    """
    node = prompt_version.split("/", 1)[0]
    effort = NODE_EFFORT.get(node, _SLOT_EFFORT[slot])
    if effort is None:
        return None
    if not _EFFORT_SUPPORTED[MODEL_BY_SLOT[slot]]:
        raise ValueError(
            f"{slot}({MODEL_BY_SLOT[slot]})에 effort='{effort}' 를 보내려 했다. "
            f"이 모델은 effort 를 지원하지 않는다 — 요청이 400 으로 거부된다. "
            f"NODE_EFFORT['{node}'] 를 지워라."
        )
    return effort


def caches_prefix(slot: Slot, system_tokens: int) -> bool:
    """이 system 고정부가 캐시 최소 프리픽스를 넘는가.

    False 면 cache_control 을 붙여도 아무 일도 일어나지 않는다 — 에러도 안 난다.
    비용 추정에 걸리지 않는 캐시를 넣지 않기 위한 판정이다.
    """
    return system_tokens >= CACHE_MIN_TOKENS[MODEL_BY_SLOT[slot]]
