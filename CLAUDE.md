# 투자 판단 검토 시스템 — 팀원3 라인

사용자가 이미 내린 투자 판단을 받아, 근거를 문장에서 뽑아 명시하고,
검증 가능한 사실과 어긋나는 지점과 빠진 근거를 짚어주는 도구다. 결론은 만들지 않는다.

권위 문서는 `docs/DDR_v2_2_FINAL_FROZEN.md` 하나다. 충돌하면 그 문서가 이긴다.

## 절대 규칙

- `app/schemas/frozen.py` 는 3인 approve 없이 수정 금지. 훅이 물리적으로 막는다
- LLM function calling / tools 를 쓰지 않는다 (영구 결정). 구조화 출력은 `output_config.format`
- 특정 종목에 대한 매수·매도·보유 권유 표현을 생성하지 않는다
- 팀원1(kiwoom·stock_master·ratelimit·cost·alerts)과
  팀원2(dart·corp_code·store·replay_cache·theory_table) 파일을 열지 않는다.
  `CODEOWNERS` 가 소유권의 전부다

## 명령어

```
설치    uv sync
테스트  uv run pytest -q
린트    uv run ruff check .
불변식  uv run python -m ci.invariants
예산    uv run python tools/measure_state.py
```

🔴 이 저장소의 시스템 Python 은 깨져 있다(`Python312/` 에 `python.exe` 없음).
`python` 을 직접 부르지 말고 항상 `uv run` 을 경유한다.

## 작업 방식 (QRSPI)

코드 한 줄 쓰기 전에 구조를 먼저 정한다. 각 게이트는 내 승인을 받고 넘어간다.

```
G1 요구사항 → G2 팩트맵(목표를 모른 채 코드베이스 조사) →
G3 시그니처·파일경로만 설계(본문 금지) → G4 수직 슬라이스 구현
```

설계 시 반드시 물을 것: "이 결정 중 가장 확신이 없는 것이 무엇인가?"

## 코딩 원칙

- 추측하지 말고, 혼란을 숨기지 말고, 트레이드오프를 수면 위로 올린다
- 요청받지 않은 기능·단일 사용처 추상화·불가능한 시나리오의 에러 처리를 만들지 않는다
- 인접 코드를 "개선"하지 않는다. 내가 어지른 것만 치운다
- 200줄을 썼는데 50줄로 되면 다시 쓴다
- 테스트는 수정 전 코드에서 반드시 실패해야 유효하다

## 컨텍스트 규칙

컨텍스트 40~60% 도달 시 즉시 중단하고 `/status-compress` 로 `docs/00-status.md` 압축 → 새 세션.
방대한 빌드 로그·검색 결과를 컨텍스트 중간에 붙여넣지 않는다. 서브에이전트에 맡긴다.
