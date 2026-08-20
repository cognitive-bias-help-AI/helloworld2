# Semantic Batch and Model Failure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make semantic observations and claims persist atomically, and define deterministic n3-ready model-failure decisions without changing runtime wiring.

**Architecture:** Extend only `ReviewStore` with a semantic-specific batch operation, whose memory implementation validates both sides before mutating either. Add a pure orchestration policy that classifies pre-model, model, draft, semantic, and persistence outcomes; it does not invoke models or mutate State.

**Tech Stack:** Python 3.12, Pydantic, pytest, Ruff.

**Spec:** User-provided `Semantic Batch Atomic Commit + Model Failure Policy` request, attached 2026-08-19.

## Global Constraints

- Do not modify Frozen schema, `ReviewState`, graph wiring, `n3`/`n4`/`n3b`, or `.claude/settings.local.json`.
- Do not create a generic transaction, retry engine, database layer, or semantic batch entity.
- Existing `put_claims` and `put_slot_observations` semantics remain unchanged.
- A retry decision permits at most one additional model invocation (two total attempts).

---

### Task 1: Add atomic semantic persistence

**Files:**
- Modify: `app/store/protocols.py`
- Modify: `app/store/memory_review_store.py`
- Modify: `tests/protocols/test_protocols.py`
- Modify: `tests/store/test_memory_review_store.py`

**Interfaces:**
- Produces: `ReviewStore.put_semantic_batch(run_id: str, observations: list[SlotValueObservation], claims: list[Claim]) -> tuple[list[str], list[str]]`.

- [x] Write failing tests for successful mixed and observation-only batches, exact replay, ownership and payload conflicts, and intra-batch conflicts.
- [x] Run focused Store and Protocol tests and confirm the new method is absent.
- [x] Validate both collection types in local temporary maps before mutating Store backing dictionaries.
- [x] Run focused tests and confirm no partial writes remain after every failure.

### Task 2: Add pure model-failure policy

**Files:**
- Create: `app/orchestration/model_failure.py`
- Create: `tests/orchestration/test_model_failure.py`

**Interfaces:**
- Produces: `classify_model_failure(...) -> ModelFailureDecision` with failure family, actual model-attempt flag, retry permission, and terminal status.

- [x] Write failing tests for budget, gateway, draft schema, retryable and terminal semantic errors, capacity, persistence, attempt ceiling, and deterministic classification.
- [x] Run the focused test and confirm import failure.
- [x] Implement the smallest immutable Pydantic contract mapping explicit typed inputs to the required decision.
- [x] Run the focused test and confirm it passes.

### Task 3: Regressions

**Files:**
- Verify only: `tests/domain`, `tests/store`, `tests/protocols`, `tests/orchestration`, `tests/s0`, `tests/schemas`, `ci/invariants.py`

- [x] Run focused tests, full pytest, Ruff, invariants, and diff check.
- [x] Confirm protected Frozen, State, graph, and runtime-node files are unchanged.
