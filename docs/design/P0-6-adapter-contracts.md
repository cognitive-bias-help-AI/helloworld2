# P0-6 Adapter Contract Suite G2/G3

> **상태: G2 완료 / G3 승인 완료 / G4 STRICT CLOSED (2026-08-13)**
> 기준 HEAD: `d4656c7`
> 권위: `docs/DDR_v2_2_FINAL_FROZEN.md` → frozen schema/Protocol → Task Card → P0-4 reference design.

### G4 구현 결과

- MockAdapter 3 provider mode와 고정된 13개 contract method를 typed registry로 연결했다.
- Adapter `51 passed`, Gateway `19 passed`, 전체 `267 passed`, Ruff와 diff check를 통과했다.
- raw-span은 500자 hard bound와 provisional nearest-rank p95 metric을 분리했다.
- normalized coverage는 eligible denominator에 즉시 90% hard gate를 적용하고 vacuous pass를 막았다.
- mutation 16개를 각각 적용→RED→원복→GREEN으로 독립 탐지했다.
- frozen, DDR, MockAdapter, Gateway, ProviderAdapter Protocol은 변경하지 않았다.

## 1. 목적

비개발자 기준으로, 팀원이 외부 API 연결 코드를 서로 다르게 구현해도 시스템에 들어올 때는
같은 규칙을 지키는지 저장 fixture와 pytest 한 줄로 판정한다.

```text
Provider implementation
  → ProviderAdapter Protocol
  → network-free common contract suite
  → PASS
  → Gateway 연결 후보
```

PASS는 shape, ownership, fixture로 입증된 의미 품질을 뜻한다. 실제 API 가용성, 인증 성공,
금융 데이터의 항상 정확함, 모든 endpoint 구현 완료를 뜻하지 않는다.

## 2. G2 현재 상태

- P0-1~P0-5 완료. P0-4와 P0-5는 STRICT CLOSED다.
- 실제 `dart.py`, `naver.py`, `kiwoom.py`는 아직 없다.
- `MockAdapter("dart" | "naver" | "kiwoom")`가 5메서드 reference다.
- `tests/fixtures/{dart,naver,kiwoom}/`은 빈 디렉터리다.
- `tests/adapters/test_contract.py`는 없다.
- 사용자 로컬 `.claude/settings.local.json`만 untracked이며 손대지 않는다.

## 3. 책임 경계

Adapter contract가 검사하는 것은 provider 의미, `EvidenceDraft` 품질, Protocol shape,
재현성, 안전한 fixture다. 다음은 Gateway 소유이므로 검사하지 않는다.

```text
content_sha256 · evidence_id · provider_request_id · fetched_at · as_of
run-scoped dedup · EvidenceQueryLink
```

특히 hash 안정성은 `tests/gateway/test_assemble.py`에만 남긴다.

## 4. Task Card 팩트맵과 count drift

Task Card에는 실제로 13개 test method가 있다. `docs/00-status.md`와
`docs/CLAUDE_CODE_T3.md`의 “12개”는 `DOCUMENT_COUNT_DRIFT`다. 12에 맞추려고 계약을
삭제하지 않고 13개를 정본 후보로 유지한다.

| # | 계약 | 현재 근거 | G3 분류 |
|---:|---|---|---|
| 1 | parse returns `list[EvidenceDraft]` | Task Card, Protocol | Hard |
| 2 | canonical fields 부재 | DDR ownership table | Hard |
| 3 | published_at aware | frozen `AwareDatetime` | Hard |
| 4 | published_at ≤ fixture collected_at | DDR A9 | Hard |
| 5 | provider/source_type mapping | DDR S-7/I9 | Hard |
| 6 | source_url scheme | DDR A4 `^https?://` | Hard |
| 7 | raw_span p95 budget | DDR §12, fixture 20건 후 측정 | Quality |
| 8 | normalized_value coverage ≥90% | 승인 보정 | Hard |
| 9 | span_scope expected 의미 | Task Card | Hard |
| 10 | error classification | Protocol/P0-4 | Hard |
| 11 | no LLM import | D-17 | Hard |
| 12 | deterministic parse | D-15 | Hard |
| 13 | no secret in fixture | fixture safety | Hard |

## 5. 접근안 A/B/C

| 안 | 구성 | 병렬성 | 재현성 | Phase 0 | 오탐/YAGNI |
|---|---|---:|---:|---:|---:|
| A Task literal | 실제 3 Adapter 선행, p95/90% 즉시 gate | 낮음 | 중간 | scope 충돌 | 높음 |
| B staged contract-first | Mock 3 mode 먼저, 동일 registry에 실제 Adapter 후속 등록 | 높음 | 높음 | 적합 | 낮음 |
| C metric-only | 거의 모두 report | 높음 | 높음 | 가능 | 계약 의미 약화 |

**추천은 B다.** Phase 0에서 공통 suite 자체를 Mock 3 mode로 검증하고, 실제 Adapter가
생기면 test method는 수정하지 않고 case registry만 확장한다. 의미/ownership hard contract는
즉시 막고, corpus가 필요한 두 항목만 측정 조건 충족 시 hard gate로 승격한다.

## 6. 실행 시점

```text
P0-6 Phase 0: MockAdapter 3 provider cases → 동일 13개 suite 통과
T1/T2: Kiwoom/Dart/Naver 실제 cases → 같은 registry에 추가
Integration: 별도 network/auth test
```

실 Adapter skeleton을 P0-6에서 만들지 않고, 실행되지 않는 빈 suite도 만들지 않는다.

## 7. AdapterCase와 registry

B1 단순 tuple은 metadata 의미가 위치에 묻힌다. B3 pytest plugin discovery는 Phase 0에 과하다.
따라서 B2 typed case가 최소 구조다.

제안 파일:

```text
tests/adapters/test_contract.py   팀원3 고정 공통 test 13개
tests/adapters/cases.py           팀원3 registry와 typed case
tests/fixtures/dart/              팀원2 raw/metadata
tests/fixtures/kiwoom/            팀원1 raw/metadata
tests/fixtures/naver/             팀원3 raw/metadata
```

제안 타입은 signature만 확정한다.

```python
@dataclass(frozen=True)
class DraftExpectation:
    source_ref: str
    expected_span_scope: Literal["headline_snippet", "full_text", "structured_field"]
    expects_normalized_value: bool

@dataclass(frozen=True)
class AdapterContractCase:
    case_id: str
    adapter: ProviderAdapter
    query: Query
    raw: dict
    collected_at: datetime
    expectations: tuple[DraftExpectation, ...]
    fixture_paths: tuple[Path, ...]

@dataclass(frozen=True)
class AdapterErrorCase:
    case_id: str
    adapter: ProviderAdapter
    raw: dict
    expected_reason: ReasonCode
    expected_retryable: bool
    expects_rate_limit_hint: bool
```

`test_contract.py`는 `ALL_ADAPTER_CASES`와 `ALL_ERROR_CASES`만 소비한다. 실제 Adapter 추가는
provider fixture와 `cases.py` 등록만 바꾸며 test method는 바꾸지 않는다. CODEOWNERS상
`tests/adapters/cases.py`의 명시 소유권이 없으므로 G4 전에 팀원3 소유로 추가할지 승인해야 한다.

## 8. Fixture metadata와 snapshot 경계

최소 metadata는 case ID, provider, collected_at, Draft별 expected span scope와
normalized-value 의무 여부다. Query/as_of는 typed case가 소유하고 raw는 provider별 JSON에 둔다.
전체 expected `EvidenceDraft` snapshot은 parser의 정당한 내부 개선까지 고정하므로 두지 않는다.

`source_ref`는 expectation과 출력 Draft를 연결하는 fixture-local semantic key다. 한 raw가 여러 Draft를
내면 expectation과 Draft의 source_ref 집합이 정확히 같아야 하며, first/last wins는 허용하지 않는다.

## 9. Network-free 경계

Contract suite는 저장 raw를 `parse_response`, `classify_error`, `rate_limit_hint`에만 넣는다.
실 key, network, 서버 상태, rate-limit 응답, `datetime.now()`를 사용하지 않는다.

case admission에서 `isinstance(adapter, ProviderAdapter)`와 `acall`이 coroutine function인지 검사한다.
`build_request`는 fixed Query/as_of로 deterministic `Request` shape까지만 검사할 수 있으나, 실제
`acall()` 호출은 network-free 원칙 때문에 하지 않는다. HTTP 성공은 provider integration test다.

## 10. published_at와 timezone

두 실패 원인을 분리한다.

1. `published_at is None`이 아니면 timezone-aware여야 한다.
2. aware `collected_at`과 비교해 `published_at <= collected_at`이어야 한다.

비교 기준은 `Query.as_of`가 아니다. 정정공시는 과거 사실을 나중에 정정할 수 있고 장중 quote도
run anchor와 다를 수 있지만, fixture가 수집되기 뒤의 publish 시각은 정상일 수 없다. 비교 전 양쪽
datetime을 UTC instant로 보며 naive collected_at 자체도 fixture contract 위반이다.

## 11. source_url 정책

| 안 | 정책 | 평가 |
|---|---|---|
| U1 | `http://`, `https://`, `None` | frozen `HttpUrlStr`와 DDR A4 literal |
| U2 | `https://`, `None` | P0-4 reference와 보안상 강하지만 frozen보다 강화 |
| U3 | dart/naver HTTPS, kiwoom None/documented | provider별 정책을 공통 suite에 결합 |

**G3 결론은 U1이다.** DDR A4가 `^https?://`를 이미 확정했으므로 이는 재승인 선택지가 아니다.
U2/U3는 freeze amendment 없이는 P0-6에서 강제하지 않는다. P0-4 Mock의 HTTPS/None은 reference
회귀로 유지하되 모든 실제 Adapter의 공통 계약으로 승격하지 않는다.

## 12. raw_span p95

R1 단일 fixture max는 `[추정]` p95를 다른 의미로 바꾼다. R2 corpus 통계가 문서 literal이지만
DDR §12는 provider별 fixture 20건을 측정 주체와 시점으로 명시한다. R3 metric-only는 Phase 0에
맞지만 영구적으로 hard gate를 없애면 context budget 보호가 약해진다.

승인 보정에 따라 frozen hard bound `len(raw_span) <= 500`만 항상 적용한다. p95는 표본 수와
관계없이 provisional metric이며, 20건부터 threshold review eligibility만 표시한다. 자동으로
hard gate로 승격하지 않는다.

```text
news 250 · dart 150 · quote 100
p95 = nearest-rank(sorted lengths, ceil(0.95 * n))
길이 단위 = Python len(str), 즉 Unicode code point 수
```

`ctx_chars`가 JSON 문자열 문자 수를 쓰며 문서에 byte/token 근거가 없으므로 character count가
최소 일관안이다. threshold 자체는 DDR대로 20건 실측 후 갱신 대상이다.

## 13. normalized_value coverage

분모를 모든 raw나 모든 Draft로 잡으면 뉴스 같은 비수치 자료가 왜곡된다. fixture metadata가
`expects_normalized_value=True`로 선언한 Draft만 분모로 삼고, non-None이면서 빈 dict가 아닌
Draft를 분자로 삼는다. 적용 provider는 dart와 quote다.

```text
coverage = populated eligible Drafts / eligible Drafts
```

승인 보정에 따라 표본 수와 무관하게 `coverage >= 90%`를 즉시 hard gate로 적용한다. eligible이
0이면 vacuous pass가 아니라 registry contract error다. 뉴스는 분모에서 제외한다.

## 14. span_scope

S1 enum-only는 `headline_snippet`을 `full_text`로 거짓 선언해도 통과한다. S3 parser 휴리스틱은
provider 내부를 공통 test가 추측한다. **S2 fixture expectation equality**를 사용한다.

각 `source_ref`의 `expected_span_scope`와 Draft 값을 정확히 대조한다. expectation 누락·중복과
출력 Draft 누락·추가는 contract failure다.

## 15. Error classification과 timeout gap

공통 error matrix는 다음 5개다.

```text
5xx → UPSTREAM_5XX, true
429 → RATE_LIMIT, true
401 → AUTH_FAILED, false
403 → AUTH_FAILED, false
timeout sentinel → UPSTREAM_TIMEOUT, true
```

403은 P0-4 reference가 이미 포함하므로 유지한다. 현재 Protocol은 `classify_error(raw: dict)`인데
실 timeout은 exception일 수 있다. Phase 0은 Mock의 `{"timeout": true}` normalized sentinel을
검증하되, 누가 exception을 dict로 변환하는지는 정본에 없다. Protocol은 수정하지 않고
`PROTOCOL_FOLLOW_UP: TIMEOUT_NORMALIZATION_BOUNDARY`로 기록한다.

## 16. RateLimitHint

429에서는 non-None hint, 동일 provider, nonnegative optional 수치, 허용 source, 반복 호출 value
동일성을 요구한다. 429가 아니면 `None`을 요구한다. 특정 header 이름이나 retry_after 값은
provider마다 달라 공통화하지 않는다. `error_classification` test 안에서 함께 검증한다.

## 17. no LLM import

L1 string grep은 주석/문자열 오탐이 있고 L3 transitive graph는 HTTP client 내부까지 따라가 과하다.
**L2 AST direct import 검사**를 사용한다.

금지 prefix:

```text
app.models · app.prompts · app.orchestration · app.contexts
```

허용 범위는 stdlib, HTTP client, provider utility, `app.schemas`, `app.gateway.protocols`다. 검사 대상은
`type(adapter)`의 실제 source module이며 Mock과 실제 Adapter에 같은 규칙을 적용한다.

## 18. deterministic contract

동일 adapter, raw, Query로 `parse_response`를 두 번 호출하고 각 Draft의 `model_dump(mode="json")`
목록을 value equality로 비교한다. 객체 identity는 보지 않는다. 입력 raw도 deep-copy 전후가 같아야
해 parser의 in-place mutation을 막는다. 현재 시간, random, 비결정 iteration order가 결과에
들어가면 실패한다.

## 19. secret fixture 검사

K3 외부 scanner dependency는 과하고 K2 token entropy만 쓰면 정상 금융 식별자를 오탐한다.
**K1 key-name + placeholder 규칙**을 사용한다.

fixture JSON/metadata에서 case-insensitive key `authorization`, `api_key`, `appkey`, `appsecret`,
`client_secret`, `access_token`, `secret_key`를 찾는다. 값은 빈 값 또는 명시 placeholder
`<REDACTED>`, `test-placeholder`만 허용한다. `Bearer ` 뒤 실제 값과 URL query의 알려진 secret key도
거부한다. 실제 환경변수 값을 읽거나 테스트 코드에 진짜 secret을 넣지 않는다.

## 20. 기존 Mock tests와 공통 suite

| Contract | 기존 Mock test | P0-6 suite | 둘 다 유지하는 이유 |
|---|---:|---:|---|
| 5메서드/reference behavior | O | O | reference 자체 회귀 vs 구현 공통 conformance |
| Draft-only/canonical 부재 | O | O | Mock 고정 vs 모든 provider ownership |
| aware time | O | O | 동일 |
| source mapping | O | O | 동일 |
| HTTPS/None | O | U1만 | Mock reference는 더 엄격, 공통은 DDR literal |
| normalized nonempty | O | corpus metric | 단일 Mock과 실제 corpus 의미 차이 |
| 5xx/429/401/403/timeout | O | O | reference 회귀 vs 재사용 matrix |
| rate hint | O | O | 동일 |
| deterministic | O | O | 동일 |
| collected_at/p95/scope/AST/secret | 일부/없음 | O | P0-6 신규 의미 |

기존 테스트를 삭제하거나 P0-6으로 이동하지 않는다.

## 21. P0-7 I9

P0-6이 실제 source mapping contract의 정본이다. P0-7 I9는 mapping을 다시 구현하지 않고
P0-6의 `test_source_type_matches_provider` contract target을 실행하는 thin CI wrapper로 둔다.
pytest 실행 결합이 CI 구조상 부적절하면 P0-7 G3에서 test helper 추출을 별도 승인하되, 지금
production helper나 CI 코드를 만들지 않는다.

## 22. Hard Contract와 Quality Metric

Hard 12개는 한 번의 위반으로 Gateway 연결 불가다. raw-span p95만 provisional metric이다.

| # | 계약 | Phase 0 | corpus 충족 후 |
|---:|---|---|---|
| 1 | EvidenceDraft 반환 | Hard | Hard |
| 2 | canonical field 금지 | Hard | Hard |
| 3 | aware published_at | Hard | Hard |
| 4 | future vs collected_at | Hard | Hard |
| 5 | source mapping | Hard | Hard |
| 6 | URL http(s)/None | Hard | Hard |
| 7 | raw_span p95 | Metric + frozen 500 Hard | review only, auto-hardening 없음 |
| 8 | normalized coverage | 90% Hard | 90% Hard |
| 9 | expected span_scope | Hard | Hard |
| 10 | error/hint mapping | Hard | Hard |
| 11 | AST no-LLM | Hard | Hard |
| 12 | deterministic | Hard | Hard |
| 13 | fixture secret | Hard | Hard |

## 23. G4 파일과 TDD 계획

G4 승인 후에만 다음 파일을 만든다.

```text
tests/adapters/test_contract.py
tests/adapters/cases.py
tests/fixtures/{dart,naver,kiwoom}/success.json
tests/fixtures/{dart,naver,kiwoom}/errors.json
tests/fixtures/{dart,naver,kiwoom}/metadata.json
```

TDD 순서와 verify:

1. case/registry admission test RED → typed registry 최소 구현 → Mock 3 provider GREEN.
2. Draft/ownership tests RED → parse result와 canonical-field contract GREEN.
3. timezone/source/URL/scope tests RED → fixture metadata와 semantic checks GREEN.
4. error/hint matrix RED → 5개 오류와 deterministic hint GREEN.
5. AST/determinism/secret tests RED → 정적·반복·fixture safety GREEN.
6. raw-span metric 경계와 normalized 90% hard gate RED → GREEN.
7. mutation 13종을 각각 적용→RED→원복→GREEN.
8. `uv run pytest tests/adapters -q -p no:cacheprovider`.
9. `uv run pytest -q -p no:cacheprovider`, `uv run ruff check .`, `git diff --check`.
10. frozen/DDR/MockAdapter/Gateway diff 0건 확인, docs/status 동기화, 원자 커밋.

Mutation은 Evidence 반환, canonical 허용, naive/future time, source mapping, URL 정책, scope,
5xx, 429 retryability, forbidden import, now/random, secret, registry provider 누락을 각각 겨냥한다.
통계형 mutation은 해당 기준을 hard gate로 승인한 경우에만 포함한다.

## 24. Cross-card gaps와 승인 항목

Cross-card gaps:

- `DOCUMENT_COUNT_DRIFT`: 문서 12 vs Task Card 13.
- `PROTOCOL_FOLLOW_UP`: timeout exception → raw dict sentinel normalization 주체 부재.
- `QUALITY_GATE_FOLLOW_UP`: p95/coverage는 Phase 0 Mock 한 건으로 통계 의미 없음.
- `OWNERSHIP_FOLLOW_UP`: `tests/adapters/cases.py`와 `tests/fixtures/naver/` 명시 소유권 필요.

승인된 보정은 Approach B, Mock 3-provider, typed registry, HTTP(S)/None, normalized eligible 90% hard,
raw-span provisional p95, fixture semantic scope, AST/secret 검사, P0-7 I9 thin wrapper다.

DDR에서 이미 확정된 URL `http(s)/None`, collected_at 기준 미래 검사, source mapping, Gateway
ownership은 재승인 항목이 아니다.

## 25. 실제 Phase 0 측정

```text
news   count=1  p95=16  provisional=250  review_required=false
dart   count=1  p95=27  provisional=150  review_required=false
quote  count=1  p95=34  provisional=100  review_required=false

dart normalized   1/1 = 100%
quote normalized  1/1 = 100%
```
