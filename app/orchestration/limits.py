"""DDR I6 loop and call ceilings."""

from typing import Final

HITL_REASK_LIMIT: Final = 2
GRAPH_RECOLLECT_LIMIT: Final = 1
REWRITE_LIMIT: Final = 2
EXTERNAL_CALL_LIMIT: Final = 25


def llm_call_limit(claim_count: int) -> int:
    return 4 * claim_count + 9


def permits(current: int, limit: int, additional: int = 1) -> bool:
    return current + additional <= limit
