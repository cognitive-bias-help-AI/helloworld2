# 스키마 — 수정 금지 영역

`frozen.py` 는 3인 approve 없이 수정 금지. 필드 추가는 approve 만으로 가능.
기존 필드의 의미 변경 금지 — 새 필드를 만든다. Enum 값 삭제·문자열 변경 금지.

## Draft / canonical 4쌍

권한 없는 주체가 채울 수 없는 필드는 애초에 갖지 않는다.

```
EvidenceDraft        -> Evidence           어댑터는 evidence_id·sha256·as_of 를 모른다
ClaimEvidenceDraft   -> ClaimEvidence      LLM 은 stance_source="rule" 을 선언할 수 없다
ClaimEvaluationDraft -> ClaimEvaluation    LLM 은 computed_by="rule" 을 선언할 수 없다
ClaimStanceDraft     = n7 output_schema    invoke 가 BaseModel 1개를 받으므로 감싼다
```

canonical 4종을 LLM output_schema 로 지정하지 않는다 (CI I8):
`Evidence` · `ClaimEvidence` · `ClaimEvaluation` · `Finding`

## 🔴 `ModelSpec.reasoning_effort` 는 자유 문자열이지만 값은 5개뿐이다

`NonBlankStr | None` 로 선언돼 있어 스키마가 오타를 못 잡는다.
허용값은 `low` `medium` `high` `xhigh` `max` 이고, 검증은
`app/models/registry.py` 의 `_EFFORT_LEVELS` 가 한다.

그리고 **SMALL 슬롯(Haiku 4.5)은 effort 를 지원하지 않는다 — 보내면 400 이다.**
`reasoning_effort` 는 반드시 `None` 이어야 하고 게이트웨이가 한 번 더 막는다.
스키마를 `Literal` 로 조이지 않는 이유: 그건 3인 approve 대상이고,
값 하나 때문에 frozen 을 여는 것보다 registry 에서 막는 편이 싸다.
