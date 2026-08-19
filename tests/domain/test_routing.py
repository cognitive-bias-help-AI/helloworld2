from itertools import permutations

import pytest

from app.domain.hitl_policy import select_ask_targets
from app.domain.intake import ResponseState
from app.domain.missing import analyze_missing
from app.domain.routing import RoutingOutcome, decide_routing
from app.domain.slot_resolution import (
    CurrentSlotProjection,
    CurrentSlotStatus,
    to_missing_observations,
)

VALUES = {
    1: ("CONSIDER_ENTRY",),
    2: ("NOT_HOLDING",),
    3: ("LONG",),
    4: ("개인 관심",),
    5: ("장기 성장",),
    6: ("NEWS",),
    7: ("가격 부담",),
    8: ("전제가 바뀌면 재검토",),
}


def projection(
    slot_id: int,
    *,
    status: CurrentSlotStatus = CurrentSlotStatus.RESOLVED,
    response_state: ResponseState = ResponseState.ANSWERED,
) -> CurrentSlotProjection:
    return CurrentSlotProjection(
        slot_id=slot_id,
        status=status,
        values=VALUES[slot_id] if status is CurrentSlotStatus.RESOLVED else (),
        response_state=response_state,
    )


def all_resolved() -> tuple[CurrentSlotProjection, ...]:
    return tuple(projection(slot_id) for slot_id in range(1, 9))


def policy_inputs(projections):
    missing = analyze_missing(to_missing_observations(projections))
    return missing, select_ask_targets(missing)


def test_Claim이_있어도_AskTarget이_있으면_NEEDS_HITL이다():
    projections = list(all_resolved())
    projections[1] = projection(
        2,
        status=CurrentSlotStatus.ABSENT,
        response_state=ResponseState.UNKNOWN,
    )
    missing, ask_targets = policy_inputs(projections)

    result = decide_routing(
        projections,
        missing,
        ask_targets,
        verifiable_claim_count=1,
        hard_blocked=False,
    )

    assert result is RoutingOutcome.NEEDS_HITL


def test_AskTarget과_blocking_missing이_없고_Claim이_있으면_READY_FOR_EVIDENCE다():
    projections = all_resolved()
    missing, ask_targets = policy_inputs(projections)

    assert (
        decide_routing(
            projections,
            missing,
            ask_targets,
            verifiable_claim_count=1,
            hard_blocked=False,
        )
        is RoutingOutcome.READY_FOR_EVIDENCE
    )


def test_Claim이_없어도_해결된_context는_CONTEXT_ONLY다():
    projections = all_resolved()
    missing, ask_targets = policy_inputs(projections)

    assert (
        decide_routing(
            projections,
            missing,
            ask_targets,
            verifiable_claim_count=0,
            hard_blocked=False,
        )
        is RoutingOutcome.CONTEXT_ONLY
    )


def test_질문하지_않는_optional_missing은_CONTEXT_ONLY를_막지_않는다():
    projections = list(all_resolved())
    projections[5] = projection(
        6,
        status=CurrentSlotStatus.ABSENT,
        response_state=ResponseState.UNKNOWN,
    )
    missing, ask_targets = policy_inputs(projections)

    assert ask_targets == ()
    assert (
        decide_routing(
            projections,
            missing,
            ask_targets,
            verifiable_claim_count=0,
            hard_blocked=False,
        )
        is RoutingOutcome.CONTEXT_ONLY
    )


def test_declined_blocking은_AskTarget이_없어도_BLOCKED다():
    projections = list(all_resolved())
    projections[0] = projection(
        1,
        status=CurrentSlotStatus.ABSENT,
        response_state=ResponseState.USER_DECLINED,
    )
    missing, ask_targets = policy_inputs(projections)

    assert ask_targets == ()
    assert (
        decide_routing(
            projections,
            missing,
            ask_targets,
            verifiable_claim_count=1,
            hard_blocked=False,
        )
        is RoutingOutcome.BLOCKED
    )


def test_hard_block은_AskTarget보다_우선한다():
    projections = list(all_resolved())
    projections[1] = projection(
        2,
        status=CurrentSlotStatus.ABSENT,
        response_state=ResponseState.UNKNOWN,
    )
    missing, ask_targets = policy_inputs(projections)

    assert (
        decide_routing(
            projections,
            missing,
            ask_targets,
            verifiable_claim_count=1,
            hard_blocked=True,
        )
        is RoutingOutcome.BLOCKED
    )


def test_routing은_입력순서와_무관하다():
    projections = list(all_resolved())
    projections[1] = projection(
        2,
        status=CurrentSlotStatus.ABSENT,
        response_state=ResponseState.UNKNOWN,
    )
    missing, ask_targets = policy_inputs(projections)

    outcomes = {
        decide_routing(
            projection_order,
            missing_order,
            ask_order,
            verifiable_claim_count=2,
            hard_blocked=False,
        )
        for projection_order in (projections, list(reversed(projections)))
        for missing_order in (missing, tuple(reversed(missing)))
        for ask_order in permutations(ask_targets)
    }

    assert outcomes == {RoutingOutcome.NEEDS_HITL}


def test_routing은_8개_projection과_nonnegative_claim_count를_요구한다():
    projections = all_resolved()

    with pytest.raises(ValueError, match="exactly 8"):
        decide_routing(
            projections[:-1],
            (),
            (),
            verifiable_claim_count=0,
            hard_blocked=False,
        )
    with pytest.raises(ValueError, match="non-negative"):
        decide_routing(
            projections,
            (),
            (),
            verifiable_claim_count=-1,
            hard_blocked=False,
        )
