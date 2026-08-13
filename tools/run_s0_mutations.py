"""Apply each S0 mutation, require RED, and restore the exact source bytes."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    old: str
    new: str
    test: str


LIMIT_TEST = "tests/s0/test_runtime_invariants.py"
FLOW_TEST = "tests/s0/test_vertical_slice.py"
CONTEXT_TEST = "tests/s0/test_runtime_context.py"

MUTATIONS = (
    Mutation("hitl_limit_low", "app/orchestration/limits.py", "HITL_REASK_LIMIT: Final = 2", "HITL_REASK_LIMIT: Final = 1", LIMIT_TEST),
    Mutation("hitl_limit_high", "app/orchestration/limits.py", "HITL_REASK_LIMIT: Final = 2", "HITL_REASK_LIMIT: Final = 3", LIMIT_TEST),
    Mutation("recollect_limit_low", "app/orchestration/limits.py", "GRAPH_RECOLLECT_LIMIT: Final = 1", "GRAPH_RECOLLECT_LIMIT: Final = 0", LIMIT_TEST),
    Mutation("recollect_limit_high", "app/orchestration/limits.py", "GRAPH_RECOLLECT_LIMIT: Final = 1", "GRAPH_RECOLLECT_LIMIT: Final = 2", LIMIT_TEST),
    Mutation("rewrite_limit_low", "app/orchestration/limits.py", "REWRITE_LIMIT: Final = 2", "REWRITE_LIMIT: Final = 1", LIMIT_TEST),
    Mutation("rewrite_limit_high", "app/orchestration/limits.py", "REWRITE_LIMIT: Final = 2", "REWRITE_LIMIT: Final = 3", LIMIT_TEST),
    Mutation("external_limit_low", "app/orchestration/limits.py", "EXTERNAL_CALL_LIMIT: Final = 25", "EXTERNAL_CALL_LIMIT: Final = 24", LIMIT_TEST),
    Mutation("external_limit_high", "app/orchestration/limits.py", "EXTERNAL_CALL_LIMIT: Final = 25", "EXTERNAL_CALL_LIMIT: Final = 26", LIMIT_TEST),
    Mutation("llm_coefficient_low", "app/orchestration/limits.py", "return 4 * claim_count + 9", "return 3 * claim_count + 9", LIMIT_TEST),
    Mutation("llm_coefficient_high", "app/orchestration/limits.py", "return 4 * claim_count + 9", "return 5 * claim_count + 9", LIMIT_TEST),
    Mutation("llm_constant_low", "app/orchestration/limits.py", "return 4 * claim_count + 9", "return 4 * claim_count + 8", LIMIT_TEST),
    Mutation("llm_constant_high", "app/orchestration/limits.py", "return 4 * claim_count + 9", "return 4 * claim_count + 10", LIMIT_TEST),
    Mutation("boundary_exclusive", "app/orchestration/limits.py", "current + additional <= limit", "current + additional < limit", LIMIT_TEST),
    Mutation("boundary_bypass", "app/orchestration/limits.py", "current + additional <= limit", "current <= limit", LIMIT_TEST),
    Mutation("citation_unknown_bypass", "app/orchestration/validators/citations.py", "if evidence is None:", "if evidence is not None:", LIMIT_TEST),
    Mutation("citation_span_bypass", "app/orchestration/validators/citations.py", "if citation.span not in evidence.raw_span:", "if citation.span in evidence.raw_span:", LIMIT_TEST),
    Mutation("raw_from_state", "app/orchestration/nodes/s0.py", "masked = _mask(runtime.context.raw_text)", "masked = _mask(state['raw_text'])", CONTEXT_TEST),
    Mutation("n0_put_input_bypass", "app/orchestration/nodes/s0.py", "await deps.review_store.put_input(", "await deps.review_store.put_report(", CONTEXT_TEST),
    Mutation("n0_raw_delta", "app/orchestration/nodes/s0.py", '"input_id": input_id', '"raw_text": input_id', CONTEXT_TEST),
    Mutation("thread_raw_coupling", "tests/s0/runtime_fixtures.py", '"thread_id": "thread-s0"', '"thread_id": RAW', CONTEXT_TEST),
    Mutation("extra_retry_n7", "app/orchestration/nodes/s0.py", 'for _ in range(2):\n                candidate, _ = await _invoke(deps, "n7"', 'for _ in range(3):\n                candidate, _ = await _invoke(deps, "n7"', "tests/s0/test_vertical_slice.py::test_degraded_two_failures_then_rule_fallback_and_no_third_call"),
    Mutation("partial_salvage", "app/assemblers/fallbacks.py", 'stance="unknown"', 'stance="support"', "tests/s0/test_fallbacks.py"),
    Mutation("coverage_code_drift", "app/assemblers/fallbacks.py", "ReasonCode.COVERAGE_TRUNCATED", "ReasonCode.CONTRACT_VIOLATION", "tests/s0/test_fallbacks.py"),
    Mutation("n3b_provenance", "app/orchestration/nodes/s0.py", "origin=SourceTrace.USER_CONFIRMED", "origin=SourceTrace.LLM_EXTRACTION", "tests/s0/test_vertical_slice.py::test_slot_HITL_resume_n3b_USER_CONFIRMED"),
    Mutation("vertex_drop", "app/orchestration/graph.py", ', "n12"', "", "tests/s0/test_runtime_context.py::test_14_vertices와_n0만_raw_text를_읽는_architecture"),
    Mutation("guard_before_generate", "app/orchestration/graph.py", '("n9", "n11")', '("n9", "n10")', FLOW_TEST),
    Mutation("publish_before_guard", "app/orchestration/graph.py", 'else "n10"', 'else "n12"', FLOW_TEST),
    Mutation("stock_membership_bypass", "app/orchestration/hitl.py", "if resume.selected_code not in by_code:", "if resume.selected_code in by_code:", "tests/s0/test_stock_resolution.py"),
)


def main() -> int:
    killed = 0
    for mutation in MUTATIONS:
        path = ROOT / mutation.path
        original = path.read_bytes()
        text = original.decode("utf-8")
        if text.count(mutation.old) != 1:
            print(f"ERROR {mutation.name}: anchor count={text.count(mutation.old)}")
            return 2
        try:
            path.write_text(text.replace(mutation.old, mutation.new, 1), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", mutation.test],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                check=False,
            )
            if result.returncode == 0:
                print(f"SURVIVED {mutation.name}")
                return 1
            killed += 1
            print(f"KILLED {mutation.name}")
        finally:
            path.write_bytes(original)
    print(f"TOTAL {killed}/{len(MUTATIONS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
