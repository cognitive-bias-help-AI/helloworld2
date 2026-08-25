"""Development-only 20-run N3 semantic assembly acceptance harness.

It prints aggregate diagnostics only; credentials and model drafts never leave
the configured gateway or appear in output.
"""

from __future__ import annotations

import asyncio
import collections
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.intake import HybridIntake, IntakeMode, ResponseState, StructuredAnswer
from app.domain.semantic_source import build_semantic_segments
from app.models.mlapi_gateway import MlApiGatewayError
from app.orchestration.intake_review_runtime import _invoke_and_assemble
from app.runtime.local import compose_local_model_runtime, load_dotenv
from app.schemas.frozen import SourceTrace


def fixture() -> tuple:
    structured = (
        StructuredAnswer(slot_id=1, value="CONSIDER_ENTRY", source=SourceTrace.SURVEY, response_state=ResponseState.ANSWERED),
        StructuredAnswer(slot_id=2, value="NOT_HOLDING", source=SourceTrace.SURVEY, response_state=ResponseState.ANSWERED),
        StructuredAnswer(slot_id=3, value="LONG", source=SourceTrace.SURVEY, response_state=ResponseState.ANSWERED),
        StructuredAnswer(slot_id=4, value="AI 수요와 실적 개선 때문에 신규 매수를 고려하고 있다.", source=SourceTrace.SURVEY, response_state=ResponseState.ANSWERED),
        StructuredAnswer(slot_id=5, value="실적 개선이 이어지며 기업가치가 높아질 것으로 기대한다.", source=SourceTrace.SURVEY, response_state=ResponseState.ANSWERED),
        StructuredAnswer(slot_id=6, value=("FINANCIALS", "DISCLOSURE", "NEWS", "PRICE_CHART"), source=SourceTrace.SURVEY, response_state=ResponseState.ANSWERED),
        StructuredAnswer(slot_id=7, value="HBM 경쟁력 회복이 늦을 수 있다고 우려한다.", source=SourceTrace.SURVEY, response_state=ResponseState.ANSWERED),
        StructuredAnswer(slot_id=8, value="영업이익 증가세가 꺾이면 다시 판단한다.", source=SourceTrace.SURVEY, response_state=ResponseState.ANSWERED),
    )
    intake = HybridIntake(
        schema_version="hybrid_intake/v1", mode=IntakeMode.SURVEY_FIRST,
        structured=structured, free_text=(),
    )
    return build_semantic_segments(
        intake.model_dump(mode="json", exclude={"schema_version"}),
        "semantic_projection/v1",
    )


class CountingGateway:
    def __init__(self, gateway):
        self.gateway = gateway
        self.calls = 0
        self.gateway_failures: list[str] = []

    async def invoke(self, *args, **kwargs):
        self.calls += 1
        try:
            return await self.gateway.invoke(*args, **kwargs)
        except MlApiGatewayError as error:
            self.gateway_failures.append(error.code)
            raise


async def main() -> None:
    load_dotenv()
    if os.getenv("MODEL_BACKEND", "").strip().lower() != "mlapi":
        print("total=0 success=0 failure=1 reason=MODEL_BACKEND_NOT_MLAPI")
        return
    successes = 0
    failures: collections.Counter[str] = collections.Counter()
    first_attempt_gateway_failures: collections.Counter[str] = collections.Counter()
    retry_gateway_failures: collections.Counter[str] = collections.Counter()
    claims: collections.Counter[int] = collections.Counter()
    calls = 0
    segments = fixture()
    async with compose_local_model_runtime() as model_runtime:
        for index in range(20):
            gateway = CountingGateway(model_runtime.gateway)
            try:
                result = await _invoke_and_assemble(
                    run_id=f"live-n3-{index}", segments=segments, structured_answers=(),
                    existing_verifiable_claim_count=0,
                    run_started_at=datetime.now(UTC), model_gateway=gateway,
                )
            except Exception as error:
                if isinstance(error, MlApiGatewayError):
                    failures[f"MlApiGatewayError:{error.code}"] += 1
                else:
                    failures[getattr(error, "category", type(error).__name__)] += 1
            else:
                successes += 1
                claims[len(result.claims)] += 1
            if gateway.gateway_failures:
                first_attempt_gateway_failures[gateway.gateway_failures[0]] += 1
                retry_gateway_failures.update(gateway.gateway_failures[1:])
            calls += gateway.calls
    print({
        "total": 20,
        "success": successes,
        "failures": dict(failures),
        "total_model_invocations": calls,
        "retries": calls - 20,
        "first_attempt_gateway_failures": dict(first_attempt_gateway_failures),
        "retry_gateway_failures": dict(retry_gateway_failures),
        "claim_count_distribution": dict(claims),
    })


if __name__ == "__main__":
    asyncio.run(main())
