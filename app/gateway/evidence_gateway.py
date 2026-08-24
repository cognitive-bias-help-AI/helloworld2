"""Provider-agnostic sequential Evidence collection boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from time import perf_counter

from app.diagnostics import debug_log
from app.gateway.admission import ProviderAdmissionController
from app.gateway.assemble import ContractViolation, assemble_evidence
from app.gateway.execution import ProviderExecutionError
from app.gateway.protocols import ProviderAdapter
from app.schemas.frozen import (
    PROVIDER_SOURCE_TYPE,
    CollectionResult,
    NodeStatus,
    ProviderCall,
    Query,
    ReasonCode,
    Request,
)
from app.store.protocols import EvidenceStore

MAX_ATTEMPTS = 2
_RETRYABLE_REASONS = frozenset(
    {ReasonCode.UPSTREAM_TIMEOUT, ReasonCode.UPSTREAM_5XX}
)


def should_retry(reason_code: ReasonCode, *, retryable: bool, attempt: int) -> bool:
    return retryable and reason_code in _RETRYABLE_REASONS and attempt < MAX_ATTEMPTS


class GatewayBudgetExceeded(RuntimeError):
    reason_code = ReasonCode.BUDGET_EXCEEDED


class GatewayContractError(RuntimeError):
    reason_code = ReasonCode.CONTRACT_VIOLATION

    def __init__(
        self, message: str, *, provider_calls: tuple[ProviderCall, ...] = ()
    ):
        self.provider_calls = provider_calls
        self.provider_call = provider_calls[-1] if provider_calls else None
        super().__init__(f"{self.reason_code.value}: {message}")


@dataclass(frozen=True)
class ProviderFailure:
    query_id: str
    provider: str
    reason_code: ReasonCode
    retryable: bool


@dataclass(frozen=True)
class GatewayResult:
    collections: dict[str, dict]
    external_calls: int
    provider_calls: tuple[ProviderCall, ...]
    failures: tuple[ProviderFailure, ...]


@dataclass(frozen=True)
class _Prepared:
    query: Query
    adapter: ProviderAdapter
    request: Request


def _provider_call(run_id: str, query: Query, clock, id_factory) -> ProviderCall:
    return ProviderCall(
        provider_request_id=id_factory(),
        run_id=run_id,
        provider=query.provider,
        endpoint=query.endpoint,
        query_id=query.query_id,
        latency_ms=0,
        idempotency_key=sha256(f"{run_id}|{query.query_id}".encode()).hexdigest(),
        created_at=clock(),
    )


async def collect_evidence(
    *,
    run_id: str,
    as_of: datetime,
    queries: list[Query],
    adapters: Mapping[str, ProviderAdapter],
    evidence_store: EvidenceStore,
    provider_admission: ProviderAdmissionController,
    clock: Callable[[], datetime],
    id_factory: Callable[[], str],
    current_external_calls: int,
    external_call_limit: int,
) -> GatewayResult:
    if current_external_calls < 0 or external_call_limit < 0:
        raise GatewayContractError("external-call budget values must be non-negative")
    if current_external_calls + len(queries) > external_call_limit:
        raise GatewayBudgetExceeded(ReasonCode.BUDGET_EXCEEDED.value)

    prepared = []
    for query in queries:
        adapter = adapters.get(query.provider)
        if adapter is None or adapter.name != query.provider:
            raise GatewayContractError("adapter ownership mismatch")
        try:
            request = adapter.build_request(query, as_of)
        except Exception as exc:
            raise GatewayContractError("adapter request contract failure") from exc
        if request.provider != query.provider:
            raise GatewayContractError("request provider lineage mismatch")
        prepared.append(_Prepared(query, adapter, request))

    fatal_stop = asyncio.Event()
    retry_budget = external_call_limit - current_external_calls - len(prepared)
    retry_budget_lock = asyncio.Lock()
    results: list[dict | None] = [None] * len(prepared)
    fatal_errors: dict[int, GatewayContractError] = {}

    async def claim_retry_budget() -> bool:
        nonlocal retry_budget
        async with retry_budget_lock:
            if fatal_stop.is_set() or retry_budget <= 0:
                return False
            retry_budget -= 1
            return True

    async def execute(item_index: int) -> None:
        item = prepared[item_index]
        provider = item.query.provider
        item_calls: list[ProviderCall] = []
        item_failure = None
        fetched = adopted = deduped_count = 0
        succeeded = False
        attempt = 1
        while True:
            if fatal_stop.is_set():
                break
            try:
                started = perf_counter()
                debug_log(
                    "provider", "START", provider=provider,
                    query_id=item.query.query_id, attempt=attempt,
                )
                async with provider_admission.acquire(provider):
                    if fatal_stop.is_set():
                        break
                    call = _provider_call(run_id, item.query, clock, id_factory)
                    raw = await item.adapter.acall(item.request)
            except ProviderExecutionError as exc:
                debug_log(
                    "provider", "FAIL", provider=provider, attempt=attempt,
                    error_code=exc.reason_code, http_status=exc.http_status,
                    elapsed_ms=round((perf_counter() - started) * 1000, 1),
                )
                failed_call = call.model_copy(
                    update={"reason_code": exc.reason_code, "http_status": exc.http_status}
                )
                await evidence_store.put_provider_calls(run_id, [failed_call])
                item_calls.append(failed_call)
                if should_retry(
                    exc.reason_code, retryable=exc.retryable, attempt=attempt
                ) and await claim_retry_budget():
                    attempt += 1
                    continue
                item_failure = ProviderFailure(
                    item.query.query_id, provider, exc.reason_code, exc.retryable
                )
                break
            except Exception as exc:
                debug_log(
                    "provider", "FAIL", provider=provider, attempt=attempt,
                    exception_type=type(exc).__name__, exception_message=str(exc),
                    elapsed_ms=round((perf_counter() - started) * 1000, 1),
                )
                failed_call = call.model_copy(
                    update={"reason_code": ReasonCode.CONTRACT_VIOLATION}
                )
                await evidence_store.put_provider_calls(run_id, [failed_call])
                item_calls.append(failed_call)
                error = GatewayContractError(
                    "unclassified provider execution failure",
                    provider_calls=tuple(item_calls),
                )
                error.__cause__ = exc
                fatal_errors[item_index] = error
                fatal_stop.set()
                break
            try:
                reason_code, retryable = item.adapter.classify_error(raw)
            except ValueError:
                reason_code = None
                retryable = False
            if reason_code is not None:
                debug_log(
                    "provider", "FAIL", provider=provider, attempt=attempt,
                    error_code=reason_code,
                    elapsed_ms=round((perf_counter() - started) * 1000, 1),
                )
                failed_call = call.model_copy(update={"reason_code": reason_code})
                await evidence_store.put_provider_calls(run_id, [failed_call])
                item_calls.append(failed_call)
                if should_retry(
                    reason_code, retryable=retryable, attempt=attempt
                ) and await claim_retry_budget():
                    attempt += 1
                    continue
                item_failure = ProviderFailure(
                    item.query.query_id, provider, reason_code, retryable
                )
                break
            await evidence_store.put_provider_calls(run_id, [call])
            item_calls.append(call)
            try:
                drafts = item.adapter.parse_response(raw, item.query)
                evidence, deduped = await assemble_evidence(
                    drafts, item.query, call, as_of, run_id, clock(), evidence_store
                )
            except (ContractViolation, ValueError, TypeError) as exc:
                error = GatewayContractError(
                    "provider output contract failure", provider_calls=tuple(item_calls)
                )
                error.__cause__ = exc
                fatal_errors[item_index] = error
                fatal_stop.set()
                break
            fetched, adopted, deduped_count = len(drafts), len(evidence), deduped
            debug_log(
                "provider", "END", provider=provider, status="ok", attempt=attempt,
                items_fetched=fetched, items_adopted=adopted,
                elapsed_ms=round((perf_counter() - started) * 1000, 1),
            )
            succeeded = True
            break
        results[item_index] = {
            "provider": provider,
            "calls": item_calls,
            "failure": item_failure,
            "fetched": fetched,
            "adopted": adopted,
            "deduped": deduped_count,
            "succeeded": succeeded,
        }

    async def run_provider(indices: list[int]) -> None:
        limit = prepared[indices[0]].adapter.max_concurrency
        for offset in range(0, len(indices), limit):
            if fatal_stop.is_set():
                return
            await asyncio.gather(*(execute(index) for index in indices[offset : offset + limit]))

    by_provider: dict[str, list[int]] = {}
    for index, item in enumerate(prepared):
        by_provider.setdefault(item.query.provider, []).append(index)
    await asyncio.gather(*(run_provider(indices) for indices in by_provider.values()))

    calls = [call for result in results if result for call in result["calls"]]
    if fatal_errors:
        error = fatal_errors[min(fatal_errors)]
        error.provider_calls = tuple(calls)
        error.provider_call = calls[-1] if calls else None
        raise error

    counts: dict[str, dict[str, int]] = {}
    successes: dict[str, int] = {}
    failures: list[ProviderFailure] = []
    for result in results:
        if result is None:
            continue
        provider = result["provider"]
        provider_counts = counts.setdefault(
            provider,
            {"items_fetched": 0, "items_adopted": 0, "items_deduped": 0, "queries_run": 0},
        )
        provider_counts["queries_run"] += 1
        provider_counts["items_fetched"] += result["fetched"]
        provider_counts["items_adopted"] += result["adopted"]
        provider_counts["items_deduped"] += result["deduped"]
        if result["succeeded"]:
            successes[provider] = successes.get(provider, 0) + 1
        if result["failure"] is not None:
            failures.append(result["failure"])

    collections = {}
    for provider, provider_counts in counts.items():
        provider_failures = [failure for failure in failures if failure.provider == provider]
        success_count = successes.get(provider, 0)
        status = (
            NodeStatus.OK
            if not provider_failures
            else NodeStatus.PARTIAL
            if success_count
            else NodeStatus.MISSING
        )
        reasons = {failure.reason_code for failure in provider_failures}
        reason = next(iter(reasons)) if len(reasons) == 1 else None
        collections[provider] = CollectionResult(
            source=PROVIDER_SOURCE_TYPE[provider],
            status=status,
            reason_code=reason,
            **provider_counts,
        ).model_dump(mode="json")
    return GatewayResult(collections, len(calls), tuple(calls), tuple(failures))
