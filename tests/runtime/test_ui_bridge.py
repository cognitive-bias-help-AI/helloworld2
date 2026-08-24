from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.orchestration.graph import build_graph
from app.orchestration.runtime import ReviewRequestContext
from app.ui_bridge import build_result_view, public_error, serve
from tests.s0.runtime_fixtures import complete_intake, deps, initial_state


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


def test_public_error_does_not_expose_internal_exception_or_secrets():
    error = public_error(RuntimeError("ANTHROPIC_API_KEY=top-secret"))

    assert error == {
        "code": "REVIEW_FAILED",
        "message": "검토를 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    }
    assert "top-secret" not in repr(error)


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
async def test_worker_protocol_failure_is_terminal_and_public(monkeypatch):
    async def read_message():
        return {"kind": "resume", "value": {}}

    emitted = []

    async def emit(message):
        emitted.append(message)

    await serve(read_message, emit, runtime=SimpleNamespace())

    assert emitted == [{"kind": "error", **public_error(RuntimeError())}]
