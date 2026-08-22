"""Phase-aware CI invariants for DDR v2.2."""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
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
I8_ROOTS = (ROOT / "app" / "prompts", ROOT / "app" / "orchestration")
I10_MAPPING = {
    "input_id": ("review", "get_input"),
    "claim_ids": ("review", "get_claims"),
    "query_ids": ("evidence", "get_queries"),
    "claim_evaluation_ids": ("review", "get_claim_evaluations"),
    "finding_ids": ("review", "get_findings"),
    "report_id": ("review", "get_report"),
}
I5_MEMORY_NODE = (
    "tests/store/test_memory_evidence_store.py::"
    "test_query와_evidence의_idempotency_conflict_run_scope를_검증한다"
)
I5_POSTGRES_NODES = (
    "tests/store/test_sql_evidence_store_postgres.py::"
    "test_physical_pk_fk_unique_and_composite_ownership",
    "tests/store/test_sql_evidence_store_postgres.py::"
    "test_concurrent_conflicting_evidence_hash_race",
)


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


@dataclass(frozen=True)
class PytestEvidence:
    returncode: int
    passed: int
    failed: int
    skipped: int
    errors: int
    detail: str


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


def _run_pytest_evidence(node_ids: tuple[str, ...]) -> PytestEvidence:
    with tempfile.TemporaryDirectory(prefix="i5-pytest-") as temp_dir:
        report = Path(temp_dir) / "report.xml"
        process = _run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                *node_ids,
                f"--junitxml={report}",
            ]
        )
        output = (process.stdout or process.stderr).strip().splitlines()
        detail = output[-1][:200] if output else "pytest produced no output"
        if not report.is_file():
            return PytestEvidence(process.returncode, 0, 0, 0, 0, detail)
        root = ET.parse(report).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.findall("./testsuite"))
        tests = sum(int(suite.get("tests", "0")) for suite in suites)
        failed = sum(int(suite.get("failures", "0")) for suite in suites)
        errors = sum(int(suite.get("errors", "0")) for suite in suites)
        skipped = sum(int(suite.get("skipped", "0")) for suite in suites)
        return PytestEvidence(
            process.returncode,
            tests - failed - errors - skipped,
            failed,
            skipped,
            errors,
            detail,
        )


def _i5_tests_passed(result: PytestEvidence, expected: int) -> bool:
    return (
        result.returncode == 0
        and result.passed == expected
        and result.failed == 0
        and result.skipped == 0
        and result.errors == 0
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
        "tests/s0/test_runtime_invariants.py::test_I3_7개_runtime_model_call이_existing_budget를_준수한다",
        CheckStatus.PASS,
        "all seven production LLM phases observed under NODE_BUDGETS",
    )


def check_i4() -> CheckResult:
    return _pytest_result(
        "tests/contexts/test_views.py::test_9개_semantic_View의_허용_필드가_고정된다",
        CheckStatus.PASS,
        "legacy and semantic n3/v2 exact contracts pass for all nine semantic Views",
    )


def check_i5() -> CheckResult:
    memory = _run_pytest_evidence((I5_MEMORY_NODE,))
    if not _i5_tests_passed(memory, 1):
        return CheckResult(CheckStatus.FAIL, f"Memory uniqueness proof failed: {memory.detail}")

    test_dsn = os.getenv("TEST_POSTGRES_DSN")
    if not test_dsn:
        return CheckResult(
            CheckStatus.PARTIAL,
            "Memory uniqueness verified; PostgreSQL physical acceptance inactive "
            "because TEST_POSTGRES_DSN is unavailable",
        )

    postgres_dsn = os.getenv("POSTGRES_DSN")
    if postgres_dsn and test_dsn == postgres_dsn:
        return CheckResult(
            CheckStatus.FAIL,
            "PostgreSQL physical acceptance refused because TEST_POSTGRES_DSN "
            "equals POSTGRES_DSN",
        )

    physical = _run_pytest_evidence(I5_POSTGRES_NODES)
    if not _i5_tests_passed(physical, len(I5_POSTGRES_NODES)):
        return CheckResult(
            CheckStatus.FAIL,
            "PostgreSQL physical uniqueness acceptance failed or did not fully execute: "
            f"passed={physical.passed} failed={physical.failed} "
            f"errors={physical.errors} skipped={physical.skipped}; {physical.detail}",
        )
    return CheckResult(
        CheckStatus.PASS,
        "Memory uniqueness and PostgreSQL physical UNIQUE/race acceptance passed",
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


def _canonical_schema_name(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if not isinstance(node, ast.Name):
        return None
    name = aliases.get(node.id, node.id)
    return name if name in CANONICAL_OUTPUT_SCHEMAS else None


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
                if keyword.arg != "output_schema":
                    continue
                if name := _canonical_schema_name(keyword.value, aliases):
                    violations.append(f"{path}:{node.lineno} output_schema={name}")
            invokes_model = (
                isinstance(node.func, ast.Attribute) and node.func.attr == "invoke"
            ) or (isinstance(node.func, ast.Name) and node.func.id == "invoke")
            if (
                invokes_model
                and len(node.args) >= 4
                and (name := _canonical_schema_name(node.args[3], aliases))
            ):
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
