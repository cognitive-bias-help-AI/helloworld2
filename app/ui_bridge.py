"""Thin, read-only projection from canonical review artifacts to the local UI."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.cli import initial_state
from app.diagnostics import debug_enabled, debug_log
from app.domain.intake import (
    FreeTextInput,
    HybridIntake,
    IntakeMode,
    ResponseState,
    StructuredAnswer,
    TargetSecurityInput,
)
from app.orchestration.runtime import ReviewRequestContext
from app.runtime.local import compose_local_runtime, load_dotenv
from app.schemas.frozen import NonBlankStr, SourceTrace
from app.ui_projection import build_ui_result, safe_terminal_view


class TargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selected_code: str | None = Field(default=None, validation_alias="selectedCode")
    name: NonBlankStr | None = None
    market: Literal["KOSPI", "KOSDAQ"] | None = None

    @model_validator(mode="after")
    def exactly_one_identity(self):
        if (self.selected_code is None) == (self.name is None):
            raise ValueError("target requires exactly one identity")
        if self.market is not None and self.selected_code is None:
            raise ValueError("market requires selected_code")
        return self


class StructuredResponseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slot_id: int = Field(validation_alias="slotId", ge=1, le=8)
    response_state: ResponseState = Field(validation_alias="responseState")
    value: str | tuple[str, ...] | None = None

    @model_validator(mode="after")
    def response_state_matches_value(self):
        if self.response_state is ResponseState.ANSWERED and self.value is None:
            raise ValueError("ANSWERED requires value")
        if self.response_state is not ResponseState.ANSWERED and self.value is not None:
            raise ValueError(f"{self.response_state.value} must not carry value")
        return self


class CanonicalIntakeRequest(BaseModel):
    """Closed UI transport mirroring HybridIntake without client-owned provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: IntakeMode
    target: TargetRequest | None = None
    structured: tuple[StructuredResponseRequest, ...] = ()
    free_text: tuple[NonBlankStr, ...] = Field(default=(), validation_alias="freeText")

    @model_validator(mode="after")
    def enforce_mode_shape(self):
        if "target" in self.model_fields_set and self.target is None:
            raise ValueError("target must be omitted instead of null")
        slot_ids = [item.slot_id for item in self.structured]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("duplicate structured slot")
        if self.mode is IntakeMode.SURVEY_FIRST and self.free_text:
            raise ValueError("SURVEY_FIRST must not carry free text")
        if self.mode is IntakeMode.CHAT_FIRST and (self.structured or not self.free_text):
            raise ValueError("CHAT_FIRST requires free text without structured answers")
        return self


def _project_structured_intake(payload: object) -> HybridIntake:
    if not isinstance(payload, dict):
        raise ValueError("structured intake must be an object")
    request = CanonicalIntakeRequest.model_validate(payload)
    target = None
    if request.target is not None:
        target = TargetSecurityInput(
            selected_code=request.target.selected_code,
            name=request.target.name,
            market=request.target.market,
            source=SourceTrace.SURVEY,
        )
    structured = tuple(
        StructuredAnswer(
            slot_id=item.slot_id,
            value=item.value,
            source=SourceTrace.SURVEY,
            response_state=item.response_state,
        )
        for item in request.structured
    )
    return HybridIntake(
        schema_version="hybrid_intake/v1",
        mode=request.mode,
        target=target,
        structured=structured,
        free_text=tuple(
            FreeTextInput(text=text, source=SourceTrace.CHAT_EXPLICIT)
            for text in request.free_text
        ),
    )


def _start_context(message: object) -> ReviewRequestContext:
    if not isinstance(message, dict) or message.get("kind") != "start":
        raise ValueError("first message must be start")
    if "intake" in message:
        if set(message) != {"kind", "intake"}:
            raise ValueError("structured start contains undeclared fields")
        return ReviewRequestContext(intake=_project_structured_intake(message["intake"]))
    if set(message) != {"kind", "text"} or not isinstance(message.get("text"), str):
        raise ValueError("legacy start must carry text only")
    return ReviewRequestContext(raw_text=message["text"])


def public_error(_error: BaseException) -> dict[str, str]:
    """Return a fixed public error without reflecting internal exception data."""

    return {
        "code": "REVIEW_FAILED",
        "message": "검토를 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    }


_JUDGMENT_CONTEXT_SLOTS = {
    1: "decisionAction",
    2: "holdingState",
    3: "timeHorizon",
    4: "primaryReasons",
    5: "expectedOutcome",
}


def _project_judgment_context(input_body: object) -> dict[str, str]:
    """Expose only the approved survey slots from persisted masked intake."""

    if not isinstance(input_body, dict):
        return {}
    masked_intake = input_body.get("masked_intake")
    if not isinstance(masked_intake, dict):
        return {}
    structured = masked_intake.get("structured")
    if not isinstance(structured, list):
        return {}

    context: dict[str, str] = {}
    for answer in structured:
        if not isinstance(answer, dict):
            continue
        field = _JUDGMENT_CONTEXT_SLOTS.get(answer.get("slot_id"))
        value = answer.get("value")
        if field is not None and isinstance(value, str):
            context[field] = value
    return context


async def build_result_view(runtime: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Project persisted canonical artifacts without adding a new judgment."""
    result = await build_ui_result(runtime, state)
    input_id = state.get("input_id")
    judgment_context = {}
    if isinstance(input_id, str) and input_id:
        judgment_context = _project_judgment_context(
            await runtime.deps.review_store.get_input(input_id)
        )
    return {**result, "judgmentContext": judgment_context}


async def serve(read_message: Any, emit: Any, *, runtime: Any) -> None:
    """Serve exactly one review session over an injected JSON-message boundary."""

    run_id: str | None = None
    command = "waiting"
    phase = "receive"
    try:
        message = await read_message()
        command = str(message.get("kind")) if isinstance(message, dict) else "invalid"
        debug_log("review", "received command", command=command)
        context = _start_context(message)

        run_id = f"ui-{uuid4().hex}"
        config = {"configurable": {"thread_id": run_id}}
        phase = "graph"
        debug_log("review", "graph invocation started", run_id=run_id, thread_id=run_id)
        result = await runtime.graph.ainvoke(
            initial_state(run_id, run_id, now=datetime.now(UTC)),
            config,
            context=context,
        )
        debug_log("review", "graph invocation returned", run_id=run_id)

        for _ in range(10):
            interrupts = result.get("__interrupt__")
            if not interrupts:
                terminal = safe_terminal_view(result)
                if terminal is not None:
                    await emit(terminal)
                    return
                phase = "result_projection"
                debug_log("review", "result projection started", run_id=run_id)
                await emit({"kind": "result", "result": await build_result_view(runtime, result)})
                debug_log("review", "result projection completed", run_id=run_id)
                return
            payload = interrupts[0].value
            details: dict[str, Any] = {}
            if isinstance(payload, dict):
                details = {
                    "schema": payload.get("schema_version"),
                    "candidate_count": len(payload.get("candidates", ())),
                    "question_count": len(payload.get("questions", ())),
                    "ask_ids": [q.get("ask_id") for q in payload.get("questions", ()) if isinstance(q, dict)],
                    "slot_ids": [q.get("slot_id") for q in payload.get("questions", ()) if isinstance(q, dict)],
                }
            debug_log("hitl", "interrupt", run_id=run_id, **details)
            await emit({"kind": "hitl", "payload": payload})
            debug_log("review", "HITL emitted", run_id=run_id)
            message = await read_message()
            command = str(message.get("kind")) if isinstance(message, dict) else "invalid"
            debug_log("review", "received command", run_id=run_id, command=command)
            if message.get("kind") != "resume" or "value" not in message:
                raise ValueError("expected resume message")
            resume = message["value"]
            resume_fields = list(resume) if isinstance(resume, dict) else []
            debug_log(
                "hitl", "resume", run_id=run_id, fields=resume_fields,
                selected_code=resume.get("selected_code") if isinstance(resume, dict) else None,
                answer_count=len(resume.get("answers", ())) if isinstance(resume, dict) else None,
            )
            phase = "graph_resume"
            debug_log("review", "graph invocation started", run_id=run_id, command="resume")
            result = await runtime.graph.ainvoke(Command(resume=message["value"]), config)
            debug_log("review", "graph invocation returned", run_id=run_id, command="resume")
        raise RuntimeError("HITL turn limit exceeded")
    except BaseException as error:
        debug_log(
            "review", "failure", run_id=run_id, thread_id=run_id, phase=phase,
            command=command, exception_type=type(error).__name__,
        )
        await emit({"kind": "error", **public_error(error)})


async def _stdio_main() -> None:
    debug_log("review", "worker started")
    load_dotenv()
    debug_log("review", "environment loaded")
    if debug_enabled():
        names = (
            "MODEL_BACKEND", "ANTHROPIC_API_KEY", "LUNA_API_URL", "LUNA_API_KEY",
            "TERRA_API_URL", "TERRA_API_KEY", "SOL_API_URL", "SOL_API_KEY",
            "DART_API_KEY", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET",
            "KIWOOM_APP_KEY", "KIWOOM_APP_SECRET", "DATABASE_URL",
        )
        debug_log("config", "presence", **{name: "SET" if os.getenv(name) else "UNSET" for name in names})

    async def read_message() -> dict[str, Any]:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            raise EOFError("worker input closed")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("message must be an object")
        return value

    async def emit(message: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()

    try:
        debug_log("review", "runtime composition started")
        async with compose_local_runtime() as runtime:
            debug_log("review", "runtime composition completed")
            await serve(read_message, emit, runtime=runtime)
    except BaseException as error:
        debug_log("review", "composition failure", exception_type=type(error).__name__)
        await emit({"kind": "error", **public_error(error)})
    finally:
        debug_log("review", "worker terminating")


if __name__ == "__main__":
    asyncio.run(_stdio_main())
