from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.orchestration.graph import build_graph
from app.orchestration.runtime import ReviewRequestContext
from app.schemas.frozen import (
    CitationRef,
    Claim,
    ClaimEvaluation,
    Evidence,
    Finding,
    NumericCheck,
    Query,
    ReasonCode,
    SourceTrace,
)
from app.ui_bridge import _start_context, build_result_view, public_error, serve
from tests.s0.runtime_fixtures import complete_intake, deps, initial_state


def _structured_start(*, intake_overrides=None, message_overrides=None):
    intake = {
        "mode": "SURVEY_FIRST",
        "target": {"name": "삼성전자"},
        "structured": [
            {"slotId": 1, "responseState": "answered", "value": "CONSIDER_ENTRY"},
            {"slotId": 2, "responseState": "answered", "value": "NOT_HOLDING"},
            {"slotId": 3, "responseState": "answered", "value": "LONG"},
            {"slotId": 4, "responseState": "answered", "value": "AI 수요와 실적 개선을 기대합니다."},
            {"slotId": 5, "responseState": "answered", "value": "기업가치 개선을 기대합니다."},
            {"slotId": 6, "responseState": "answered", "value": ["FINANCIALS", "DISCLOSURE"]},
            {"slotId": 7, "responseState": "answered", "value": "HBM 경쟁력 회복이 늦을 수 있습니다."},
            {"slotId": 8, "responseState": "answered", "value": "영업이익 증가세가 꺾이면 재검토합니다."},
        ],
    }
    intake.update(intake_overrides or {})
    message = {"kind": "start", "intake": intake}
    message.update(message_overrides or {})
    return message


def _structured_start_without(*slot_ids):
    message = _structured_start()
    message["intake"]["structured"] = [
        item for item in message["intake"]["structured"]
        if item["slotId"] not in slot_ids
    ]
    return message


@pytest.mark.parametrize("mode", ["SURVEY_FIRST", "CHAT_FIRST", "HYBRID"])
def test_canonical_start_preserves_selected_intake_mode(mode):
    payload = {"mode": mode}
    if mode == "CHAT_FIRST":
        payload["freeText"] = ["삼성전자 살까 고민 중입니다."]
    else:
        payload["structured"] = [
            {"slotId": 1, "responseState": "answered", "value": "CONSIDER_ENTRY"}
        ]

    context = _start_context({"kind": "start", "intake": payload})

    assert context.intake.mode.value == mode


def test_chat_start_preserves_free_text_without_synthetic_survey_answers():
    context = _start_context({
        "kind": "start",
        "intake": {
            "mode": "CHAT_FIRST",
            "target": {"name": "삼성전자"},
            "freeText": ["삼성전자 살까 고민 중인데 AI 수요 때문에 실적이 좋아질 것 같아."],
        },
    })

    assert context.intake.structured == ()
    assert [(item.text, item.source.value) for item in context.intake.free_text] == [
        ("삼성전자 살까 고민 중인데 AI 수요 때문에 실적이 좋아질 것 같아.", "chat_explicit")
    ]
    assert context.intake.target.name == "삼성전자"
    assert context.intake.target.selected_code is None


def test_canonical_start_projects_all_survey_response_states_without_values():
    context = _start_context({
        "kind": "start",
        "intake": {
            "mode": "SURVEY_FIRST",
            "target": {"selectedCode": "0126Z0"},
            "structured": [
                {"slotId": 1, "responseState": "answered", "value": "CONSIDER_ENTRY"},
                {"slotId": 5, "responseState": "unknown"},
                {"slotId": 7, "responseState": "undecided"},
                {"slotId": 8, "responseState": "user_declined"},
            ],
        },
    })

    assert context.intake.target.selected_code == "0126Z0"
    assert [(item.slot_id, item.response_state.value, item.value) for item in context.intake.structured] == [
        (1, "answered", "CONSIDER_ENTRY"),
        (5, "unknown", None),
        (7, "undecided", None),
        (8, "user_declined", None),
    ]


def test_hybrid_start_keeps_survey_and_chat_provenance_distinct():
    context = _start_context({
        "kind": "start",
        "intake": {
            "mode": "HYBRID",
            "structured": [{"slotId": 4, "responseState": "answered", "value": "AI 수요"}],
            "freeText": ["추가로 공급 부족도 우려합니다."],
        },
    })

    assert context.intake.structured[0].source.value == "survey"
    assert context.intake.free_text[0].source.value == "chat_explicit"


@pytest.mark.asyncio
async def test_result_view_projects_only_canonical_runtime_artifacts():
    runtime_deps = deps()
    state = await build_graph(runtime_deps).ainvoke(
        initial_state(), context=ReviewRequestContext(intake=complete_intake())
    )

    result = await build_result_view(SimpleNamespace(deps=runtime_deps), state)

    assert result["stock"] == {"code": "005930", "name": "삼성전자", "market": "KOSPI"}
    assert result["claims"][0]["proposition"] == "2025 사업보고서 연결 영업이익이 증가했다"
    assert result["claims"][0]["evaluation"]["verdict"] == "support"
    dart_evidence = next(item for item in result["evidence"] if item["sourceType"] == "dart")
    assert dart_evidence["url"] == (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250814000123"
    )
    assert dart_evidence["publishedAt"] is not None
    assert result["report"]["renderedSlots"] == [
        {
            "slotNo": 1,
            "text": "검증된 결과",
            "citations": result["report"]["renderedSlots"][0]["citations"],
        }
    ]
    assert result["finalSummary"] == "검증된 결과"
    assert result["degraded"] is False
    assert [item["slotId"] for item in result["judgmentSlots"]] == list(range(1, 9))
    assert result["judgmentSlots"][5]["values"] == []
    assert result["judgmentSlots"][6]["values"] == []
    assert result["judgmentSlots"][7]["values"] == ["전제가 바뀌면 재검토"]
    assert "free_text" not in repr(result["judgmentContext"])
    assert "전제가 바뀌면 재검토" not in repr(result["judgmentContext"])


def test_public_error_does_not_expose_internal_exception_or_secrets():
    error = public_error(RuntimeError("ANTHROPIC_API_KEY=top-secret"))

    assert error == {
        "code": "REVIEW_FAILED",
        "message": "검토를 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    }
    assert "top-secret" not in repr(error)


@pytest.mark.asyncio
async def test_result_view_treats_blocked_n12_as_reportless_terminal_without_report_lookup():
    class ReviewStore:
        async def get_report(self, _report_id):
            raise AssertionError("reportless terminal must not look up a report")

    runtime = SimpleNamespace(
        deps=SimpleNamespace(
            review_store=ReviewStore(),
            evidence_store=SimpleNamespace(),
        )
    )
    state = {
        "report_id": None,
        "node_results": ["n0:ok", "n1:block:prompt_injection", "n12:end"],
    }

    with pytest.raises(RuntimeError, match="published report is missing"):
        await build_result_view(runtime, state)


@pytest.mark.asyncio
@pytest.mark.parametrize("report_id", [None, "missing-report"])
async def test_result_view_fails_closed_when_published_report_is_missing(report_id):
    class ReviewStore:
        async def get_claims(self, _ids):
            return []

        async def get_claim_evaluations(self, _ids):
            return []

        async def get_report(self, _report_id):
            return None

    class EvidenceStore:
        async def get_many(self, _ids):
            return []

    runtime = SimpleNamespace(
        deps=SimpleNamespace(
            review_store=ReviewStore(),
            evidence_store=EvidenceStore(),
        )
    )
    state = {
        "report_id": report_id,
        "node_results": ["n11:publish", "n12:end"],
    }

    with pytest.raises(RuntimeError, match="published report is missing"):
        await build_result_view(runtime, state)


@pytest.mark.asyncio
async def test_worker_keeps_reportless_terminal_details_out_of_public_error():
    emitted = []

    class Graph:
        async def ainvoke(self, _payload, _config, context=None):
            return {
                "report_id": None,
                "node_results": ["n0:ok", "n1:block:prompt_injection", "n12:end"],
            }

    async def read_message():
        return _structured_start()

    async def emit(message):
        emitted.append(message)

    await serve(read_message, emit, runtime=SimpleNamespace(graph=Graph(), deps=SimpleNamespace()))

    assert emitted == [{
        "kind": "terminal",
        "reasonCode": "prompt_injection",
        "message": "입력 내용을 안전하게 처리할 수 없어 검토를 종료했습니다.",
    }]


@pytest.mark.asyncio
async def test_result_view_does_not_invent_a_verdict_without_canonical_evaluation():
    runtime_deps = deps()
    state = await build_graph(runtime_deps).ainvoke(
        initial_state(), context=ReviewRequestContext(intake=complete_intake())
    )
    state["claim_evaluation_ids"] = []

    result = await build_result_view(SimpleNamespace(deps=runtime_deps), state)

    assert len(result["claims"]) == 1
    assert result["claims"][0]["evaluation"] is None
    assert result["evidence"]


NOW = datetime(2026, 8, 24, tzinfo=UTC)


def _ulid(index: int) -> str:
    return f"01ARZ3NDEKTSV4RRFFQ69G{index:04d}"


def _canonical_projection_runtime(verdict: str, *, numeric_checks=None, verifiable=True):
    claim_id = _ulid(1)
    evaluation_id = _ulid(2)
    evidence_id = _ulid(3)
    query_id = _ulid(4)
    report_id = _ulid(5)
    finding_id = _ulid(6)
    numeric_checks = list(numeric_checks or [])
    support = [evidence_id] if verdict in {"support", "partial_support"} else []
    oppose = [evidence_id] if verdict == "contradicted" else []
    unknown = [evidence_id] if not support and not oppose else []
    citation = CitationRef(evidence_id=evidence_id, span="공식 근거")
    claim = Claim(
        claim_id=claim_id,
        slot_id=4,
        user_text_span="영업이익이 증가했다",
        span_offset=(0, 11),
        normalized_proposition="2025년 영업이익이 증가했다",
        verifiable=verifiable,
        origin=SourceTrace.SURVEY,
        created_at=NOW,
    )
    evaluation = ClaimEvaluation(
        claim_evaluation_id=evaluation_id,
        claim_id=claim_id,
        citations=[citation],
        support_evidence_ids=support,
        oppose_evidence_ids=oppose,
        neutral_evidence_ids=[],
        unknown_evidence_ids=unknown,
        numeric_checks=numeric_checks,
        verdict=verdict,
        missing_dimensions=[5],
        uncertainty_codes=[ReasonCode.COVERAGE_TRUNCATED],
        created_at=NOW,
    )
    finding = Finding(
        finding_id=finding_id,
        slot_id=4,
        kind="unverified",
        citations=[citation],
        claim_evaluation_id=evaluation_id,
        created_at=NOW,
    )
    query = Query(
        query_id=query_id,
        scope="claim",
        claim_id=claim_id,
        intent="verify",
        provider="dart",
        endpoint="financial_statement",
        params={"stock_code": "005930"},
        created_at=NOW,
    )
    evidence = Evidence(
        evidence_id=evidence_id,
        source_type="dart",
        source_ref="20250814000123",
        source_url="https://dart.fss.or.kr/example",
        publisher="금융감독원",
        published_at=NOW,
        fetched_at=NOW,
        raw_span="공식 근거",
        span_scope="structured_field",
        content_sha256="a" * 64,
        provider_request_id=_ulid(7),
        as_of=NOW,
    )
    report = {
        "schema_version": "s0.v1",
        "rendered_slots": [{"slot_no": 4, "text": "검토 결과", "citations": [citation.model_dump(mode="json")]}],
        "banners": ["COVERAGE_TRUNCATED"],
        "theory_notes": [],
        "citations": [{
            "evidence_id": evidence_id,
            "span": "공식 근거",
            "source_url": "https://dart.fss.or.kr/example",
            "publisher": "금융감독원",
        }],
        "created_at": NOW.isoformat(),
    }

    class ReviewStore:
        async def get_report(self, value):
            return report if value == report_id else None

        async def get_claims(self, ids):
            return [claim] if claim_id in ids else []

        async def get_claim_evaluations(self, ids):
            return [evaluation] if evaluation_id in ids else []

        async def get_findings(self, ids):
            return [finding] if finding_id in ids else []

        async def get_claim_evidence(self, _run_id, requested_claim_id):
            from app.schemas.frozen import ClaimEvidence

            return [ClaimEvidence(
                claim_id=claim_id,
                evidence_id=evidence_id,
                stance="support" if support else "oppose" if oppose else "unknown",
                stance_source="llm",
                query_id=query_id,
            )] if requested_claim_id == claim_id else []

        async def get_input(self, _input_id):
            return {"masked_intake": {"structured": []}}

        async def get_slot_observations(self, _run_id):
            return []

        async def get_ask_records(self, _run_id):
            return []

        async def get_resume_sources(self, _run_id):
            return []

    class EvidenceStore:
        async def get_many(self, ids):
            return [evidence] if evidence_id in ids else []

        async def get_queries(self, ids):
            return [query] if query_id in ids else []

        async def evidence_ids_for_queries(self, ids):
            return [evidence_id] if query_id in ids else []

    runtime = SimpleNamespace(deps=SimpleNamespace(
        review_store=ReviewStore(), evidence_store=EvidenceStore()
    ))
    state = {
        "run_id": "run-1",
        "input_id": None,
        "stock": {"code": "005930", "name": "삼성전자", "market": "KOSPI"},
        "claim_ids": [claim_id],
        "claim_evaluation_ids": [evaluation_id],
        "finding_ids": [finding_id],
        "query_ids": [query_id],
        "collections": {"dart": {
            "source": "dart", "status": "PARTIAL", "reason_code": "coverage_truncated",
            "items_fetched": 1, "items_adopted": 1, "items_deduped": 0, "queries_run": 1,
        }},
        "oppose": {"status": "unverified", "count": None, "queries": ["반대 검색"], "reason": "rate_limit"},
        "report_id": report_id,
        "node_results": ["n11:publish", "n12:end"],
    }
    return runtime, state, evidence_id, finding_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdict",
    ["support", "partial_support", "unsupported", "contradicted", "unverifiable"],
)
async def test_result_view_preserves_each_canonical_verdict(verdict):
    runtime, state, _, _ = _canonical_projection_runtime(verdict)

    result = await build_result_view(runtime, state)

    assert result["claims"][0]["evaluation"]["verdict"] == verdict
    assert result["claims"][0]["evaluation"]["verdict"] not in {
        "verified", "partial", "unverified"
    }
    assert result["claims"][0]["evaluation"]["numericChecks"] == []


@pytest.mark.asyncio
async def test_result_view_keeps_non_verifiable_claim_without_evaluation():
    runtime, state, _, _ = _canonical_projection_runtime("unverifiable", verifiable=False)
    state["claim_evaluation_ids"] = []

    result = await build_result_view(runtime, state)

    assert result["claims"][0]["verifiable"] is False
    assert result["claims"][0]["evaluation"] is None


@pytest.mark.asyncio
async def test_result_view_preserves_evaluation_finding_oppose_report_and_lineage():
    check = NumericCheck(
        metric="영업이익",
        claimed="100",
        observed=100.0,
        unit="억원",
        period="2025",
        result="consistent",
        evidence_id=_ulid(3),
    )
    runtime, state, evidence_id, finding_id = _canonical_projection_runtime(
        "support", numeric_checks=[check]
    )

    result = await build_result_view(runtime, state)

    evaluation = result["claims"][0]["evaluation"]
    assert evaluation["supportEvidenceIds"] == [evidence_id]
    assert evaluation["opposeEvidenceIds"] == []
    assert evaluation["neutralEvidenceIds"] == []
    assert evaluation["unknownEvidenceIds"] == []
    assert evaluation["missingDimensions"] == [5]
    assert evaluation["uncertaintyCodes"] == ["coverage_truncated"]
    assert evaluation["numericChecks"][0]["computedBy"] == "rule"
    assert result["findings"][0]["findingId"] == finding_id
    assert result["opposingSearch"] == {
        "status": "unverified", "count": None, "queries": ["반대 검색"], "reason": "rate_limit"
    }
    assert result["evidence"][0]["evidenceId"] == evidence_id
    assert result["evidence"][0]["relatedQueryIds"] == [_ulid(4)]
    assert result["evidence"][0]["relatedClaimIds"] == [_ulid(1)]
    assert result["evidence"][0]["roles"] == ["PRIMARY"]
    assert result["evidence"][0]["stances"][0]["stance"] == "support"
    assert result["providerCollections"]["dart"]["status"] == "PARTIAL"
    assert result["report"]["renderedSlots"][0]["slotNo"] == 4
    assert result["report"]["renderedSlots"][0]["citations"][0]["evidenceId"] == evidence_id
    assert result["report"]["citations"][0]["evidenceId"] == evidence_id
    assert result["report"]["banners"] == ["COVERAGE_TRUNCATED"]
    assert result["report"]["theoryNotes"] == []
    assert result["report"]["createdAt"] == NOW.isoformat()


@pytest.mark.asyncio
async def test_result_view_rejects_malformed_persisted_collection():
    runtime, state, _, _ = _canonical_projection_runtime("support")
    state["collections"]["dart"]["items_adopted"] = 2

    with pytest.raises(ValueError):
        await build_result_view(runtime, state)


@pytest.mark.asyncio
async def test_worker_emits_safe_terminal_reason_without_internal_details():
    emitted = []

    class Graph:
        async def ainvoke(self, _payload, _config, context=None):
            return {
                "report_id": None,
                "node_results": ["n0:ok", "n1:block:prompt_injection", "n12:end"],
            }

    async def read_message():
        return _structured_start()

    async def emit(message):
        emitted.append(message)

    await serve(read_message, emit, runtime=SimpleNamespace(graph=Graph(), deps=SimpleNamespace()))

    assert emitted == [{
        "kind": "terminal",
        "reasonCode": "prompt_injection",
        "message": "입력 내용을 안전하게 처리할 수 없어 검토를 종료했습니다.",
    }]
    assert "Traceback" not in repr(emitted)


@pytest.mark.asyncio
async def test_debug_diagnostics_are_safe_while_public_error_stays_sanitized(monkeypatch, capsys):
    monkeypatch.setenv("REVIEW_DEBUG_LOGS", "1")

    class Graph:
        async def ainvoke(self, _payload, _config, context=None):
            raise RuntimeError("MLAPI_API_KEY=super-secret-test-value")

    async def read_message():
        return _structured_start()

    emitted = []

    async def emit(message):
        emitted.append(message)

    await serve(read_message, emit, runtime=SimpleNamespace(graph=Graph()))

    assert emitted == [{
        "kind": "error",
        "code": "REVIEW_FAILED",
        "message": "검토를 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    }]
    assert "super-secret-test-value" not in capsys.readouterr().err


@pytest.mark.asyncio
async def test_worker_emits_existing_hitl_payload_then_terminal_result(monkeypatch):
    messages = iter(
        [
            {"kind": "start", "text": "삼성전자 살까?"},
            {"kind": "resume", "value": {"selected_code": "005930"}},
        ]
    )
    emitted = []
    hitl = {"candidates": [{"selected_code": "005930", "display_name": "삼성전자"}]}

    class Graph:
        calls = 0

        async def ainvoke(self, payload, config, context=None):
            self.calls += 1
            if self.calls == 1:
                return {"__interrupt__": [SimpleNamespace(value=hitl)]}
            return {"report_id": "report-1"}

    runtime = SimpleNamespace(graph=Graph())

    async def read_message():
        return next(messages)

    async def emit(message):
        emitted.append(message)

    async def fake_result(_runtime, _state):
        return {"finalSummary": "canonical report"}

    monkeypatch.setattr("app.ui_bridge.build_result_view", fake_result)

    await serve(read_message, emit, runtime=runtime)

    assert emitted == [
        {"kind": "hitl", "payload": hitl},
        {"kind": "result", "result": {"finalSummary": "canonical report"}},
    ]


@pytest.mark.asyncio
async def test_worker_projects_structured_start_to_survey_slots_and_explicit_target(
    monkeypatch,
):
    messages = iter([_structured_start()])
    emitted = []

    class Graph:
        context = None

        async def ainvoke(self, _payload, _config, context=None):
            self.context = context
            return {"report_id": "report-1"}

    graph = Graph()

    async def read_message():
        return next(messages)

    async def emit(message):
        emitted.append(message)

    async def fake_result(_runtime, _state):
        return {"finalSummary": "canonical report"}

    monkeypatch.setattr("app.ui_bridge.build_result_view", fake_result)

    await serve(read_message, emit, runtime=SimpleNamespace(graph=graph))

    intake = graph.context.intake
    assert graph.context.raw_text is None
    assert intake.mode.value == "SURVEY_FIRST"
    assert intake.target.name == "삼성전자"
    assert [(item.slot_id, item.value) for item in intake.structured] == [
        (1, "CONSIDER_ENTRY"),
        (2, "NOT_HOLDING"),
        (3, "LONG"),
        (4, "AI 수요와 실적 개선을 기대합니다."),
        (5, "기업가치 개선을 기대합니다."),
        (6, ("FINANCIALS", "DISCLOSURE")),
        (7, "HBM 경쟁력 회복이 늦을 수 있습니다."),
        (8, "영업이익 증가세가 꺾이면 재검토합니다."),
    ]
    assert all(item.source.value == "survey" for item in intake.structured)
    assert all(item.response_state.value == "answered" for item in intake.structured)
    assert intake.free_text == ()
    assert emitted == [{"kind": "result", "result": {"finalSummary": "canonical report"}}]


@pytest.mark.asyncio
async def test_worker_omits_absent_optional_structured_slots(monkeypatch):
    messages = iter(
        [
            _structured_start_without(
                5, 6, 7, 8,
            )
        ]
    )
    emitted = []

    class Graph:
        context = None

        async def ainvoke(self, _payload, _config, context=None):
            self.context = context
            return {"report_id": "report-1"}

    graph = Graph()

    async def read_message():
        return next(messages)

    async def emit(message):
        emitted.append(message)

    async def fake_result(_runtime, _state):
        return {"finalSummary": "canonical report"}

    monkeypatch.setattr("app.ui_bridge.build_result_view", fake_result)

    await serve(read_message, emit, runtime=SimpleNamespace(graph=graph))

    assert [item.slot_id for item in graph.context.intake.structured] == [1, 2, 3, 4]
    assert emitted[0]["kind"] == "result"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        _structured_start(intake_overrides={"target": {"name": "   "}}),
        _structured_start(intake_overrides={"mode": "INVALID"}),
        _structured_start(intake_overrides={"structured": [
            {"slotId": 1, "responseState": "answered", "value": "BUY_NOW"}
        ]}),
        _structured_start(intake_overrides={"structured": [
            {"slotId": 6, "responseState": "answered", "value": ["NEWS", "NEWS"]}
        ]}),
        _structured_start(intake_overrides={"structured": [
            {"slotId": 5, "responseState": "unknown", "value": "fake"}
        ]}),
        _structured_start(intake_overrides={"unexpected": "value"}),
        _structured_start(message_overrides={"unexpected": "value"}),
    ],
)
async def test_worker_rejects_malformed_structured_start_with_a_public_error(message):
    emitted = []

    async def read_message():
        return message

    async def emit(value):
        emitted.append(value)

    await serve(read_message, emit, runtime=SimpleNamespace())

    assert emitted == [{"kind": "error", **public_error(RuntimeError())}]


@pytest.mark.asyncio
async def test_worker_protocol_failure_is_terminal_and_public(monkeypatch):
    async def read_message():
        return {"kind": "resume", "value": {}}

    emitted = []

    async def emit(message):
        emitted.append(message)

    await serve(read_message, emit, runtime=SimpleNamespace())

    assert emitted == [{"kind": "error", **public_error(RuntimeError())}]
