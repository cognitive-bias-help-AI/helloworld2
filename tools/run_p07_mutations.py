"""P0-7 phase-aware invariant runner mutation gate."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVARIANTS = ROOT / "ci" / "invariants.py"


@dataclass(frozen=True)
class Mutation:
    name: str
    old: bytes
    new: bytes
    command: tuple[str, ...]


def _pytest(node: str) -> tuple[str, ...]:
    return (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", node)


MUTATIONS = (
    Mutation(
        "M1 required checker missing",
        b'InvariantSpec("I2", "reducer-order-independence", "p0", check_i2)',
        b'InvariantSpec("I2", "reducer-order-independence", "p0", None)',
        (sys.executable, "-m", "ci.invariants", "--phase", "p0", "--only", "I2"),
    ),
    Mutation(
        "M2 required pending",
        b'InvariantSpec("I2", "reducer-order-independence", "p0", check_i2)',
        b'InvariantSpec("I2", "reducer-order-independence", "p0", check_i1)',
        (sys.executable, "-m", "ci.invariants", "--phase", "p0", "--only", "I2"),
    ),
    Mutation(
        "M3 future FAIL ignored",
        b'InvariantSpec("I3", "llm-context-budget", "s0", check_i3)',
        b'InvariantSpec("I3", "llm-context-budget", "s0", lambda: CheckResult(CheckStatus.FAIL, "mutation"))',
        (sys.executable, "-m", "ci.invariants", "--phase", "p0", "--only", "I3"),
    ),
    Mutation(
        "M4 I2 wrapper target",
        "tests/orchestration/test_state.py::test_add_unique의_I2는_집합_의미로_순서에_독립적이다".encode(),
        b"tests/orchestration/test_state.py::missing_i2_target",
        (sys.executable, "-m", "ci.invariants", "--phase", "p0", "--only", "I2"),
    ),
    Mutation(
        "M5 I4 wrapper target",
        "tests/contexts/test_views.py::test_8개_semantic_View의_허용_필드가_고정된다".encode(),
        b"tests/contexts/test_views.py::missing_i4_target",
        (sys.executable, "-m", "ci.invariants", "--phase", "p0", "--only", "I4"),
    ),
    Mutation(
        "M6 I9 wrapper target",
        b"tests/adapters/test_contract.py::TestProviderContract::test_source_type_matches_provider",
        b"tests/adapters/test_contract.py::TestProviderContract::missing_i9_target",
        (sys.executable, "-m", "ci.invariants", "--phase", "p0", "--only", "I9"),
    ),
    Mutation(
        "M7 I10 mapping",
        b'"report_id": ("review", "get_report")',
        b'"report_id": ("review", "get_nonexistent_report")',
        (sys.executable, "-m", "ci.invariants", "--phase", "p0", "--only", "I10"),
    ),
    Mutation(
        "M8 I11 threshold",
        b'"tools/measure_state.py", "--assert-under", "5120"',
        b'"tools/measure_state.py", "--assert-under", "1"',
        (sys.executable, "-m", "ci.invariants", "--phase", "p0", "--only", "I11"),
    ),
    Mutation(
        "M9 I8 canonical AST",
        b'{"Evidence", "ClaimEvidence", "ClaimEvaluation", "Finding"}',
        b'{"Evidence", "ClaimEvidence", "ClaimEvaluation"}',
        _pytest("tests/ci/test_invariants.py::test_i8_detects_direct_canonical_names[Finding]"),
    ),
    Mutation(
        "M10 I8 vacuous pass",
        b'return CheckResult(CheckStatus.PENDING, "required prompts/nodes Python artifacts are absent")',
        b'return CheckResult(CheckStatus.PASS, "required prompts/nodes Python artifacts are absent")',
        _pytest("tests/ci/test_invariants.py::test_i8_missing_or_empty_required_roots_are_pending"),
    ),
    Mutation(
        "M11 strict non-pass accepted",
        b"required and checked.status is not CheckStatus.PASS",
        b"required and checked.status is CheckStatus.FAIL",
        _pytest("tests/ci/test_invariants.py::test_strict_requires_every_selected_result_to_pass"),
    ),
)


def main() -> int:
    original = INVARIANTS.read_bytes()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    detected = 0
    try:
        for mutation in MUTATIONS:
            if original.count(mutation.old) != 1:
                print(f"TEST_GAP {mutation.name}: source anchor count != 1")
                continue
            INVARIANTS.write_bytes(original.replace(mutation.old, mutation.new, 1))
            process = subprocess.run(
                mutation.command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=90,
                check=False,
            )
            if process.returncode:
                detected += 1
                print(f"DETECTED {mutation.name}")
            else:
                print(f"TEST_GAP {mutation.name}: mutation stayed green")
            INVARIANTS.write_bytes(original)
    finally:
        INVARIANTS.write_bytes(original)
    print(f"TOTAL {detected}/{len(MUTATIONS)}")
    return 0 if detected == len(MUTATIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
