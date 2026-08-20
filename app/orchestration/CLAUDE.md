# 그래프 · 노드 · 조립기

`state.py` / `graph.py` 는 3인 approve 없이 채널·엣지 추가 금지.
채널을 추가하려면 `tools/measure_state.py` 실측 바이트를 함께 제출한다 (CI I11).

## Production 노드 12개

```
n0  초기화·마스킹 규칙   | n1  입력가드 SMALL      | n2  종목해소 규칙
n3 의미추출은 intake_review 내부 SMALL | 질문선정·wording·resume 조립은 결정론 규칙
n5  쿼리설계 규칙(템플릿 3종) | n6  수집 규칙
n7  stance SMALL×C     | n8  검증 LARGE×C       | n9  통합 LARGE
n10 출력가드 LARGE≤2    | n11 렌더 MID           | n12 종료·차단 규칙
```

intake_review 질문선정·resume 조립과 n5를 LLM으로 만들면 예산 공식이 깨진다. 규칙이다.

## 조립기 4종 — 스키마가 못 잡는 것을 잡는 자리

```
assemble_evidence          provider↔source_type 대조 · sha256 · dedup
assemble_claim_evidence    union(stances) == packet · stance_source="llm" 주입
assemble_claim_evaluation  union(4버킷) == packet · numeric_checks 주입
assemble_findings          citations ⊆ 선언된 evidence 집합
```

불일치 → 재시도 1회 → `COVERAGE_TRUNCATED` + 배너. 조용히 통과시키지 않는다.

## 모델 슬롯

슬롯을 노드에서 직접 고르지 않는다. `ModelGateway.invoke(slot=..., prompt_version=...)` 만 쓴다.
모델 ID·단가·effort 는 전부 `app/models/registry.py` 가 정본이다.

```
SMALL = claude-haiku-4-5-20251001   effort 없음(미지원) · thinking OFF
MID   = claude-sonnet-5             effort low        · thinking adaptive
LARGE = claude-opus-5               effort high(기본) · thinking adaptive
        └ 노드별 override: n8=high · n9=medium · n10=low
```

🔴 effort 는 `prompt_version` 접두사("n8/v1" → "n8")로 조회한다.
노드에서 effort 를 인자로 넘기지 않는다 — 그러면 노드마다 값이 갈라지고
"이 판정이 어떤 설정에서 나왔는가"를 사후에 추적할 수 없게 된다.
