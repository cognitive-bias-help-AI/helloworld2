from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.orchestration.graph import build_graph
from app.orchestration.runtime import ReviewRequestContext
from app.ui_bridge import build_result_view, public_error, serve
from tests.s0.runtime_fixtures import complete_intake, deps, initial_state


def _structured_start(*, intake_overrides=None, message_overrides=None):
    intake = {
        "stockInput": "삼성전자",
        "decisionAction": "CONSIDER_ENTRY",
        "holdingState": "NOT_HOLDING",
        "timeHorizon": "LONG",
        "primaryReasons": "AI 수요와 실적 개선을 기대합니다.",
        "expectedOutcome": "기업가치 개선을 기대합니다.",
        "informationChecked": ["FINANCIALS", "DISCLOSURE"],
        "counterEvidenceConcerns": "HBM 경쟁력 회복이 늦을 수 있습니다.",
        "changeConditions": "영업이익 증가세가 꺾이면 재검토합니다.",
    }
    intake.update(intake_overrides or {})
    message = {"kind": "start", "intake": intake}
    message.update(message_overrides or {})
    return message


def _structured_start_without(*fields):
    message = _structured_start()
    for field in fields:
        del message["intake"][field]
    return message


@pytest.mark.asyncio
async def test_result_view_projects_only_canonical_runtime_artifacts():
    runtime_deps = deps()
    state = await build_graph(runtime_deps).ainvoke(
        initial_state(), context=ReviewRequestContext(intake=complete_intake())
    )

    result = await build_result_view(SimpleNamespace(deps=runtime_deps), state)

    assert result["stock"] == {"code": "005930", "name": "삼성전자"}
    assert result["claims"] == [
        {
            "text": "2025 사업보고서 연결 영업이익이 증가했다",
            "status": "verified",
            "summary": "근거가 주장을 뒷받침합니다.",
        }
    ]
    assert result["evidence"][0]["url"] == (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250814000123"
    )
    assert result["evidence"][0]["publishedAt"] is not None
    assert result["finalSummary"] == "검증된 결과"
    assert result["degraded"] is False
    assert result["judgmentContext"] == {
        "decisionAction": "CONSIDER_ENTRY",
        "holdingState": "NOT_HOLDING",
        "timeHorizon": "LONG",
        "expectedOutcome": "기업가치 상승",
    }
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

    with pytest.raises(RuntimeError, match="review terminated without report"):
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

    assert emitted == [{"kind": "error", **public_error(RuntimeError())}]
    assert "prompt_injection" not in repr(emitted)


@pytest.mark.asyncio
async def test_result_view_does_not_invent_a_verdict_without_canonical_evaluation():
    runtime_deps = deps()
    state = await build_graph(runtime_deps).ainvoke(
        initial_state(), context=ReviewRequestContext(intake=complete_intake())
    )
    state["claim_evaluation_ids"] = []

    result = await build_result_view(SimpleNamespace(deps=runtime_deps), state)

    assert result["claims"] == []
    assert result["evidence"] == []


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
async def test_worker_projects_structured_start_to_survey_slots_and_stock_free_text(
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
    assert intake.mode.value == "HYBRID"
    assert intake.target is None
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
    assert [(item.text, item.source.value) for item in intake.free_text] == [
        ("삼성전자", "chat_explicit")
    ]
    assert "삼성전자 신규 매수" not in "\n".join(
        item.text for item in intake.free_text
    )
    assert emitted == [{"kind": "result", "result": {"finalSummary": "canonical report"}}]


@pytest.mark.asyncio
async def test_worker_omits_absent_optional_structured_slots(monkeypatch):
    messages = iter(
        [
            _structured_start_without(
                "expectedOutcome",
                "informationChecked",
                "counterEvidenceConcerns",
                "changeConditions",
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
        _structured_start(intake_overrides={"stockInput": "   "}),
        _structured_start(intake_overrides={"decisionAction": "BUY_NOW"}),
        _structured_start(intake_overrides={"informationChecked": ["NEWS", "NEWS"]}),
        _structured_start(
            intake_overrides={"informationChecked": ["NEWS", "NONE_CHECKED"]}
        ),
        _structured_start_without("primaryReasons"),
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
