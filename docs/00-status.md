# 현재 상태 — 2026-08-13

> 이 파일만 읽으면 새 세션이 이어갈 수 있어야 한다. 대화 요약이 아니라 상태다.

## 1. 완료한 것

**저장소 골격 + 제어면 + 모델 슬롯 정본 + P0-1~P0-7.**

```
uv sync                              pydantic 2.13.3 핀 설치 완료
uv run pytest -q                     295 passed (2026-08-13 fresh 검증)
uv run ruff check .                  All checks passed
uv run python -m ci.invariants       P0 REQUIRED 5/5 PASS (exit 0)
uv run python -m ci.invariants --strict  NOT GREEN (future 6종, expected exit 1)
uv run python tools/measure_state.py C=4 3,016B · C=6 3,248B · C=8 3,480B  (DDR §5.1 재현)
```

### ✅ P0-1 완료 — `tests/schemas/test_frozen_contract.py` (70건)

```
거부 43  카드 번호 1~38 + 6c + 15의 실제 5건.  🔴 (에러 타입, 메시지 조각)까지 고정
통과 12  P1~P5 우선주 실재 코드 4건 · P8~P12 정당한 공집합
구조 13  필드 순서 · 금지 필드 부재 · ReasonCode 27 · SourceTrace 7 · 모델 30개
기타  2  건수 산술 고정 · v2.2 델타 11건(28~38) 전원 존재 확인
```

**`frozen.py` 무변경.** `git diff` 클린.

검증 2단계를 더 거쳤다:

1. **거부 사유 전수 확인** — 43건이 전부 *의도한 검증자*에서 막힌다.
   (#27만 `missing`인데 그게 카드의 "params 누락" 의도)
2. **돌연변이 검사** — S-1·S-2·S-3·S-6·S-9 검증자를 제거한 `frozen.py` 사본에서
   해당 케이스 5건이 전부 **거부에 실패**했다 → 테스트가 그 검증자를 실제로 겨냥한다.
   (`CLAUDE.md`: "테스트는 수정 전 코드에서 반드시 실패해야 유효하다")

배치된 것:

| 경로 | 내용 |
|---|---|
| `app/schemas/frozen.py` | v2.2 FROZEN. **한 글자도 안 고쳤다** |
| `app/models/registry.py` | 🆕 모델 ID · 단가 · effort 정본 |
| `config/fx.yaml` | 환율 (코드 하드코딩 금지) |
| `tools/measure_state.py` | import 경로 수정 + `--assert-under` 추가 (I11 진입점) |
| `ci/invariants.py` | 11종 골격. I11 만 구현 |
| `CLAUDE.md` ×3 | 루트 · `app/schemas/` · `app/orchestration/` |
| `.claude/` | settings.json · 훅 2 · 서브에이전트 5 · 커맨드 4 |
| `CODEOWNERS` | §9.2 소유권 |
| `docs/` | DDR · TASK_CARDS · T3 · DIAGRAMS · STATE_LIFECYCLE · model_cost(v1·v2) |

### ✅ P0-2 완료 — `app/orchestration/state.py` + 계약 테스트 25건

```
리듀서 5종     add_unique · add_unique_by_id · merge_by_slot_id · merge_dict · sum_counters
ReviewState    승인된 19채널만 유지 · evidence_ids/claim_evidence_keys 없음
merge_dict     M1 right overwrite · 동일 provider 복수 Query 집계는 n6 Gateway 내부 책임
add_unique     최초 도착 순서 보존 · I2 비교는 set semantics
검증           단위 25 passed · 전체 135 passed · Ruff 통과
예산           C=4 3,016B · C=6 3,248B · C=8 3,480B (상한 5,120B)
```

### ✅ P0-3 완료 — Context/View/Budget + Protocol 5종

```text
View             semantic 8종 + 최소 projection + GuardBatchEnvelope transport
Budget           8개 노드 상한 · payload ctx_chars · 의미 단위 ctx_items · Evidence 9+3
truncate         최오래 1 + 최신 limit-1 · ID 결정 정렬 · limit<=0 ValueError
Protocol         EvidenceStore · ReviewStore · ProviderAdapter · ReplayCache · ModelGateway
검증             contexts 22 · protocols 6 · 전체 163 passed · Ruff 통과
frozen.py        무변경
```

FOLLOW-UP: P0-4에서 EvidenceStore explicit DI와 MemoryEvidenceStore를 확정하고,
P0-7에서 I3/I4 thin CI wrapper를 구현한다.

### ✅ P0-4 STRICT CLOSED — Evidence Gateway + Memory Store

```text
MockAdapter          provider 3종 · Protocol 5메서드 · deterministic Draft-only
MemoryEvidenceStore  run-scoped hash dedup · ordering · link set semantics
MemoryReviewStore    Protocol 12메서드 · run isolation · ClaimEvaluation current upsert
assemble_evidence    explicit Store DI · fetched_at 주입 · batch/store dedup · canonical link
검증                 전체 188 passed · Ruff 통과 · frozen.py 무변경
mutation             Mock 4/4 · EvidenceStore 4/4 · ReviewStore 2/2 · Assembler 8/8
```

FOLLOW-UP: T2-D ReplayCache fetched_at provenance, hash delimiter serialization ambiguity,
P0-7 I3/I4 thin CI wrapper.

### ✅ P0-5 STRICT CLOSED — Draft → Canonical Assembler Foundation

```text
Output schema        orchestration Draft 6종 + nested item · extra-forbid · frozen
AssemblyError        4 kind · coverage/contract ReasonCode · caller/LLM retryability 분리
n7/n8/n9             packet exact coverage · allowlist · lineage/ID/time 주입 · deterministic sort
MockModelGateway     정확히 8종 Draft allowlist · BaseModel View · budget.ctx_chars Usage
검증                 P0-5 신규 28건 · 전체 216 passed · Ruff 통과 · frozen/DDR 무변경
mutation             Schema 4/4 · Error 2/2 · n7 4/4 · n8 5/5 · n9 6/6 · Mock 3/3
```

FOLLOW-UP: Node retry/store orchestration, 두 번째 coverage 실패 fallback/banner는 별도 카드다.

### ✅ P0-6 STRICT CLOSED — Adapter Contract Suite

```text
Registry          MockAdapter dart/naver/kiwoom · typed immutable cases
Contract          실제 13 method · Hard 12 · raw-span p95 provisional 1
Quality           normalized eligible 90% hard · vacuous pass 거부 · raw_span 500 hard
Boundary          network-free · AST direct import · fixture secret scan
검증              Adapter 51 passed · Gateway 19 passed · 전체 267 passed · Ruff 통과
mutation          16/16 independently detected
보호              frozen/DDR/MockAdapter/Gateway/Protocol 무변경
```

FOLLOW-UP: TIMEOUT_NORMALIZATION_BOUNDARY, registry/fixture ownership, P0-7 I9 thin wrapper.

### ✅ P0-7 G4 CLOSED — Phase-Aware CI Invariants

```text
P0 REQUIRED      I2 · I4 · I9 · I10 · I11 = 5/5 PASS
PARTIAL          I3 static budget · I5 Memory reference uniqueness
PENDING          I1 runtime checkpoint · I6 graph loop · I8 production AST scope
CONTRACT_GAP     I7 citation span application enforcement
CLI              default=p0 · --phase p0 exit 0 · --strict expected exit 1 · --only scoped
검증             CI 28 passed · 전체 295 passed · Ruff PASS · mutation 11/11
보호             frozen.py/DDR 무변경
```

Activation backlog:

```text
S0 → I1 runtime checkpoint · I3 runtime View budget · I6 loop termination
     I7 citation containment enforcement · I8 production nodes/prompts AST
T2 → I5 PostgreSQL physical UNIQUE(run_id, content_sha256)
```

기존 FOLLOW-UP 유지: TIMEOUT_NORMALIZATION_BOUNDARY, OWNERSHIP_FOLLOW_UP,
NODE_ORCHESTRATION_FOLLOW_UP, VERDICT_CITATION_BINDING, VERDICT_NUMERIC_RECONCILIATION,
T2-D fetched_at provenance, HASH_SERIALIZATION_AMBIGUITY.

## 2. 🔴 아직 안 한 것 — 다음 세션이 할 일

**P0-7까지 Phase 0 foundation gate가 닫혔다. 다음 단계는 S0 Mock Vertical Slice다.**

```
P0-1  ✅ 완료
P0-2  ✅ 완료 — state.py + 리듀서5 + 계약 테스트 25건
P0-3  ✅ 완료 — Context/View/Budget + Protocol 5종 + 계약 테스트 28건
P0-4  ✅ Evidence Gateway + Memory Store      참조 구현 완료
P0-5  Draft schema + MockModelGateway + 조립기3종 ✅ STRICT CLOSED       opus-5
P0-6  tests/adapters/test_contract.py         13개 계약 ✅ STRICT CLOSED sonnet-5
P0-7  phase-aware CI · P0 REQUIRED 5/5         ✅ G4 CLOSED               sonnet-5
```

**게이트: D+2 S0 예광탄.** Mock 어댑터·Mock LLM·in-memory ReviewStore 로 `curl` 한 번에
`report_id` 가 나와야 하고, 통과한 뒤에 계약 테스트와 함께 어댑터 작업을 인계한다.

## 3. 내가 내린 설계 결정과 이유

| # | 결정 | 이유 |
|---|---|---|
| D1 | Sonnet 5 를 **정가 $3/$15** 로 등록 | 문서의 $2/$10 은 **2026-08-31 만료** 도입가. 그대로 두면 9월 1일에 예산이 1.5배 틀어짐 |
| D2 | SMALL `reasoning_effort=None` **강제** | Haiku 4.5 는 effort 미지원 — 보내면 400. registry 가 빌드 시점에 거부 |
| D3 | effort 를 **노드별 override** (n8=high·n9=medium·n10=low) | `ModelSpec` 은 슬롯당 1개만 담는데 LARGE 3노드 요구가 다름. `prompt_version` 접두사로 조회 → `frozen.py` 변경 0건 |
| D4 | n8 = `high` (xhigh 아님) | packet 12건 고정 + 조립기 union 검사가 누락을 이미 잡음. 등급을 올려 얻을 것이 조립기와 겹침. **S1 골든셋에서 medium 스윕** |
| D5 | SMALL 슬롯 캐시 계획 **폐기** | Haiku 4.5 최소 프리픽스 4,096tok. n7 예산 전체(5,800자≈3,867tok)로도 못 넘음. T3 §1.5 재배치는 n8 에만 적용 |
| D6 | 훅을 `.sh` → `.py` | 이 머신의 `bash` 는 git-bash 가 아니라 **WSL** 로 해소됨. `.sh` 훅은 다른 파일시스템을 봄 |
| D7 | `docs/` 를 ruff 제외 | 설계 시점 스크립트. 실행 경로 아님 |
| D8 | `frozen.py` 에 `UP037`·`UP042` per-file-ignore | 고치려면 frozen 을 열어야 함. **승인 대상 목록**이지 면제가 아님 |
| D9 | `test_frozen_contract_v2_2.py` → `docs/*.reference.py` | pytest 테스트가 아니라 v2.1d↔v2.2 동시 비교 하네스. `app.schemas.frozen_v2_1d` 가 이 저장소에 없어 수집 시 전원 실패시킴 |

## 4. 확신이 없는 것

1. **thinking 토큰량.** Opus 5·Sonnet 5 는 기본 ON 이고 출력으로 과금된다.
   콜당 2,500토큰 가정 시 run 비용이 문서값(543원)의 **2.1배(1,155원)**.
   `[미측정]` — S1 실호출로 노드별 실측이 필요하다. `r` 다음으로 큰 변수다.
2. **`r`(chars_per_token)이 슬롯마다 다르다.** Opus 5 와 Haiku 4.5 는 토크나이저가 다르다.
   `budget.py` 에 상수 1개를 두면 안 된다. T1-D 는 **슬롯당** 20건씩 재야 한다.
3. **`max_tokens` 상한.** thinking + 응답 텍스트의 합에 걸린다. 여유 없이 잡으면
   n8 응답이 중간에 잘린다. 아직 아무도 값을 안 정했다.
4. **`.claude/settings.json` 훅이 이 머신에서 실제로 발화하는지** 미검증.
   `uv run python .claude/hooks/verify.py` 를 직접 돌리면 0 을 리턴하는 것까지만 확인했다.

## 5. 외부 블로커 (팀원 대기)

```
팀원1   키움 계좌 개설 + IP 등록 (리드타임 최장 — 오늘 신청) · KRX 인증키
팀원2   OpenDART 인증키 · Postgres (docker-compose 미작성)
팀원3   ANTHROPIC_API_KEY · 네이버 검색 API · Slack Webhook (알람)
```
