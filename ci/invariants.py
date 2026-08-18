"""Phase-aware CI invariants for DDR v2.2."""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import get_type_hints

from app.orchestration.state import ReviewState
from app.store.protocols import EvidenceStore, ReviewStore

ROOT = Path(__file__).resolve().parents[1]
PHASE_ORDER = {"p0": 0, "s0": 1, "t2": 2}
CANONICAL_OUTPUT_SCHEMAS = {"Evidence", "ClaimEvidence", "ClaimEvaluation", "Finding"}
I8_ROOTS = (ROOT / "app" / "prompts", ROOT / "app" / "orchestration" / "nodes")
I10_MAPPING = {
    "input_id": ("review", "get_input"),
    "claim_ids": ("review", "get_claims"),
    "query_ids": ("evidence", "get_queries"),
    "claim_evaluation_ids": ("review", "get_claim_evaluations"),
    "finding_ids": ("review", "get_findings"),
    "report_id": ("review", "get_report"),
}


class CheckStatus(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"
    PENDING = "PENDING"
    CONTRACT_GAP = "CONTRACT_GAP"


@dataclass(frozen=True)
class CheckResult:
    status: CheckStatus
    message: str


Check = Callable[[], CheckResult]


@dataclass(frozen=True)
class InvariantSpec:
    invariant_id: str
    label: str
    required_from: str
    check: Check | None


def _run(command: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    try:
        return subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(command, 124, exc.stdout or "", "timeout")


def _command_result(command: list[str], success: CheckStatus, message: str) -> CheckResult:
    process = _run(command)
    output = (process.stdout or process.stderr).strip().splitlines()
    detail = output[-1][:200] if output else message
    if process.returncode:
        return CheckResult(CheckStatus.FAIL, f"{message}; exit={process.returncode}: {detail}")
    return CheckResult(success, message)


def _pytest_result(node_id: str, success: CheckStatus, message: str) -> CheckResult:
    return _command_result(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", node_id],
        success,
        message,
    )


def check_i1() -> CheckResult:
    return _pytest_result(
        "tests/s0/test_runtime_context.py::test_runtime_context_n0_ownership과_checkpoint_leakage",
        CheckStatus.PASS,
        "actual LangGraph saver serialization stays within 5120 bytes",
    )


def check_i2() -> CheckResult:
    return _pytest_result(
        "tests/orchestration/test_state.py::test_add_unique의_I2는_집합_의미로_순서에_독립적이다",
        CheckStatus.PASS,
        "existing reducer test: seeded shuffle 5 times yields one semantic result",
    )


def check_i3() -> CheckResult:
    return _pytest_result(
        "tests/s0/test_runtime_invariants.py::test_I3_8개_runtime_model_call이_existing_budget를_준수한다",
        CheckStatus.PASS,
        "all eight runtime LLM vertices observed under existing NODE_BUDGETS",
    )


def check_i4() -> CheckResult:
    return _pytest_result(
        "tests/contexts/test_views.py::test_9개_semantic_View의_허용_필드가_고정된다",
        CheckStatus.PASS,
        "legacy and semantic n3/v2 exact contracts pass for all nine semantic Views",
    )


def check_i5() -> CheckResult:
    return _pytest_result(
        "tests/store/test_memory_evidence_store.py::test_query와_evidence의_idempotency_conflict_run_scope를_검증한다",
        CheckStatus.PARTIAL,
        "reference store uniqueness passes; PostgreSQL physical constraint pending T2",
    )


def check_i6() -> CheckResult:
    return _pytest_result(
        "tests/s0/test_runtime_invariants.py",
        CheckStatus.PASS,
        "six termination and call ceilings are backed by runtime tests",
    )


def check_i7() -> CheckResult:
    return _pytest_result(
        "tests/s0/test_runtime_invariants.py::test_I7_unknown과_span_mismatch는_report_publish전에_거부된다",
        CheckStatus.PASS,
        "citation identity and exact-span containment run before report persistence",
    )


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for imported in node.names:
                if imported.name in CANONICAL_OUTPUT_SCHEMAS:
                    aliases[imported.asname or imported.name] = imported.name
    return aliases


def scan_i8_roots(roots: Sequence[Path]) -> CheckResult:
    sources = sorted(path for root in roots if root.is_dir() for path in root.rglob("*.py"))
    sources = [path for path in sources if path.name != "__init__.py" or path.stat().st_size]
    if not sources:
        return CheckResult(
            CheckStatus.PENDING, "required prompts/nodes Python artifacts are absent"
        )

    violations: list[str] = []
    for path in sources:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            return CheckResult(CheckStatus.FAIL, f"cannot scan {path}: {exc}")
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "output_schema" or not isinstance(keyword.value, ast.Name):
                    continue
                name = aliases.get(keyword.value.id, keyword.value.id)
                if name in CANONICAL_OUTPUT_SCHEMAS:
                    violations.append(f"{path}:{node.lineno} output_schema={name}")
    if violations:
        return CheckResult(CheckStatus.FAIL, "; ".join(violations))
    return CheckResult(CheckStatus.PASS, f"scanned {len(sources)} Python source file(s)")


def check_i8() -> CheckResult:
    return scan_i8_roots(I8_ROOTS)


def check_i9() -> CheckResult:
    return _pytest_result(
        "tests/adapters/test_contract.py::TestProviderContract::test_source_type_matches_provider",
        CheckStatus.PASS,
        "P0-6 common adapter source_type contract passes",
    )


def check_i10_mapping(
    state_fields: Collection[str],
    review_methods: Collection[str],
    evidence_methods: Collection[str],
) -> CheckResult:
    missing_fields = sorted(set(I10_MAPPING) - set(state_fields))
    missing_methods = sorted(
        method
        for _, (store, method) in I10_MAPPING.items()
        if method not in (review_methods if store == "review" else evidence_methods)
    )
    if missing_fields or missing_methods:
        details = []
        if missing_fields:
            details.append(f"missing State fields: {', '.join(missing_fields)}")
        if missing_methods:
            details.append(f"missing Store methods: {', '.join(missing_methods)}")
        return CheckResult(CheckStatus.FAIL, "; ".join(details))
    return CheckResult(CheckStatus.PASS, "six State reference fields have Store read paths")


def check_i10() -> CheckResult:
    state_fields = set(get_type_hints(ReviewState, include_extras=True))
    review_methods = {name for name in ReviewStore.__dict__ if not name.startswith("_")}
    evidence_methods = {name for name in EvidenceStore.__dict__ if not name.startswith("_")}
    return check_i10_mapping(state_fields, review_methods, evidence_methods)


def check_i11() -> CheckResult:
    process = _run([sys.executable, "tools/measure_state.py", "--assert-under", "5120"])
    lines = [line.strip() for line in process.stdout.splitlines() if line.strip().startswith("C=")]
    if process.returncode:
        detail = (process.stderr or process.stdout).strip().splitlines()
        return CheckResult(CheckStatus.FAIL, (detail or ["state measurement failed"])[-1][:200])
    ascii_lines = [line.encode("ascii", "ignore").decode().strip() for line in lines]
    return CheckResult(CheckStatus.PASS, " / ".join(ascii_lines))


SPECS = (
    InvariantSpec("I1", "runtime-checkpoint-size", "s0", check_i1),
    InvariantSpec("I2", "reducer-order-independence", "p0", check_i2),
    InvariantSpec("I3", "llm-context-budget", "s0", check_i3),
    InvariantSpec("I4", "view-forbidden-fields", "p0", check_i4),
    InvariantSpec("I5", "evidence-unique", "t2", check_i5),
    InvariantSpec("I6", "loop-termination", "s0", check_i6),
    InvariantSpec("I7", "citation-span-containment", "s0", check_i7),
    InvariantSpec("I8", "canonical-output-schema-ast", "s0", check_i8),
    InvariantSpec("I9", "adapter-source-type", "p0", check_i9),
    InvariantSpec("I10", "state-store-access-path", "p0", check_i10),
    InvariantSpec("I11", "representative-state-size", "p0", check_i11),
)


def _display_status(status: CheckStatus) -> str:
    return "GAP" if status is CheckStatus.CONTRACT_GAP else status.value


def evaluate(specs: Sequence[InvariantSpec], *, phase: str | None, strict: bool) -> int:
    failed = False
    required_total = 0
    required_passed = 0
    for item in specs:
        required = strict or PHASE_ORDER[phase or "p0"] >= PHASE_ORDER[item.required_from]
        if not strict and required:
            required_total += 1
        if item.check is None:
            checked = CheckResult(CheckStatus.FAIL, "required checker is missing")
        else:
            try:
                checked = item.check()
            except Exception as exc:  # runner boundary must convert exceptions into RED
                checked = CheckResult(
                    CheckStatus.FAIL, f"checker raised {type(exc).__name__}: {exc}"
                )
        print(f"[{_display_status(checked.status):<7}] {item.invariant_id:<4} {item.label}")
        print(f"          {checked.message}")
        if checked.status is CheckStatus.FAIL or (
            required and checked.status is not CheckStatus.PASS
        ):
            failed = True
        elif not strict and required:
            required_passed += 1

    if strict:
        print(f"STRICT: {'PASS' if not failed else 'NOT GREEN'}")
    else:
        print(f"{phase.upper()} REQUIRED: {required_passed}/{required_total} PASS")
    return 1 if failed else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ci.invariants")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--phase", choices=tuple(PHASE_ORDER))
    mode.add_argument("--strict", action="store_true")
    parser.add_argument("--only", help="comma-separated invariant IDs, e.g. I2,I4")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    phase = None if args.strict else (args.phase or "p0")
    print(f"MODE: {'strict' if args.strict else 'phase'}")
    if phase:
        print(f"PHASE: {phase}")

    selected = list(SPECS)
    if args.only:
        requested = list(
            dict.fromkeys(part.strip().upper() for part in args.only.split(",") if part.strip())
        )
        known = {item.invariant_id for item in SPECS}
        unknown = [name for name in requested if name not in known]
        if not requested or unknown:
            print(f"ERROR: unknown or empty invariant ID: {', '.join(unknown) or '(empty)'}")
            return 2
        by_id = {item.invariant_id: item for item in SPECS}
        selected = [by_id[name] for name in requested]
        print("SCOPED RUN - NOT FULL PHASE CERTIFICATION")

    return evaluate(selected, phase=phase, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
