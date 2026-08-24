# Full KRX Stock Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a validated local KRX snapshot the sole default authority for KOSPI/KOSDAQ security identity used by `StockResolver` and n2.

**Architecture:** A sync-only KRX HTTP client fetches KOSPI and KOSDAQ basic-information rows for one common `basDd`. Domain parsing validates and classifies the complete batch, atomically replaces a JSON snapshot, and a snapshot-backed resolver reuses one shared matcher with the legacy CSV demo resolver. KRX is not registered as an Evidence Provider.

**Tech Stack:** Python 3.13, Pydantic 2, httpx, pytest, existing `uv` workflow.

**Spec:** User-approved Full KRX Stock Master request and amendments in this task.

## Global Constraints

- Use only `KRX_API_KEY`; translate it to HTTP header `AUTH_KEY` at the transport boundary.
- Preserve `StockResolver`, n2, graph topology, `app/schemas/frozen.py`, and all LLM/evidence/provider contracts.
- KRX sync must never register a `ProviderAdapter` or create evidence.
- No automatic test may call KRX; fixtures must be official-style and sanitized.
- Validate both markets completely before atomic replacement; never silently fall back to the 17-row demo CSV.
- Preserve all pre-existing working-tree changes; do not commit, push, or create a PR.

---

### Task 1: Shared stock matcher and snapshot domain

**Files:**
- Create: `app/domain/stock_matcher.py`
- Create: `app/domain/stock_master.py`
- Modify: `app/domain/stock_directory.py`
- Test: `tests/domain/test_stock_master.py`
- Modify tests: `tests/domain/test_stock_directory.py`

**Interfaces:**
- Produces `StockIdentity`, `StockMasterSnapshot`, `StockMasterResolver.from_snapshot(...)`, `load_stock_master(...)`, and a shared matcher used by both resolvers.
- Alias overlay maps existing CSV codes to aliases only; canonical identity always comes from KRX.

- [ ] Write failing tests for representative resolution, alphanumeric KRX codes, validation, exclusions, unknown classification, alias conflicts, round-trip, corruption, empty master, and non-authoritative `is_managed`.
- [ ] Run focused tests and confirm failures are caused by missing stock-master behavior.
- [ ] Implement the smallest shared matcher and snapshot domain that satisfies the tests.
- [ ] Run the focused domain tests until green.

### Task 2: KRX transport and complete-batch sync

**Files:**
- Create: `providers/krx/__init__.py`
- Create: `providers/krx/client.py`
- Create: `providers/krx/parser.py`
- Test: `tests/providers/krx/test_client.py`
- Test: `tests/providers/krx/test_parser.py`
- Test: `tests/providers/krx/test_sync.py`

**Interfaces:**
- `KrxClient.fetch_basic_info(market, bas_dd)` sends `AUTH_KEY` and returns decoded `OutBlock_1` rows.
- `sync_stock_master(...)` requires one common date, validates the combined batch, then performs same-directory temporary write and `Path.replace`.

- [ ] Write failing offline transport, parser, date-window, and existing-snapshot-preservation tests.
- [ ] Confirm RED without network calls.
- [ ] Implement strict transport normalization, official-value classification, bounded seven-day common-date selection, and atomic replacement.
- [ ] Run provider/sync tests until green.

### Task 3: CLI and runtime authority

**Files:**
- Modify: `app/cli.py`
- Modify: `app/runtime/local.py`
- Modify: `.env.example` only if the canonical key is not already documented
- Test: `tests/runtime/test_local.py`
- Test: `tests/test_cli.py` or the repository's existing CLI test module
- Regress: `tests/s0/test_explicit_target_resolution.py`, `tests/s0/test_stock_resolution.py`

**Interfaces:**
- CLI `krx-master-sync [--as-of YYYYMMDD]` reads only `KRX_API_KEY`.
- Default local runtime loads `data/krx_stock_master.json`; missing, malformed, or empty snapshot fails fast.
- Explicit test/demo callers may continue injecting `CsvStockDirectory` or an explicit directory path.

- [ ] Write failing CLI and runtime-wiring tests.
- [ ] Confirm RED for missing snapshot, malformed snapshot, missing key, and wrong environment-variable name.
- [ ] Implement the minimal CLI and default runtime switch without changing n2.
- [ ] Run focused runtime, n2, and stock-choice tests until green.

### Task 4: Verification

**Files:** No production changes expected.

- [ ] Run all focused KRX/master/resolver/n2/HITL tests.
- [ ] Run `uv run pytest -q`.
- [ ] Run `uv run ruff check .`.
- [ ] Run `uv run python -m ci.invariants`.
- [ ] Run `git diff --check`, inspect `git diff --stat`, `git diff --name-only`, and `git status --short`.
- [ ] Confirm `app/schemas/frozen.py`, graph topology, provider registration, and `.claude/settings.local.json` are unchanged.
