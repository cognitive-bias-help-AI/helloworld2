"""제품 LLM 슬롯 비용 계산 v2 — model_cost.py 를 API 실제 동작에 맞춰 교정.

model_cost.py(v1) 대비 교정 4건. 전부 "가정이 틀려서 비용이 과소계상되던 자리"다.

  C-1 Sonnet 5 단가      $2/$10 은 2026-08-31 만료되는 도입가다. 정가는 $3/$15.
                         v1 의 MID 슬롯 원화 단가는 18일 뒤 조용히 틀린 값이 된다.
  C-2 캐시 최소 프리픽스   모델마다 다르다. Haiku 4.5 는 4,096 토큰이다.
                         n7 의 system 고정부는 1,800자(≈1,200토큰)라 최소치 미달 →
                         SMALL 슬롯은 캐시가 아예 안 걸린다. v1 의 "절감 8.9%" 는
                         걸리지 않는 캐시를 계산에 넣은 값이다.
  C-3 thinking 기본 ON    Opus 5 와 Sonnet 5 는 thinking 파라미터를 생략하면
                         adaptive thinking 이 돈다. thinking 토큰은 출력 토큰으로 과금된다.
                         v1 의 출력 토큰(n8=1,200tok)에는 이게 빠져 있다.
                         Haiku 4.5 는 adaptive 가 없어 기본 OFF 다.
  C-4 r 은 슬롯별로 다르다  Opus 5 와 Haiku 4.5 는 토크나이저가 다르다.
                         budget.py 에 r 상수 1개를 두면 안 되고 슬롯당 3개가 필요하다.
                         (이 스크립트는 슬롯별 r 을 받도록 시그니처를 바꿔 놓았다)

단가 출처: platform.claude.com/docs/en/about-claude/pricing (2026-08-13 조회)
환율: 1 USD = 1,415 KRW — 코드에 박지 말고 config/fx.yaml 로 뺀다
"""
USD_KRW = 1415

# (input, output, cache_write_5m, cache_read) USD per 1M tokens
PRICE = {
    "opus-5":    (5.0, 25.0, 6.25, 0.50),
    "sonnet-5":  (3.0, 15.0, 3.75, 0.30),   # 🔴 C-1 정가. 도입가 $2/$10 은 2026-08-31 만료
    "haiku-4.5": (1.0,  5.0, 1.25, 0.10),
}

# 🔴 C-2 캐시가 걸리는 최소 프리픽스 길이(토큰). 미달이면 조용히 캐시가 안 된다.
CACHE_MIN_TOKENS = {"opus-5": 512, "sonnet-5": 1024, "haiku-4.5": 4096}

# 🔴 C-3 thinking 기본값. adaptive 가 있는 모델은 파라미터 생략 시 ON.
THINKING_DEFAULT_ON = {"opus-5": True, "sonnet-5": True, "haiku-4.5": False}

# 🔴 C-4 슬롯별 chars_per_token. 전부 [추정] — T1-D 가 S1 에서 슬롯당 20건씩 실측한다.
R_BY_SLOT = {"SMALL": 1.5, "MID": 1.5, "LARGE": 1.5}

# 노드: (이름, slot, ctx_chars 가변부, system 고정부 chars, 가시출력 chars, 호출수)
NODES = [
    ("n1  입력가드",   "SMALL",  2000, 1200,   400, lambda C: 1),
    ("n3  슬롯추출",   "SMALL",  6000, 2500,  2500, lambda C: 1),
    ("n4  되묻기",     "SMALL",  1500, 1200,   500, lambda C: 1),
    ("n7  stance",     "SMALL",  4000, 1800,  1000, lambda C: C),
    ("n8  검증",       "LARGE",  4500, 2500,  1800, lambda C: C),
    ("n9  통합",       "LARGE",  5000, 2500,  3000, lambda C: 1),
    ("n10 출력가드",   "LARGE",  3000, 1800,  1200, lambda C: 1),
    ("n11 렌더",       "MID",    3500, 1500,  4000, lambda C: 1),
]

ASSIGN = {
    "A 권장":        {"SMALL": "haiku-4.5", "MID": "sonnet-5", "LARGE": "opus-5"},
    "B 전부 Sonnet": {"SMALL": "sonnet-5",  "MID": "sonnet-5", "LARGE": "sonnet-5"},
    "C 전부 Opus":   {"SMALL": "opus-5",    "MID": "opus-5",   "LARGE": "opus-5"},
    "E 절약형":      {"SMALL": "haiku-4.5", "MID": "haiku-4.5", "LARGE": "sonnet-5"},
}


def run_cost(assign, C, think_tok=0, cache=True, r_by_slot=None):
    """think_tok = thinking 이 켜진 노드 1콜당 추가 출력 토큰 [추정].

    캐시는 system 고정부가 그 모델의 최소 프리픽스를 넘을 때만 걸린다(C-2).
    """
    r_by_slot = r_by_slot or R_BY_SLOT
    total_usd, rows = 0.0, []
    for name, slot, var_chars, sys_chars, out_chars, ncall in NODES:
        model = assign[slot]
        pin, pout, pcw, pcr = PRICE[model]
        r = r_by_slot[slot]
        n = ncall(C)
        var_tok, sys_tok = var_chars / r, sys_chars / r
        out_tok = out_chars / r + (think_tok if THINKING_DEFAULT_ON[model] else 0)

        cacheable = cache and n > 1 and sys_tok >= CACHE_MIN_TOKENS[model]
        if cacheable:
            cost = (sys_tok * pcw + (n - 1) * sys_tok * pcr + n * var_tok * pin
                    + n * out_tok * pout) / 1e6
        else:
            cost = (n * (sys_tok + var_tok) * pin + n * out_tok * pout) / 1e6
        total_usd += cost
        rows.append((name, slot, model, n, int(n * (sys_tok + var_tok)),
                     int(n * out_tok), cost, cacheable))
    return total_usd, rows


W = 100
print("=" * W)
print("v2 — Sonnet5 정가 · 캐시 최소치 · thinking 기본 ON 반영")
print(f"가정: r=1.5 (슬롯 공통 [추정] — S1 슬롯별 실측으로 교정) · 재수집 0 · 되묻기 1 · 환율 {USD_KRW:,}")
print("=" * W)

print("\n■ v1 vs v2 — 같은 배치안이 얼마나 달라지나 (C=4, thinking 0 토큰 가정)")
print(f"{'배치안':<16}{'v2 (thinking 0)':>20}{'v2 (thinking 2,500tok)':>26}{'증가':>10}")
for label, a in ASSIGN.items():
    c0, _ = run_cost(a, 4, think_tok=0)
    c2, _ = run_cost(a, 4, think_tok=2500)
    print(f"{label:<16}{'$'+format(c0,'.4f'):>9}{format(c0*USD_KRW,',.0f')+'원':>11}"
          f"{'$'+format(c2,'.4f'):>13}{format(c2*USD_KRW,',.0f')+'원':>13}"
          f"{c2/c0*100-100:>9.0f}%")

print("\n■ 🔴 thinking 토큰 민감도 (권장안 A) — 이게 이 프로젝트 최대 미측정 변수다")
print(f"{'think_tok/콜':>14}{'C=4':>22}{'C=8':>22}{'월 1,000run/일(C=4)':>24}")
for t in (0, 1000, 2500, 5000, 10000):
    c4, _ = run_cost(ASSIGN["A 권장"], 4, think_tok=t)
    c8, _ = run_cost(ASSIGN["A 권장"], 8, think_tok=t)
    print(f"{t:>14,}{'$'+format(c4,'.4f'):>10}{format(c4*USD_KRW,',.0f')+'원':>12}"
          f"{'$'+format(c8,'.4f'):>10}{format(c8*USD_KRW,',.0f')+'원':>12}"
          f"{format(c4*1000*30*USD_KRW,',.0f')+'원':>24}")

print("\n■ 권장안(A) 노드별 · C=4 · think_tok=2,500")
usd, rows = run_cost(ASSIGN["A 권장"], 4, think_tok=2500)
print(f"{'노드':<16}{'slot':<7}{'모델':<12}{'콜':>4}{'입력tok':>9}{'출력tok':>9}"
      f"{'KRW':>9}{'비중':>7}  캐시")
for name, slot, model, n, itok, otok, cost, cached in rows:
    flag = "적용" if cached else ("—" if n == 1 else "🔴 최소치미달")
    print(f"{name:<16}{slot:<7}{model:<12}{n:>4}{itok:>9,}{otok:>9,}"
          f"{cost*USD_KRW:>8,.0f}원{cost/usd*100:>6.1f}%  {flag}")
print(f"{'합계':<16}{'':<26}{'':<9}{'':<9}{usd*USD_KRW:>8,.0f}원{100:>6.1f}%")

print("\n■ 🔴 캐시 최소 프리픽스 판정 — 어느 노드가 캐시에 걸리는가")
print(f"{'노드':<16}{'모델':<12}{'sys고정부':>10}{'토큰(r=1.5)':>12}{'최소치':>9}{'판정':>16}")
for name, slot, var_chars, sys_chars, out_chars, ncall in NODES:
    m = ASSIGN["A 권장"][slot]
    tok = sys_chars / R_BY_SLOT[slot]
    lo = CACHE_MIN_TOKENS[m]
    verdict = "캐시 가능" if tok >= lo else f"🔴 미달 ({lo-tok:,.0f}tok 부족)"
    print(f"{name:<16}{m:<12}{sys_chars:>9,}자{tok:>11,.0f}{lo:>9,}{verdict:>16}")

print("\n■ ModelSpec 에 넣을 원화 단가 (int, 1M 토큰당) — v2 정가 기준")
print(f"{'slot':<8}{'model_id':<28}{'in':>10}{'cached_in':>12}{'out':>10}{'effort':>10}")
EFFORT = {"SMALL": "None(미지원)", "MID": "medium", "LARGE": "high"}
for slot, m in ASSIGN["A 권장"].items():
    mid = {"haiku-4.5": "claude-haiku-4-5-20251001", "sonnet-5": "claude-sonnet-5",
           "opus-5": "claude-opus-5"}[m]
    pin, pout, pcw, pcr = PRICE[m]
    print(f"{slot:<8}{mid:<28}{int(pin*USD_KRW):>10,}{int(pcr*USD_KRW):>12,}"
          f"{int(pout*USD_KRW):>10,}{EFFORT[slot]:>10}")
