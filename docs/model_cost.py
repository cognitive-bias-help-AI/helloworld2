"""제품 LLM 슬롯 비용 계산. ctx_chars 예산(v2.1a §4)과 실단가로 run 1회 비용을 낸다.

단가 출처: platform.claude.com/docs/en/about-claude/pricing (2026-08-13 조회)
환율: 1 USD = 1,415 KRW (Wise mid-market, 2026-08-13 조회) — 설정값으로 빼야 하는 값
"""
USD_KRW = 1415

PRICE = {  # (input, output, cache_write_5m, cache_read) USD per 1M tokens
    "opus-5":    (5.0, 25.0, 6.25, 0.50),
    "sonnet-5":  (2.0, 10.0, 2.50, 0.20),
    "haiku-4.5": (1.0,  5.0, 1.25, 0.10),
    "fable-5":  (10.0, 50.0, 12.50, 1.00),
}

# 노드: (slot, ctx_chars 가변부, system 고정부 chars, 출력 chars, 호출수 함수)
NODES = [
    ("n1  입력가드",   "SMALL",  2000, 1200,   400, lambda C: 1),
    ("n3  슬롯추출",   "SMALL",  6000, 2500,  2500, lambda C: 1),
    ("n4  되묻기",     "SMALL",  1500, 1200,   500, lambda C: 1),   # 대표 시나리오 1회
    ("n7  stance",     "SMALL",  4000, 1800,  1000, lambda C: C),
    ("n8  검증",       "LARGE",  4500, 2500,  1800, lambda C: C),
    ("n9  통합",       "LARGE",  5000, 2500,  3000, lambda C: 1),
    ("n10 출력가드",   "LARGE",  3000, 1800,  1200, lambda C: 1),   # 재작성 0회
    ("n11 렌더",       "MID",    3500, 1500,  4000, lambda C: 1),
]

ASSIGN = {
    "A 권장":        {"SMALL": "haiku-4.5", "MID": "sonnet-5", "LARGE": "opus-5"},
    "B 전부 Sonnet": {"SMALL": "sonnet-5",  "MID": "sonnet-5", "LARGE": "sonnet-5"},
    "C 전부 Opus":   {"SMALL": "opus-5",    "MID": "opus-5",   "LARGE": "opus-5"},
    "D LARGE=Fable": {"SMALL": "haiku-4.5", "MID": "sonnet-5", "LARGE": "fable-5"},
    "E 절약형":      {"SMALL": "haiku-4.5", "MID": "haiku-4.5", "LARGE": "sonnet-5"},
}


def run_cost(assign, C, r, cache=True):
    """r = chars_per_token. 캐시: system 고정부만 캐시 대상, 2번째 호출부터 read."""
    total_usd, rows = 0.0, []
    for name, slot, var_chars, sys_chars, out_chars, ncall in NODES:
        model = assign[slot]
        pin, pout, pcw, pcr = PRICE[model]
        n = ncall(C)
        var_tok, sys_tok, out_tok = var_chars / r, sys_chars / r, out_chars / r
        if cache and n > 1:
            cost = (sys_tok * pcw + (n - 1) * sys_tok * pcr + n * var_tok * pin
                    + n * out_tok * pout) / 1e6
        else:
            cost = (n * (sys_tok + var_tok) * pin + n * out_tok * pout) / 1e6
        total_usd += cost
        rows.append((name, slot, model, n, int(n * (sys_tok + var_tok)), int(n * out_tok), cost))
    return total_usd, rows


print("=" * 96)
print("가정: chars_per_token r = 1.5 (한국어 [추정] — S1 실측으로 교정) · 캐시 5분 · 재수집 0 · 되묻기 1")
print(f"환율 1 USD = {USD_KRW:,} KRW")
print("=" * 96)

print("\n■ 배치안별 run 1회 비용")
print(f"{'배치안':<16}{'C=4':>22}{'C=8':>22}{'월 1,000 run/일 (C=4)':>26}")
base = None
for label, a in ASSIGN.items():
    c4, _ = run_cost(a, 4, 1.5)
    c8, _ = run_cost(a, 8, 1.5)
    monthly = c4 * 1000 * 30 * USD_KRW
    if base is None:
        base = c4
    print(f"{label:<16}{'$'+format(c4,'.4f'):>10}{format(c4*USD_KRW,',.0f')+'원':>12}"
          f"{'$'+format(c8,'.4f'):>10}{format(c8*USD_KRW,',.0f')+'원':>12}"
          f"{format(monthly,',.0f')+'원':>26}")

print("\n■ 권장안(A) 노드별 내역 · C=4")
usd, rows = run_cost(ASSIGN["A 권장"], 4, 1.5)
print(f"{'노드':<16}{'slot':<7}{'모델':<12}{'콜':>4}{'입력tok':>9}{'출력tok':>9}{'USD':>10}{'KRW':>10}{'비중':>7}")
for name, slot, model, n, itok, otok, cost in rows:
    print(f"{name:<16}{slot:<7}{model:<12}{n:>4}{itok:>9,}{otok:>9,}"
          f"{cost:>10.4f}{cost*USD_KRW:>9,.0f}원{cost/usd*100:>6.1f}%")
print(f"{'합계':<16}{'':<7}{'':<12}{'':<4}{'':<9}{'':<9}{usd:>10.4f}{usd*USD_KRW:>9,.0f}원{100:>6.1f}%")

print("\n■ r 민감도 (권장안 A · C=4) — r 이 틀리면 비용이 얼마나 흔들리나")
for r in (1.2, 1.5, 2.0, 2.5):
    u, _ = run_cost(ASSIGN["A 권장"], 4, r)
    print(f"  r={r}  ${u:.4f}  {u*USD_KRW:,.0f}원   (r=1.5 대비 {u/run_cost(ASSIGN['A 권장'],4,1.5)[0]*100:5.1f}%)")

print("\n■ 캐시 효과 (권장안 A · C=8) — n7/n8 이 C회 반복되므로 여기서만 의미가 있다")
on, _ = run_cost(ASSIGN["A 권장"], 8, 1.5, cache=True)
off, _ = run_cost(ASSIGN["A 권장"], 8, 1.5, cache=False)
print(f"  캐시 사용 ${on:.4f} / 미사용 ${off:.4f} → 절감 {(1-on/off)*100:.1f}%")

print("\n■ SMALL 슬롯 단독 비교 (n7 만, C=8) — Haiku vs Sonnet5 가 실제로 얼마나 차이나나")
for m in ("haiku-4.5", "sonnet-5"):
    a = dict(ASSIGN["A 권장"]); a["SMALL"] = m
    u, rws = run_cost(a, 8, 1.5)
    n7 = [x for x in rws if x[0].startswith("n7")][0]
    print(f"  n7 {m:<11} ${n7[6]:.4f}  {n7[6]*USD_KRW:,.0f}원   전체 ${u:.4f}")

print("\n■ ModelSpec 에 넣을 원화 단가 (int, 1M 토큰당)")
print(f"{'slot':<8}{'model_id':<28}{'in':>10}{'cached_in':>12}{'out':>10}")
for slot, m in ASSIGN["A 권장"].items():
    mid = {"haiku-4.5": "claude-haiku-4-5-20251001", "sonnet-5": "claude-sonnet-5",
           "opus-5": "claude-opus-5"}[m]
    pin, pout, pcw, pcr = PRICE[m]
    print(f"{slot:<8}{mid:<28}{int(pin*USD_KRW):>10,}{int(pcr*USD_KRW):>12,}{int(pout*USD_KRW):>10,}")
