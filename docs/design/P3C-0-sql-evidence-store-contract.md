# Phase 3C-0 — SQL Evidence Acquisition Store Contract

Status: **PROTOCOL_ATOMICITY_GAP**

## Phase 3C-0.1 approved resolution

The atomicity blocker is resolved at the contract/reference level by the approved
`put_evidence_batch(run_id, evidence, links)` semantic operation. A generic Unit of
Work remains rejected. The bounded amendment also establishes a non-Frozen Store error
hierarchy, a JSON-native-only acquisition boundary, `TEST_POSTGRES_DSN` for an isolated
future test database, versioned raw SQL migrations under `db/migrations/`, and
MemoryStore validation of Evidence-to-ProviderCall lineage. PostgreSQL implementation
and physical proof remain Phase 3C-1 work.

This document is an audit and design artifact. It does not implement SQL persistence.
The scope is limited to `Query`, `ProviderCall`, `Evidence`, and
`EvidenceQueryLink`.

## 1. Database stack audit

| Area | Observed repository state |
|---|---|
| Database | PostgreSQL is named by the Frozen handoff and `POSTGRES_DSN`; no database is implemented |
| Driver | `asyncpg>=0.29` is installed but unused by application persistence |
| ORM | None: no SQLAlchemy or SQLModel |
| Access mode | Application/Gateway runtime is async; no DB access layer exists |
| Configuration | `.env.example` defines `POSTGRES_DSN=postgresql://postgres:postgres@localhost:5432/review` |
| Engine/pool/session lifecycle | None |
| Models/base | None |
| Migrations/DDL | None |
| Transaction helpers | None |
| Database tests | No PostgreSQL Store tests; pytest has a `postgres` marker only |
| Docker/dev service | No Docker Compose or repository-owned PostgreSQL service definition found |
| Production bootstrap | None; Phase 3B-3.1 remains deferred |

The smallest repository-compatible implementation direction is direct asynchronous
`asyncpg`, not a new ORM. Phase 3C-1 should inject an `asyncpg.Pool` into
`SqlEvidenceStore`. One pool belongs to the future application process; each Store
operation acquires its own connection and transaction. A mutable connection or
transaction must not be shared across concurrent Reviews.

Because no migration framework exists, the initial production schema should be a
versioned, explicitly applied SQL migration, proposed as
`db/migrations/0001_evidence_acquisition.sql`. Test-only schema creation must not be
presented as production migration management. Introducing Alembic is not justified
for four direct-asyncpg tables.

## 2. EvidenceStore Protocol audit

| Method | Input | Output | Current Memory semantics | SQL-compatible as-is | Concern |
|---|---|---|---|---|---|
| `put_queries` | `run_id`, `list[Query]` | IDs in input order | Whole-batch prevalidation; exact replay; ID/run/payload conflict fails | Yes | SQL must prevalidate and use physical PK/FK constraints |
| `get_queries` | requested IDs | `list[Query]` | Requested order, duplicates preserved; missing raises `KeyError` | Yes | SQL must restore caller order explicitly |
| `put_provider_calls` | `run_id`, `list[ProviderCall]` | IDs in input order | Whole-batch prevalidation; exact replay; Query existence and run/provider/endpoint lineage | Yes | Needs independent commit after physical call |
| `get_provider_calls` | requested IDs | calls | Requested order; missing raises `KeyError` | Yes | Explicit ordinal ordering required |
| `provider_calls_for_query` | Query ID | calls | Sorted by `(created_at, provider_request_id)` | Yes | SQL `ORDER BY` required |
| `put_many` | `run_id`, `list[Evidence]` | IDs in input order | Exact ID replay; ID/run/payload conflict; unique `(run_id, content_sha256)` | Yes alone | Does not atomically create links; does not currently validate ProviderCall existence |
| `get_many` | requested IDs | evidence | Requested order, duplicates preserved; missing raises `KeyError` | Yes | Explicit ordinal ordering required |
| `find_by_sha256` | run ID, hashes | hash-to-ID map | Run-scoped lookup; missing omitted | Yes | Lookup is advisory; DB UNIQUE remains final authority |
| `link` | `list[EvidenceQueryLink]` | `None` | Both parents must exist; duplicate pair is idempotent | Yes alone | Separate call cannot guarantee Evidence+Link atomicity |
| `evidence_ids_for_claim` | Claim ID | evidence IDs | Query claim ownership traversal; sorted unique IDs | Yes | Claim is not stored here; uses nullable `Query.claim_id` only |
| `evidence_ids_for_queries` | Query IDs | evidence IDs | Sorted unique IDs | Yes | Empty input returns empty list |

**Protocol change required: YES.** The existing calls are individually SQL-compatible,
but the Protocol cannot express the required atomic transaction for Evidence adoption
and its intended `EvidenceQueryLink` rows. Keeping an implicit transaction open between
two unrelated public calls would require request-local hidden connection state, would
be unsafe under concurrency, and would make failure cleanup ambiguous.

The smallest future amendment is one semantic operation:

```python
async def put_evidence_batch(
    self,
    run_id: str,
    evidence: list[Evidence],
    links: list[EvidenceQueryLink],
) -> list[str]: ...
```

It must prevalidate the complete batch, insert/replay/deduplicate Evidence, insert all
links, and commit once. `put_many` and `link` remain for compatibility and independent
queries, but production adoption must use the atomic method. This amendment is not
implemented in Phase 3C-0.

## 3. Current MemoryStore semantics

### Query

- Canonical identity: `query_id`; Store-owned run ownership is separate from the model.
- Exact same model and run replay is idempotent.
- Same ID with a different model or run fails closed.
- Batch conflicts are detected before mutation.

### ProviderCall

- Canonical identity: `provider_request_id`.
- The referenced Query must already exist.
- `run_id`, provider, and endpoint must agree with the Query and Store call.
- Multiple calls per Query and repeated `idempotency_key` values are valid.
- Query reads are ordered by `(created_at, provider_request_id)`.

### Evidence

- Canonical identity: `evidence_id`; Store-owned run ownership is separate from the model.
- Exact same ID/model/run replay is idempotent.
- `(run_id, content_sha256)` maps to one Evidence identity.
- Current `put_many` does not reject a dangling `provider_request_id`; SQL must provide
  the Frozen-requested FK and Phase 3C parity tests should also strengthen MemoryStore
  in the implementation phase, after approval.

### EvidenceQueryLink

- Identity is the pair `(evidence_id, query_id)`.
- Duplicate pairs are idempotent.
- Both parents must exist.
- Memory uses a set and exposes no mutation or deletion API.

## 4. Approved architecture

```text
Gateway / assembler
        |
        v
EvidenceStore Protocol
     /             \
MemoryEvidenceStore  SqlEvidenceStore
                         |
                         v
                    asyncpg Pool
```

Frozen Pydantic models remain canonical. SQL rows are storage representations and are
always reconstructed through the Frozen constructors. The Gateway must not branch on
Store implementation, execute SQL, own DB transactions, or translate provider errors
into database errors.

## 5. Alternatives

### A. Protocol plus SqlEvidenceStore — recommended with one atomic amendment

- Benefits: preserves storage independence, shared Store contract tests, Memory test
  speed, and current orchestration boundaries.
- Drawbacks: requires one narrowly scoped Protocol amendment and SQL error mapping.
- Complexity: moderate and bounded to four entities.
- Compatibility: highest; existing methods keep their semantics.

### B. SQL directly inside Gateway — rejected

- Benefits: superficially easy access to one transaction.
- Drawbacks: couples provider execution to PostgreSQL, damages testability, and mixes
  provider, canonicalization, and persistence ownership.
- Complexity: grows with every provider and failure path.
- Compatibility: violates the approved Protocol boundary.

### C. Replace MemoryStore with SQL-only Store — rejected

- Benefits: one runtime backend.
- Drawbacks: slow and infrastructure-dependent unit tests; removes the reference
  semantic implementation.
- Complexity: unnecessary CI and local-development cost.
- Compatibility: breaks existing tests and dependency injection.

### D. Persist the entire Review domain at once — rejected

- Benefits: could eventually provide broader durability.
- Drawbacks: combines unrelated Claim, HITL, Finding, Report, State, and acquisition
  contracts before the acquisition boundary is proven.
- Complexity: unbounded for Phase 3C.
- Compatibility: violates the four-entity scope.

## 6. Proposed SQL schema

PostgreSQL table names are plural snake case because no current SQL naming convention
exists. `run_id` on Query and Evidence is Store-owned metadata needed to preserve
Memory semantics.

### `acquisition_queries`

| Canonical source | SQL type | Nullable | Constraint | Serialization note |
|---|---|---:|---|---|
| `query_id` | `varchar(26)` | No | PK | ULID text |
| Store `run_id` | `text` | No | indexed | Ownership, not a new Frozen field |
| `scope` | `text` | No | CHECK `claim,stock` | Literal text |
| `claim_id` | `varchar(26)` | Yes | indexed | No Claim FK in this bounded Store |
| `intent` | `text` | No | CHECK `verify,counter,context` | Literal text |
| `provider` | `text` | No | CHECK `dart,naver,kiwoom` | Literal text |
| `endpoint` | `text` | No |  | Nonblank validated by Frozen reconstruction |
| `params` | `jsonb` | No |  | Canonical JSON codec required |
| `created_at` | `timestamptz` | No |  | Must reconstruct aware datetime |

### `provider_calls`

| Canonical source | SQL type | Nullable | Constraint | Serialization note |
|---|---|---:|---|---|
| `provider_request_id` | `varchar(26)` | No | PK | Physical-attempt identity |
| `run_id` | `text` | No | indexed | Must equal referenced Query run |
| `provider` | `text` | No | CHECK | Must equal referenced Query provider |
| `endpoint` | `text` | No |  | Must equal referenced Query endpoint |
| `query_id` | `varchar(26)` | No | FK to Query | Many calls per Query allowed |
| `http_status` | `smallint` | Yes | CHECK 100–599 | Exact integer |
| `latency_ms` | `bigint` | No | CHECK >= 0 | Exact integer |
| `cache_hit` | `boolean` | No |  | Default is application-owned |
| `reason_code` | `text` | Yes | CHECK approved values | Reconstruct Frozen enum |
| `idempotency_key` | `char(64)` | No | indexed, not UNIQUE | Lowercase SHA-256 |
| `created_at` | `timestamptz` | No |  | Aware datetime |

To make run/provider/endpoint ownership concurrency-safe, Query additionally needs a
candidate key `UNIQUE(query_id, run_id, provider, endpoint)`, and ProviderCall uses a
composite FK over those four columns. The simple `query_id` FK remains logically
redundant and need not be duplicated when the composite FK already includes it.

### `evidence`

| Canonical source | SQL type | Nullable | Constraint | Serialization note |
|---|---|---:|---|---|
| `evidence_id` | `varchar(26)` | No | PK | ULID text |
| Store `run_id` | `text` | No | part of UNIQUE | Ownership metadata |
| `source_type` | `text` | No | CHECK `dart,news,quote` | Literal text |
| `source_ref` | `text` | No |  | Nonblank |
| `source_url` | `text` | Yes |  | Revalidated on read |
| `publisher` | `text` | Yes |  |  |
| `published_at` | `timestamptz` | Yes |  | Aware if present |
| `fetched_at` | `timestamptz` | No |  | Aware |
| `raw_span` | `text` | No | length <= 500 | Frozen validation remains final |
| `span_scope` | `text` | No | CHECK approved literals | Literal text |
| `content_sha256` | `char(64)` | No | `UNIQUE(run_id, content_sha256)` | Lowercase SHA-256 |
| `normalized_value` | `jsonb` | Yes |  | Canonical JSON codec required |
| `provider_request_id` | `varchar(26)` | No | FK to ProviderCall | Physical lineage |
| `as_of` | `timestamptz` | No |  | Aware |

Evidence needs the application validation
`Evidence.run ownership == ProviderCall.run_id`. A concurrency-safe physical form is
a candidate key `UNIQUE(provider_request_id, run_id)` on ProviderCall and a composite
FK from Evidence `(provider_request_id, run_id)`. Provider/source lineage is already
validated before canonical assembly; it remains application validation because
Evidence source type intentionally differs from provider name.

### `evidence_query_links`

| Canonical source | SQL type | Nullable | Constraint | Serialization note |
|---|---|---:|---|---|
| `evidence_id` | `varchar(26)` | No | PK part, FK to Evidence | No surrogate ID |
| `query_id` | `varchar(26)` | No | PK part, FK to Query | No surrogate ID |

Primary key: `(evidence_id, query_id)`. The atomic Store operation validates that both
parents belong to the supplied run. SQL FKs establish existence; Store validation and
composite ownership constraints establish run consistency.

## 7. Decision table

| Concern | Query | ProviderCall | Evidence | EvidenceQueryLink |
|---|---|---|---|---|
| Primary identity | `query_id` | `provider_request_id` | `evidence_id` | `(evidence_id, query_id)` |
| Replay | exact ID/run/payload is idempotent | exact ID/run/payload is idempotent | exact ID/run/payload is idempotent | duplicate exact pair is idempotent |
| Conflict | same ID, different run/payload | same ID, different payload/ownership | same ID, different payload/run | no mutable payload |
| FK | none within scope | Query | ProviderCall | Evidence and Query |
| Physical uniqueness | PK; ownership candidate key | PK; idempotency key not unique | PK plus `(run_id, hash)` | composite PK |
| Transaction boundary | Query batch commit | independent call batch commit | atomic with intended links | atomic with Evidence adoption |
| Deterministic read | requested ordinal | requested ordinal or time/ID | requested ordinal | sorted Evidence ID projections |

## 8. Replay and content-dedup contracts

- Query: insert if absent; on PK collision read and reconstruct the existing row. Exact
  model plus run returns successfully; any difference raises Store conflict.
- ProviderCall: same rule. Never `DO UPDATE`. Multiple physical IDs per Query and
  repeated idempotency keys are valid.
- Evidence identity replay: same evidence ID/run/payload is idempotent; changed payload
  fails closed.
- Evidence content dedup: `(run_id, content_sha256)` is a separate identity rule. A
  collision reuses the existing Evidence only when its canonical payload is compatible;
  conflicting payload for the same hash fails closed.
- EvidenceQueryLink: `INSERT ... ON CONFLICT DO NOTHING` is safe because the row has no
  mutable payload and its complete identity is the pair.

The database constraints, not `SELECT` prechecks, are the final concurrent-write
authority. After an insert conflict the Store reads and compares the canonical model;
it never silently updates canonical facts.

## 9. Transaction boundaries and required flow

```text
put_queries
    -> validate full Query batch
    -> transaction: insert/replay all Queries
    -> COMMIT

physical adapter.acall()

put_provider_calls
    -> transaction: validate Query lineage, insert/replay ProviderCall
    -> COMMIT

parse and deterministically assemble Evidence

put_evidence_batch (future minimal Protocol amendment)
    -> prevalidate Evidence, links, ownership and duplicate conflicts
    -> transaction: insert/replay/deduplicate Evidence
                    insert/replay every intended link
    -> COMMIT
```

Current n5 persists Queries through `put_queries` before n6 loads them and executes
providers, so Query-before-call is already structurally present.

ProviderCall is an independently committed fact. A later parse, validation, Evidence,
or link failure must not erase the record that a physical attempt occurred. SQL
rollback affects only the current database transaction; it cannot undo a DART, Kiwoom,
or other external request.

Evidence without any intended link is not a valid successful adoption state. This
cannot be enforced with a simple parent FK, so complete-batch validation plus the
atomic Evidence/link transaction owns the invariant. ProviderCall without Evidence is
valid for failure, timeout, no-result, parsing, or canonicalization outcomes.

## 10. Protocol atomicity audit

**Can Evidence and EvidenceQueryLink currently be persisted atomically? NO.**

Evidence: `assemble_evidence()` calls `put_many()` and then `link()`. There is no
transaction object, unit-of-work token, or combined Store operation. A SQL Store that
commits each method can leave Evidence without intended lineage when `link()` fails.
Keeping a transaction open implicitly between these calls is not safe because the
Protocol provides no lifecycle, cancellation, or concurrency ownership boundary.

Smallest correction: approve and add only `put_evidence_batch` as specified above,
implement it in both Memory and SQL Stores, and switch deterministic assembly to it.
No generic Unit of Work or transaction framework is warranted.

## 11. Concurrent-write policy

- Duplicate Evidence: physical `UNIQUE(run_id, content_sha256)` admits one row. Losing
  writers read/compare and reuse the canonical deterministic Evidence ID.
- Exact ProviderCall replay: PK admits one row; losing writer compares and succeeds.
- Conflicting ProviderCall: existing row remains unchanged; conflicting writer receives
  a typed Store conflict.
- Links: composite PK makes duplicate replay harmless and permits many Queries per
  Evidence.
- Race protection: constraints are final; application validation improves diagnostics.
- No last-write-wins update is allowed.

Direct asyncpg can use `INSERT ... ON CONFLICT DO NOTHING RETURNING ...`, followed by a
same-transaction `SELECT` and canonical comparison. `DO UPDATE` is prohibited for the
three factual entities.

## 12. Serialization and round trip

- Enums/literals: store their string values; reconstruct through Frozen models.
- Datetime: PostgreSQL `timestamptz`; reject naive values on write and read. Frozen
  requires timezone awareness, not a new UTC-only contract. Equality tests must cover
  equivalent instants and the current Pydantic semantics.
- Decimal/numeric: scalar acquisition fields are integers, booleans, or strings; no
  top-level Decimal exists. `params` and `normalized_value` are `dict[str, Any]`, so
  Decimal and non-JSON-native types are not currently forbidden. Binary float coercion
  is prohibited.
- JSON: use JSONB only with an approved canonical codec. Object key order must not affect
  conflict comparison. Compare reconstructed Frozen models, not raw JSON text.
- Nullable: preserve SQL NULL distinctly from empty strings/objects.
- Hashes/IDs: store validated text without case mutation or truncation.

Before 3C-1, approve one JSON policy: restrict persisted dicts to JSON-native values via
Store validation, or define a lossless tagged codec for non-native values. The smaller
recommendation is a JSON-native recursive value contract plus tests; it requires no
Frozen field change but must fail closed instead of coercing Decimal to float.

## 13. Deterministic retrieval

- `get_queries(ids)`: join against caller ordinality and `ORDER BY ordinal`; preserve
  repeated IDs and raise `KeyError` if any requested ID is absent.
- `get_provider_calls(ids)`: same caller-ordinal rule.
- `provider_calls_for_query(query_id)`: `ORDER BY created_at, provider_request_id`.
- `get_many(ids)`: caller ordinality and `ORDER BY ordinal`.
- `find_by_sha256(run_id, hashes)`: result is a map; lookup order is not semantic, but
  duplicate input hashes must not change the mapping.
- `evidence_ids_for_claim(claim_id)`: `SELECT DISTINCT ... ORDER BY evidence_id`.
- `evidence_ids_for_queries(query_ids)`: `SELECT DISTINCT ... ORDER BY evidence_id`.

Completion timing, insertion order, and physical row order are never canonical.

## 14. Error mapping

Raw asyncpg exceptions must terminate at `SqlEvidenceStore`.

| Condition | Future application-level mapping |
|---|---|
| Exact replay/duplicate link | Success/idempotent result |
| Canonical payload or run conflict | Typed Store conflict, compatible with current fail-closed `ValueError` behavior |
| FK/lineage violation | Typed Store contract/lineage failure |
| DB unavailable | Typed persistence-unavailable failure |
| Transaction/serialization failure | Typed persistence-operation failure |

The repository currently has no typed persistence error taxonomy. Gateway contract
errors and Frozen `ReasonCode` values must not be reused for a database outage. This is
an **ERROR_TAXONOMY_GAP** subordinate to the atomicity blocker. Phase 3C-1 requires a
small non-Frozen Store error hierarchy before SQL errors can be mapped safely.

## 15. Crash windows and limitations

- Call before ProviderCall commit: the remote call may occur and the process may crash
  before its audit record commits. SQL alone cannot provide exactly-once external-side-
  effect recording; request-intent/outbox protocols are outside scope.
- Query without ProviderCall: valid. It means acquisition was planned but no durable
  physical attempt was recorded.
- ProviderCall without Evidence: valid and required for failures and invalid results.
- Evidence/link partial state: prohibited for successful adoption by the future atomic
  batch. A transaction rollback removes both its new Evidence and links, but never
  reverses the external API call or the already committed ProviderCall.
- A committed database record is durable subject to PostgreSQL guarantees; this is not
  a claim of exactly-once provider execution.

## 16. Protocol compatibility matrix

| Operation | Memory behavior | SQL intended behavior | Same contract |
|---|---|---|---|
| `put_queries` | prevalidated exact replay/conflict | transaction plus PK compare | Yes |
| `get_queries` | caller order, `KeyError` | ordinal query, mapped missing | Yes |
| `put_provider_calls` | lineage plus exact replay | independent transaction plus FK/compare | Yes |
| `get_provider_calls` | caller order | ordinal query | Yes |
| `provider_calls_for_query` | time/ID sort | explicit `ORDER BY` | Yes |
| `put_many` | evidence-only batch | evidence-only transaction | Yes, but not adoption-safe alone |
| `get_many` | caller order | ordinal query | Yes |
| `find_by_sha256` | run-scoped map | indexed run/hash query | Yes |
| `link` | parent validation, set replay | FK plus composite-PK replay | Yes, but not adoption-safe alone |
| `evidence_ids_for_claim` | sorted unique | DISTINCT plus ORDER BY | Yes |
| `evidence_ids_for_queries` | sorted unique | DISTINCT plus ORDER BY | Yes |
| future `put_evidence_batch` | not present | one Evidence/link transaction | Amendment required |

## 17. Test strategy for Phase 3C-1 / 3C-2

Run common semantic Store tests against Memory and SQL where applicable, then use real
PostgreSQL tests for physical constraints and races. SQLite is not acceptable evidence
for PostgreSQL uniqueness, FK, isolation, or concurrent-write behavior.

- S1 Query write, new Store/session read, semantic equality.
- S2 exact Query replay is idempotent.
- S3 changed Query payload/run conflicts without mutation.
- S4 ProviderCall round trip.
- S5 exact ProviderCall replay.
- S6 ProviderCall identity conflict.
- S7 multiple physical calls for one Query persist.
- S8 retry calls share idempotency key but have distinct physical IDs.
- S9 Evidence round trip including nullable fields and structured JSON.
- S10 same run/hash yields one canonical Evidence.
- S11 same hash in different runs remains independently owned.
- S12 Evidence ID/payload conflict fails closed.
- S13 one Evidence links to multiple Queries.
- S14 duplicate link replay is idempotent.
- S15 dangling ProviderCall Query and dangling link Query are rejected by PostgreSQL.
- S16 Evidence with missing ProviderCall and link with missing Evidence are rejected.
- S17 run/provider/endpoint/query ownership mismatch is rejected.
- S18 concurrent identical Evidence adoption leaves one physical Evidence row and all
  valid links.
- S19 concurrent exact ProviderCall replay leaves one equivalent row.
- S20 concurrent conflicting ProviderCall replay preserves one canonical row and fails
  the conflicting writer.
- S21 injected failure during Evidence/link transaction leaves no partial adoption.
- S22 committed ProviderCall remains after later Evidence transaction failure.
- S23 discard Store A, construct Store B on the same DB, and read all canonical data.

Additional mandatory coverage: requested-order reads with duplicates, missing-ID error
mapping, aware datetime reconstruction, JSON key-order equality, non-native numeric
fail-closed behavior, batch all-or-none prevalidation, and no delete/update API.

## 18. Future implementation boundary

Proposed Phase 3C-1 files, subject to approval:

- `app/store/errors.py` — minimal non-Frozen Store conflict/lineage/infrastructure errors.
- `app/store/sql_evidence_store.py` — asyncpg implementation and row/model mapping.
- `db/migrations/0001_evidence_acquisition.sql` — four tables and constraints.
- `tests/store/evidence_store_contract.py` — backend-neutral semantic suite.
- `tests/store/test_sql_evidence_store.py` — new-session round trip.
- `tests/store/test_sql_evidence_store_postgres.py` — real PostgreSQL constraints,
  transactions, and concurrent writers, marked `postgres`.

Required minimal existing-file changes in a later approved phase:

- `app/store/protocols.py` — add only `put_evidence_batch`.
- `app/store/memory_evidence_store.py` — semantic reference implementation.
- `app/gateway/assemble.py` — replace the separate adoption writes with the atomic call.

No ReviewStore, graph, State, Frozen, provider, Intake, Claim, Finding, or Report SQL
work belongs to Phase 3C-1.

## 19. Open blockers before Phase 3C-1

1. Approve the minimal `put_evidence_batch` Protocol amendment and assembler use.
2. Approve a non-Frozen Store error hierarchy; do not map DB outages to provider errors.
3. Approve the recursive JSON-native persistence policy or a lossless alternative.
4. Provide real PostgreSQL test infrastructure/DSN; do not substitute SQLite proof.
5. Approve versioned raw SQL migration ownership and application procedure.
6. Production composition remains deferred: a future bootstrap must own the asyncpg
   pool, SqlEvidenceStore, ProviderAdmissionController, and reusable RuntimeDeps.

## 20. Phase 3C-1 implementation resolution

Phase 3C-1 implements the approved boundary with direct `asyncpg`, explicit caller-owned
pool injection, and `db/migrations/0001_evidence_acquisition.sql` as the sole schema
artifact. `SqlEvidenceStore` implements the existing twelve-method `EvidenceStore`
Protocol without changing it. Query and ProviderCall batches use independent
transactions; canonical Evidence and its intended links use one atomic transaction.

The PostgreSQL suite covers S1-S23 semantics, physical PK/FK/UNIQUE enforcement,
concurrent exact/conflicting replay, transaction rollback, JSON/datetime reconstruction,
and new-pool visibility. These tests use only `TEST_POSTGRES_DSN`, refuse a DSN equal to
`POSTGRES_DSN`, and apply the production migration file. Until that suite is actually run
against PostgreSQL, I5 remains PARTIAL and production composition/migration execution
remain deferred.
