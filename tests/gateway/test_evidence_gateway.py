import asyncio
from datetime import UTC, datetime

import pytest

from app.gateway.adapters.mock import MockAdapter
from app.gateway.admission import ProviderAdmissionController
from app.gateway.evidence_gateway import (
    MAX_ATTEMPTS,
    GatewayBudgetExceeded,
    GatewayContractError,
    collect_evidence,
    should_retry,
)
from app.gateway.execution import ProviderExecutionError
from app.schemas.frozen import Query, ReasonCode
from app.store.memory_evidence_store import MemoryEvidenceStore

NOW = datetime(2026, 8, 21, tzinfo=UTC)


def uid(n):
    return f"01ARZ3NDEKTSV4RRFFQ69G{n:04d}"


def query(n, provider="dart", endpoint=None):
    return Query(query_id=uid(8000 + n), scope="stock", intent="context",
                 provider=provider, endpoint=endpoint or f"ok-{n}", params={}, created_at=NOW)


class Ids:
    def __init__(self): self.n = 0
    def __call__(self):
        self.n += 1
        return uid(6000 + self.n)


class SelectiveAdapter(MockAdapter):
    def __init__(self, provider, failures=()):
        super().__init__(provider)
        self.failures = set(failures)
        self.calls = []
    async def acall(self, request):
        self.calls.append(request)
        return {"status": 429} if request.endpoint in self.failures else await super().acall(request)


async def run(queries, adapters, *, current=0, limit=25, admission=None):
    store = MemoryEvidenceStore()
    await store.put_queries("run-gw", queries)
    result = await collect_evidence(
        run_id="run-gw", as_of=NOW, queries=queries, adapters=adapters,
        evidence_store=store, clock=lambda: NOW, id_factory=Ids(),
        provider_admission=admission or ProviderAdmissionController(
            {provider: adapter.max_concurrency for provider, adapter in adapters.items()}
        ),
        current_external_calls=current, external_call_limit=limit,
    )
    return result, store


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["dart", "kiwoom"])
async def test_one_query_success_persists_evidence_and_call_lineage(provider):
    q = query(1, provider)
    result, store = await run([q], {provider: SelectiveAdapter(provider)})
    collection = result.collections[provider]
    assert collection["status"] == "OK"
    assert (collection["items_fetched"], collection["items_adopted"], collection["queries_run"]) == (1, 1, 1)
    call = result.provider_calls[0]
    assert (call.run_id, call.query_id, call.provider, call.endpoint, call.reason_code) == (
        "run-gw", q.query_id, provider, q.endpoint, None)
    assert call.idempotency_key == __import__("hashlib").sha256(f"run-gw|{q.query_id}".encode()).hexdigest()
    assert len(await store.evidence_ids_for_queries([q.query_id])) == 1
    assert await store.get_provider_calls([call.provider_request_id]) == [call]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("success_provider", "failure_provider"),
    [("dart", "kiwoom"), ("kiwoom", "dart")],
)
async def test_cross_provider_failure_isolated_and_success_preserved(
    success_provider, failure_provider
):
    success = query(1, success_provider)
    failure = query(2, failure_provider, "fail")
    result, store = await run(
        [success, failure],
        {
            success_provider: SelectiveAdapter(success_provider),
            failure_provider: SelectiveAdapter(failure_provider, {"fail"}),
        },
    )
    assert result.collections[success_provider]["status"] == "OK"
    assert result.collections[failure_provider]["status"] == "MISSING"
    assert result.collections[failure_provider]["reason_code"] == "rate_limit"
    assert result.external_calls == 2
    assert result.provider_calls[1].reason_code is ReasonCode.RATE_LIMIT
    assert len(await store.evidence_ids_for_queries([success.query_id])) == 1
    assert await store.evidence_ids_for_queries([failure.query_id]) == []
    assert await store.provider_calls_for_query(failure.query_id) == [result.provider_calls[1]]


@pytest.mark.asyncio
async def test_same_provider_partial_and_all_failure_status():
    ok, fail = query(1, endpoint="ok"), query(2, endpoint="fail")
    result, _ = await run([ok, fail], {"dart": SelectiveAdapter("dart", {"fail"})})
    assert result.collections["dart"]["status"] == "PARTIAL"
    assert result.collections["dart"]["queries_run"] == 2
    assert len(result.failures) == 1


@pytest.mark.asyncio
async def test_budget_preflight_exact_limit_and_over_limit_zero_calls():
    queries = [query(i) for i in range(1, 26)]
    adapter = SelectiveAdapter("dart")
    result, _ = await run(queries, {"dart": adapter})
    assert result.external_calls == len(adapter.calls) == 25
    blocked = SelectiveAdapter("dart")
    q = query(30)
    store = MemoryEvidenceStore()
    await store.put_queries("run-gw", [q])
    with pytest.raises(GatewayBudgetExceeded):
        await collect_evidence(
            run_id="run-gw", as_of=NOW, queries=[q], adapters={"dart": blocked},
            evidence_store=store, clock=lambda: NOW, id_factory=Ids(),
            provider_admission=ProviderAdmissionController({"dart": 3}),
            current_external_calls=25, external_call_limit=25,
        )
    assert blocked.calls == []
    assert await store.provider_calls_for_query(q.query_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "mismatch"])
async def test_adapter_preflight_contract_failure_has_zero_calls(case):
    q = query(1, "kiwoom")
    adapter = SelectiveAdapter("dart")
    adapters = {} if case == "missing" else {"kiwoom": adapter}
    with pytest.raises(GatewayContractError):
        await run([q], adapters)
    assert adapter.calls == []


class WrongSource(SelectiveAdapter):
    def parse_response(self, raw, q):
        return [super().parse_response(raw, q)[0].model_copy(update={"source_type": "news"})]


@pytest.mark.asyncio
async def test_source_mismatch_fails_closed_after_attempt_without_rollback_claim():
    adapter = WrongSource("dart")
    with pytest.raises(GatewayContractError) as caught:
        await run([query(1)], {"dart": adapter})
    assert len(adapter.calls) == 1
    assert caught.value.provider_call is not None


class TypedTimeoutAdapter(SelectiveAdapter):
    async def acall(self, request):
        self.calls.append(request)
        raise ProviderExecutionError(
            reason_code=ReasonCode.UPSTREAM_TIMEOUT,
            retryable=True,
            safe_detail="request timed out",
        )


@pytest.mark.parametrize(
    ("reason", "retryable", "attempt", "expected"),
    [
        (ReasonCode.UPSTREAM_TIMEOUT, True, 1, True),
        (ReasonCode.UPSTREAM_5XX, True, 1, True),
        (ReasonCode.AUTH_FAILED, True, 1, False),
        (ReasonCode.NO_RESULT, True, 1, False),
        (ReasonCode.RATE_LIMIT, True, 1, False),
        (ReasonCode.CONTRACT_VIOLATION, True, 1, False),
        (ReasonCode.UPSTREAM_TIMEOUT, False, 1, False),
        (ReasonCode.UPSTREAM_TIMEOUT, True, MAX_ATTEMPTS, False),
    ],
)
def test_central_retry_policy(reason, retryable, attempt, expected):
    assert should_retry(reason, retryable=retryable, attempt=attempt) is expected


class SequenceAdapter(SelectiveAdapter):
    def __init__(self, provider, outcomes):
        super().__init__(provider)
        self.outcomes = list(outcomes)

    async def acall(self, request):
        self.calls.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def timeout():
    return ProviderExecutionError(
        reason_code=ReasonCode.UPSTREAM_TIMEOUT,
        retryable=True,
        safe_detail="request timed out",
    )


@pytest.mark.asyncio
async def test_timeout_then_success_uses_two_physical_calls_and_final_success():
    q = query(1)
    adapter = SequenceAdapter("dart", [timeout(), {"items": [{"value": 1}]}])
    result, store = await run([q], {"dart": adapter})

    calls = await store.provider_calls_for_query(q.query_id)
    evidence_ids = await store.evidence_ids_for_queries([q.query_id])
    evidence = (await store.get_many(evidence_ids))[0]
    assert len(adapter.calls) == result.external_calls == len(calls) == 2
    assert calls[0].reason_code is ReasonCode.UPSTREAM_TIMEOUT
    assert calls[1].reason_code is None
    assert calls[0].idempotency_key == calls[1].idempotency_key
    assert calls[0].provider_request_id != calls[1].provider_request_id
    assert evidence.provider_request_id == calls[1].provider_request_id
    assert result.collections["dart"]["status"] == "OK"
    assert result.collections["dart"]["queries_run"] == 1


@pytest.mark.asyncio
async def test_timeout_twice_stops_at_max_attempts():
    q = query(1)
    adapter = SequenceAdapter("dart", [timeout(), timeout()])
    result, store = await run([q], {"dart": adapter})
    assert len(adapter.calls) == result.external_calls == 2
    assert len(await store.provider_calls_for_query(q.query_id)) == 2
    assert await store.evidence_ids_for_queries([q.query_id]) == []
    assert result.collections["dart"]["status"] == "MISSING"


@pytest.mark.asyncio
async def test_retry_budget_exhaustion_keeps_first_attempt_only():
    q = query(1)
    adapter = SequenceAdapter("dart", [timeout()])
    result, store = await run([q], {"dart": adapter}, current=24, limit=25)
    assert len(adapter.calls) == result.external_calls == 1
    assert len(await store.provider_calls_for_query(q.query_id)) == 1


@pytest.mark.asyncio
async def test_retry_success_and_exhaustion_yield_partial_collection():
    q1, q2 = query(1), query(2)
    adapter = SequenceAdapter(
        "dart", [timeout(), {"items": [{"value": 1}]}, timeout(), timeout()]
    )
    result, store = await run([q1, q2], {"dart": adapter})
    assert len(adapter.calls) == result.external_calls == 4
    assert result.collections["dart"]["status"] == "PARTIAL"
    assert result.collections["dart"]["queries_run"] == 2
    assert len(await store.evidence_ids_for_queries([q1.query_id])) == 1
    assert await store.evidence_ids_for_queries([q2.query_id]) == []


@pytest.mark.asyncio
async def test_typed_timeout_exhausts_retry_and_preserves_provider_isolation():
    timed_out = query(1, "dart", "timeout")
    successful = query(2, "kiwoom", "ok")
    timeout_adapter = TypedTimeoutAdapter("dart")
    result, store = await run(
        [timed_out, successful],
        {"dart": timeout_adapter, "kiwoom": SelectiveAdapter("kiwoom")},
    )

    calls = await store.provider_calls_for_query(timed_out.query_id)
    assert len(timeout_adapter.calls) == len(calls) == 2
    assert all(call.reason_code is ReasonCode.UPSTREAM_TIMEOUT for call in calls)
    assert await store.evidence_ids_for_queries([timed_out.query_id]) == []
    assert result.collections["dart"]["reason_code"] == "upstream_timeout"
    assert result.collections["kiwoom"]["status"] == "OK"


@pytest.mark.asyncio
async def test_raw_5xx_retries_once_but_auth_failure_does_not_retry():
    retried = query(1, endpoint="server-error")
    denied = query(2, endpoint="auth-error")
    adapter = SequenceAdapter(
        "dart",
        [{"status": 500}, {"items": [{"value": 1}]}, {"status": 401}],
    )
    result, store = await run([retried, denied], {"dart": adapter})

    assert len(adapter.calls) == result.external_calls == 3
    assert len(await store.provider_calls_for_query(retried.query_id)) == 2
    assert len(await store.provider_calls_for_query(denied.query_id)) == 1
    assert result.collections["dart"]["status"] == "PARTIAL"


class ConcurrencyProbeAdapter(SelectiveAdapter):
    def __init__(self, provider, *, limit, release):
        super().__init__(provider)
        self.max_concurrency = limit
        self.release = release
        self.active = 0
        self.max_active = 0
        self.started = asyncio.Event()

    async def acall(self, request):
        self.calls.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.max_active >= min(2, self.max_concurrency):
            self.started.set()
        await self.release.wait()
        self.active -= 1
        return {"items": [{"value": request.endpoint}]}


@pytest.mark.asyncio
async def test_provider_scoped_concurrency_overlaps_and_respects_bounds():
    release = asyncio.Event()
    dart = ConcurrencyProbeAdapter("dart", limit=3, release=release)
    task = asyncio.create_task(run([query(i) for i in range(1, 5)], {"dart": dart}))
    await asyncio.wait_for(dart.started.wait(), timeout=1)
    release.set()
    result, _ = await task
    assert 1 < dart.max_active <= 3
    assert result.external_calls == 4


@pytest.mark.asyncio
async def test_three_gateway_runs_share_process_admission_capacity():
    release = asyncio.Event()
    adapter = ConcurrencyProbeAdapter("dart", limit=3, release=release)
    admission = ProviderAdmissionController({"dart": 2})
    tasks = [
        asyncio.create_task(
            run([query(index)], {"dart": adapter}, admission=admission)
        )
        for index in range(1, 4)
    ]
    await asyncio.wait_for(adapter.started.wait(), timeout=1)
    assert adapter.max_active == 2
    release.set()
    await asyncio.gather(*tasks)


class CountingAdmission(ProviderAdmissionController):
    def __init__(self, capacities):
        super().__init__(capacities)
        self.acquisitions = 0

    def acquire(self, provider):
        self.acquisitions += 1
        return super().acquire(provider)


@pytest.mark.asyncio
async def test_retry_reenters_process_admission_for_each_physical_attempt():
    admission = CountingAdmission({"dart": 1})
    adapter = SequenceAdapter("dart", [timeout(), {"items": [{"value": 1}]}])
    result, _ = await run([query(1)], {"dart": adapter}, admission=admission)

    assert admission.acquisitions == 2
    assert result.external_calls == 2
    assert len(result.provider_calls) == 2
    assert result.provider_calls[0].idempotency_key == result.provider_calls[1].idempotency_key
    assert result.provider_calls[0].provider_request_id != result.provider_calls[1].provider_request_id


@pytest.mark.asyncio
async def test_saturated_dart_admission_does_not_block_kiwoom():
    controller = ProviderAdmissionController({"dart": 1, "kiwoom": 1})
    dart_entered = asyncio.Event()
    release_dart = asyncio.Event()
    kiwoom_entered = asyncio.Event()

    async def hold_dart():
        async with controller.acquire("dart"):
            dart_entered.set()
            await release_dart.wait()

    async def enter_kiwoom():
        await dart_entered.wait()
        async with controller.acquire("kiwoom"):
            kiwoom_entered.set()

    dart_task = asyncio.create_task(hold_dart())
    kiwoom_task = asyncio.create_task(enter_kiwoom())
    await asyncio.wait_for(kiwoom_entered.wait(), timeout=1)
    release_dart.set()
    await asyncio.gather(dart_task, kiwoom_task)


class FatalAdapter(SelectiveAdapter):
    def __init__(self, release):
        super().__init__("dart")
        self.max_concurrency = 2
        self.release = release
        self.both_started = asyncio.Event()

    async def acall(self, request):
        self.calls.append(request)
        if len(self.calls) == 2:
            self.both_started.set()
        await self.release[request.endpoint].wait()
        if request.endpoint in {"fatal-1", "fatal-3"}:
            raise OSError(request.endpoint)
        if request.endpoint == "timeout":
            raise timeout()
        return {"items": [{"value": request.endpoint}]}


class AdmissionWaitingAdapter(SelectiveAdapter):
    def __init__(self, release):
        super().__init__("dart")
        self.max_concurrency = 2
        self.release = release
        self.first_started = asyncio.Event()

    async def acall(self, request):
        self.calls.append(request)
        if request.endpoint == "held":
            self.first_started.set()
            await self.release.wait()
        return {"items": [{"value": request.endpoint}]}


class SiblingFatalAdapter(SelectiveAdapter):
    def __init__(self, first_started):
        super().__init__("kiwoom")
        self.first_started = first_started
        self.raised = asyncio.Event()

    async def acall(self, request):
        self.calls.append(request)
        await self.first_started.wait()
        self.raised.set()
        raise OSError("sibling fatal")


@pytest.mark.asyncio
async def test_waiter_rechecks_fatal_stop_after_process_admission():
    release = asyncio.Event()
    dart = AdmissionWaitingAdapter(release)
    kiwoom = SiblingFatalAdapter(dart.first_started)
    queries = [
        query(1, endpoint="held"),
        query(2, endpoint="waiting"),
        query(3, provider="kiwoom", endpoint="fatal"),
    ]
    store = MemoryEvidenceStore()
    await store.put_queries("run-gw", queries)
    task = asyncio.create_task(
        collect_evidence(
            run_id="run-gw",
            as_of=NOW,
            queries=queries,
            adapters={"dart": dart, "kiwoom": kiwoom},
            evidence_store=store,
            provider_admission=ProviderAdmissionController({"dart": 1, "kiwoom": 1}),
            clock=lambda: NOW,
            id_factory=Ids(),
            current_external_calls=0,
            external_call_limit=25,
        )
    )
    await kiwoom.raised.wait()
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(GatewayContractError):
        await task
    assert [request.endpoint for request in dart.calls] == ["held"]
    assert await store.provider_calls_for_query(queries[1].query_id) == []


@pytest.mark.asyncio
async def test_fatal_stop_drains_inflight_blocks_queued_and_disables_retry():
    releases = {name: asyncio.Event() for name in ("fatal-1", "timeout", "queued")}
    adapter = FatalAdapter(releases)
    qs = [query(1, endpoint="fatal-1"), query(2, endpoint="timeout"), query(3, endpoint="queued")]
    store = MemoryEvidenceStore()
    await store.put_queries("run-gw", qs)
    task = asyncio.create_task(collect_evidence(
        run_id="run-gw", as_of=NOW, queries=qs, adapters={"dart": adapter},
        evidence_store=store, clock=lambda: NOW, id_factory=Ids(),
        provider_admission=ProviderAdmissionController({"dart": 3}),
        current_external_calls=0, external_call_limit=25,
    ))
    await asyncio.wait_for(adapter.both_started.wait(), timeout=1)
    releases["fatal-1"].set()
    await asyncio.sleep(0)
    releases["timeout"].set()
    with pytest.raises(GatewayContractError):
        await task
    assert [request.endpoint for request in adapter.calls] == ["fatal-1", "timeout"]
    assert len(await store.provider_calls_for_query(qs[0].query_id)) == 1
    assert len(await store.provider_calls_for_query(qs[1].query_id)) == 1
    assert await store.provider_calls_for_query(qs[2].query_id) == []


@pytest.mark.asyncio
async def test_kiwoom_authority_keeps_execution_serial():
    release = asyncio.Event()
    kiwoom = ConcurrencyProbeAdapter("kiwoom", limit=1, release=release)
    task = asyncio.create_task(run([query(1, "kiwoom"), query(2, "kiwoom")], {"kiwoom": kiwoom}))
    await asyncio.wait_for(kiwoom.started.wait(), timeout=1)
    release.set()
    await task
    assert kiwoom.max_active == 1


@pytest.mark.asyncio
async def test_multiple_fatal_errors_select_original_query_order():
    releases = {name: asyncio.Event() for name in ("fatal-1", "ok", "fatal-3")}
    adapter = FatalAdapter(releases)
    adapter.max_concurrency = 3
    qs = [query(1, endpoint="fatal-1"), query(2, endpoint="ok"), query(3, endpoint="fatal-3")]
    store = MemoryEvidenceStore()
    await store.put_queries("run-gw", qs)
    task = asyncio.create_task(collect_evidence(
        run_id="run-gw", as_of=NOW, queries=qs, adapters={"dart": adapter},
        evidence_store=store, clock=lambda: NOW, id_factory=Ids(),
        provider_admission=ProviderAdmissionController({"dart": 3}),
        current_external_calls=0, external_call_limit=25,
    ))
    while len(adapter.calls) < 3:
        await asyncio.sleep(0)
    releases["fatal-3"].set()
    await asyncio.sleep(0)
    releases["ok"].set()
    releases["fatal-1"].set()
    with pytest.raises(GatewayContractError) as caught:
        await task
    assert isinstance(caught.value.__cause__, OSError)
    assert caught.value.__cause__.args == ("fatal-1",)


class ThrowingAdapter(SelectiveAdapter):
    async def acall(self, request):
        self.calls.append(request)
        raise OSError("transport failed")


@pytest.mark.asyncio
async def test_attempted_unclassified_failure_exposes_auditable_provider_call():
    adapter = ThrowingAdapter("dart")
    q = query(1)
    store = MemoryEvidenceStore()
    await store.put_queries("run-gw", [q])
    with pytest.raises(GatewayContractError) as caught:
        await collect_evidence(
            run_id="run-gw", as_of=NOW, queries=[q], adapters={"dart": adapter},
            evidence_store=store, clock=lambda: NOW, id_factory=Ids(),
            provider_admission=ProviderAdmissionController({"dart": 3}),
            current_external_calls=0, external_call_limit=25,
        )
    assert len(adapter.calls) == 1
    assert caught.value.provider_call is not None
    assert caught.value.provider_call.reason_code is ReasonCode.CONTRACT_VIOLATION
    assert await store.provider_calls_for_query(q.query_id) == [caught.value.provider_call]


class SecondQueryWrongSource(SelectiveAdapter):
    def parse_response(self, raw, q):
        drafts = super().parse_response(raw, q)
        if q.endpoint == "wrong":
            return [drafts[0].model_copy(update={"source_type": "news"})]
        return drafts


@pytest.mark.asyncio
async def test_runtime_contract_failure_keeps_prior_evidence_and_stops_later_queries():
    queries = [query(1, endpoint="ok"), query(2, endpoint="wrong"), query(3, endpoint="later")]
    adapter = SecondQueryWrongSource("dart")
    store = MemoryEvidenceStore()
    await store.put_queries("run-gw", queries)

    with pytest.raises(GatewayContractError) as caught:
        await collect_evidence(
            run_id="run-gw",
            as_of=NOW,
            queries=queries,
            adapters={"dart": adapter},
            evidence_store=store,
            provider_admission=ProviderAdmissionController({"dart": 3}),
            clock=lambda: NOW,
            id_factory=Ids(),
            current_external_calls=0,
            external_call_limit=25,
        )

    assert len(adapter.calls) == len(caught.value.provider_calls) == 2
    assert await store.provider_calls_for_query(queries[1].query_id) == [
        caught.value.provider_calls[1]
    ]
    assert len(await store.evidence_ids_for_queries([queries[0].query_id])) == 1
    assert await store.evidence_ids_for_queries([queries[1].query_id]) == []
    assert await store.evidence_ids_for_queries([queries[2].query_id]) == []


@pytest.mark.asyncio
async def test_dedup_counts_do_not_mix_providers():
    q1, q2, q3 = query(1), query(2), query(3, "kiwoom")
    result, _ = await run(
        [q1, q2, q3], {"dart": SelectiveAdapter("dart"), "kiwoom": SelectiveAdapter("kiwoom")}
    )
    assert result.collections["dart"]["items_deduped"] == 1
    assert result.collections["kiwoom"]["items_deduped"] == 0
