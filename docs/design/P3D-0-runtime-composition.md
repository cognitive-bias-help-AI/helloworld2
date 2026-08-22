# Phase 3D-0 Application Runtime Composition

## Purpose

`app.runtime.composition` is the first explicit application-scope owner for the
already-approved runtime dependencies. It constructs resources and wires existing
contracts; it does not own Review business behavior.

## Lifecycle scopes

| Resource | Owner | Lifetime | Shared across Reviews? | Cleanup |
|---|---|---|---|---|
| asyncpg Pool | `ApplicationRuntime` composition | one runtime context | Yes | closed on context exit |
| `SqlEvidenceStore` | `ApplicationRuntime` composition | one runtime context | Yes | uses the owned Pool; no separate close |
| `ProviderAdmissionController` | `ApplicationRuntime` composition | one runtime context | Yes | in-memory semaphores need no close |
| Provider adapters | caller | caller-defined | Yes, by reference | caller-owned; Protocol has no close contract |
| `RuntimeDeps` | `ApplicationRuntime` composition | one runtime context | Yes | no separate cleanup |
| compiled Graph | `ApplicationRuntime` composition | one runtime context | Yes | no separate cleanup contract |
| injected checkpointer | caller | caller-defined | Yes through Graph | caller-owned and not closed here |
| `ReviewState` and run ID | each Graph invocation | one Review execution | No | invocation/checkpointer policy |
| Query/ProviderCall/Evidence/Link rows | PostgreSQL | durable database lifetime | Canonical shared data | never deleted by runtime shutdown |

## Startup

The caller supplies an explicit PostgreSQL DSN and already-configured adapters and
domain dependencies. Composition never reads `POSTGRES_DSN` or `TEST_POSTGRES_DSN`.

1. Validate the DSN and adapter ownership/concurrency authority.
2. Create one process-local `ProviderAdmissionController` from each configured
   adapter's `max_concurrency`.
3. Create one asyncpg Pool.
4. Wrap it in one `SqlEvidenceStore`.
5. Build one immutable `RuntimeDeps` value.
6. Compile one Graph, passing through the optional caller-owned checkpointer.
7. Yield the minimal `ApplicationRuntime(deps, graph, pool)` surface.

If any step after Pool creation fails, composition closes the Pool and re-raises the
original exception. It does not translate startup failures into provider or Store
semantic errors.

## Shutdown

The caller must stop accepting new Reviews and allow in-flight Reviews to settle before
leaving the context. Phase 3D-0 has no HTTP/task-draining owner. Context exit closes the
owned Pool. It does not close caller-owned adapters/checkpointers or delete persisted
records.

## Graph and Review isolation

The compiled Graph captures reusable dependencies, not mutable Review state. Each
`ainvoke` supplies its own state, run ID, context, and checkpointer thread configuration.
Existing compiled-Graph interrupt/resume and state tests remain the behavioral authority
for per-Review state isolation.

## Provider construction boundary

Credential loading and live adapter construction remain external inputs. Current adapter
contracts expose no `close`/`aclose` lifecycle, so composition does not invent one. The
shared admission controller is nevertheless derived from configured adapter authority and
is reused by every Review in the runtime.

## Migration separation and remaining shell gap

Runtime startup assumes `db/migrations/0001_evidence_acquisition.sql` has already been
applied. It performs no DDL, migration discovery, retry, health query, or schema mutation.
A production migration runner and an executable application shell such as ASGI or a worker
remain separate deployment concerns.
