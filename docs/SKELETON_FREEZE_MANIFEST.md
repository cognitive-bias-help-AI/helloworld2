# Skeleton Freeze Manifest

- Freeze date: 2026-08-14 (Asia/Seoul)
- Frozen implementation HEAD: `9a678c5`
- Branch: `main`
- LangGraph: `1.2.11`
- Full pytest: `322 passed`
- Ruff: PASS
- P0 invariants: PASS, 5/5 required
- S0 invariants: PASS, 10/10 required
- Strict: exit 1; I5 PARTIAL만 non-PASS
- Actual saver maximum: `3980 bytes` across 30 observed async saver writes
- Mutation: `28/28 KILLED`, exact byte restore
- frozen schema hash (git blob SHA-1): `0dfcc8e8d49b53f9b3e1c6ddb9e867413317f438`
- DDR hash (git blob SHA-1): `5506196420a4d881594a910b669f1c564e6e977c`

## Protected contract files

- `app/schemas/frozen.py`
- `docs/DDR_v2_2_FINAL_FROZEN.md`
- `app/orchestration/state.py`
- `app/contexts/views.py`
- `app/gateway/protocols.py`
- `app/models/protocols.py`
- `app/gateway/assemble.py`
- `app/gateway/adapters/mock.py`
- `app/models/mock.py`

## File ownership map

| 영역 | 경로 |
|---|---|
| Schema | `app/schemas/frozen.py` |
| Graph | `app/orchestration/graph.py` |
| Nodes | `app/orchestration/nodes/` |
| Runtime/context/deps | `app/orchestration/runtime.py` |
| State | `app/orchestration/state.py` |
| Views/budget | `app/contexts/` |
| Provider port | `app/gateway/protocols.py` |
| Model port | `app/models/protocols.py` |
| Stores | `app/store/protocols.py` |
| Stock port | `app/domain/protocols.py` |
| Report artifact | `app/orchestration/reporting.py` |
| CI | `ci/invariants.py` |
| Mutation runner | `tools/run_s0_mutations.py` |

## Production extension ports

`ReviewStore`, `EvidenceStore`, `ProviderAdapter`, `StockResolver`, `ModelGateway`가 실제 구현 교체점이다. API/UI는 compiled graph의 `context`와 `configurable.thread_id`를 공급하는 consumer다.

## Intentional partial and follow-ups

I5 PostgreSQL physical uniqueness만 intentional PARTIAL이다. RenderCandidate durability, orchestration refresh, verdict/citation 및 numeric 후속, fetched_at provenance, hash serialization, timeout normalization, ownership 명료화는 `FINAL_SKELETON_HANDOFF.md`의 단계별 설명을 따른다.

## Do Not Modify without approval

frozen.py, DDR, State 19 channels, View exact allowlists, ProviderAdapter/ModelGateway Protocol, model output allowlist, assembler ownership, I1~I11 의미는 특별 승인 없이 바꾸지 않는다.

