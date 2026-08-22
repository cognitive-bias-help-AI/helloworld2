from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import ci.invariants as invariants
from ci.invariants import (
    I8_ROOTS,
    ROOT,
    CheckResult,
    CheckStatus,
    InvariantSpec,
    check_i5,
    check_i10_mapping,
    evaluate,
    main,
    scan_i8_roots,
)


def result(status: CheckStatus) -> CheckResult:
    return CheckResult(status, status.value)


def spec(name: str, status: CheckStatus, required_from: str = "p0") -> InvariantSpec:
    return InvariantSpec(name, name, required_from, lambda: result(status))


@pytest.mark.parametrize(
    "status",
    [
        CheckStatus.PASS,
        CheckStatus.FAIL,
        CheckStatus.PARTIAL,
        CheckStatus.PENDING,
        CheckStatus.CONTRACT_GAP,
    ],
)
def test_result_model_preserves_all_five_statuses(status):
    assert result(status).status is status


@pytest.mark.parametrize(
    "status,required_from,expected",
    [
        (CheckStatus.PASS, "p0", 0),
        (CheckStatus.FAIL, "s0", 1),
        (CheckStatus.PENDING, "s0", 0),
        (CheckStatus.PENDING, "p0", 1),
        (CheckStatus.PARTIAL, "s0", 0),
        (CheckStatus.PARTIAL, "p0", 1),
        (CheckStatus.CONTRACT_GAP, "s0", 0),
        (CheckStatus.CONTRACT_GAP, "p0", 1),
    ],
)
def test_phase_exit_semantics(status, required_from, expected):
    assert evaluate([spec("I1", status, required_from)], phase="p0", strict=False) == expected


def test_required_missing_checker_fails():
    missing = InvariantSpec("I1", "missing", "p0", None)
    assert evaluate([missing], phase="p0", strict=False) == 1


def test_checker_exception_fails():
    def explode():
        raise RuntimeError("boom")

    broken = InvariantSpec("I1", "broken", "p0", explode)
    assert evaluate([broken], phase="p0", strict=False) == 1


def test_strict_requires_every_selected_result_to_pass():
    assert evaluate([spec("I1", CheckStatus.PASS)], phase=None, strict=True) == 0
    for status in CheckStatus:
        if status is not CheckStatus.PASS:
            assert evaluate([spec("I1", status)], phase=None, strict=True) == 1


def test_only_marks_run_as_scoped(capsys, monkeypatch):
    monkeypatch.setattr("ci.invariants.SPECS", (spec("I2", CheckStatus.PASS),))
    assert main(["--phase", "p0", "--only", "I2"]) == 0
    assert "SCOPED RUN - NOT FULL PHASE CERTIFICATION" in capsys.readouterr().out


def test_default_is_p0_phase(capsys, monkeypatch):
    monkeypatch.setattr("ci.invariants.SPECS", (spec("I2", CheckStatus.PASS),))
    assert main([]) == 0
    output = capsys.readouterr().out
    assert "MODE: phase" in output
    assert "PHASE: p0" in output


def test_unknown_only_is_cli_error(monkeypatch):
    monkeypatch.setattr("ci.invariants.SPECS", (spec("I2", CheckStatus.PASS),))
    assert main(["--only", "I99"]) != 0


def test_phase_and_strict_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        main(["--phase", "p0", "--strict"])


def test_i8_detects_direct_and_aliased_canonical_output_schema(tmp_path: Path):
    root = tmp_path / "nodes"
    root.mkdir()
    (root / "bad.py").write_text(
        "from app.schemas.frozen import Evidence as E\nfoo(output_schema=E)\n",
        encoding="utf-8",
    )
    checked = scan_i8_roots((root,))
    assert checked.status is CheckStatus.FAIL
    assert "bad.py" in checked.message


def test_i8_detects_fourth_positional_canonical_output_schema(tmp_path: Path):
    root = tmp_path / "orchestration"
    root.mkdir()
    (root / "runtime.py").write_text(
        "from app.schemas.frozen import Evidence as E\n"
        'gateway.invoke("SMALL", "n1/v1", view, E)\n',
        encoding="utf-8",
    )

    checked = scan_i8_roots((root,))

    assert checked.status is CheckStatus.FAIL
    assert "output_schema=Evidence" in checked.message


def test_i8_roots_cover_intake_review_runtime():
    runtime = ROOT / "app" / "orchestration" / "intake_review_runtime.py"

    assert any(root == runtime or root in runtime.parents for root in I8_ROOTS)


@pytest.mark.parametrize("canonical", ["Evidence", "ClaimEvaluation", "Finding"])
def test_i8_detects_direct_canonical_names(tmp_path: Path, canonical: str):
    root = tmp_path / "prompts"
    root.mkdir()
    (root / "bad.py").write_text(f"foo(output_schema={canonical})\n", encoding="utf-8")
    assert scan_i8_roots((root,)).status is CheckStatus.FAIL


def test_i8_allows_draft_schema(tmp_path: Path):
    root = tmp_path / "nodes"
    root.mkdir()
    (root / "good.py").write_text("foo(output_schema=EvidenceDraft)\n", encoding="utf-8")
    assert scan_i8_roots((root,)).status is CheckStatus.PASS


def test_i8_missing_or_empty_required_roots_are_pending(tmp_path: Path):
    missing = tmp_path / "missing"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert scan_i8_roots((missing, empty)).status is CheckStatus.PENDING


def test_i10_accepts_six_fields_and_store_read_paths():
    fields = {
        "input_id",
        "claim_ids",
        "query_ids",
        "claim_evaluation_ids",
        "finding_ids",
        "report_id",
    }
    review_methods = {
        "get_input",
        "get_claims",
        "get_claim_evaluations",
        "get_findings",
        "get_report",
    }
    evidence_methods = {"get_queries"}
    assert check_i10_mapping(fields, review_methods, evidence_methods).status is CheckStatus.PASS


def test_i10_fails_when_state_field_or_store_method_is_missing():
    fields = {
        "input_id",
        "claim_ids",
        "query_ids",
        "claim_evaluation_ids",
        "finding_ids",
    }
    review_methods = {"get_input", "get_claims", "get_claim_evaluations", "get_findings"}
    evidence_methods = {"get_queries"}
    assert check_i10_mapping(fields, review_methods, evidence_methods).status is CheckStatus.FAIL

    fields.add("report_id")
    assert check_i10_mapping(fields, review_methods, evidence_methods).status is CheckStatus.FAIL


def pytest_evidence(*, passed=0, failed=0, skipped=0, errors=0, returncode=0):
    return SimpleNamespace(
        returncode=returncode,
        passed=passed,
        failed=failed,
        skipped=skipped,
        errors=errors,
        detail="controlled test result",
    )


def test_i5_without_test_dsn_is_partial_and_does_not_use_postgres_dsn(
    monkeypatch,
):
    monkeypatch.delenv("TEST_POSTGRES_DSN", raising=False)
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://must-not-be-used")
    calls = []

    def run(nodes):
        calls.append(nodes)
        return pytest_evidence(passed=1)

    monkeypatch.setattr("ci.invariants._run_pytest_evidence", run)

    checked = check_i5()

    assert checked.status is CheckStatus.PARTIAL
    assert "TEST_POSTGRES_DSN is unavailable" in checked.message
    assert calls == [(invariants.I5_MEMORY_NODE,)]


def test_i5_with_active_dsn_passes_only_after_exact_required_tests_execute(monkeypatch):
    monkeypatch.setenv("TEST_POSTGRES_DSN", "postgresql://test")
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    results = iter((pytest_evidence(passed=1), pytest_evidence(passed=2)))
    calls = []

    def run(nodes):
        calls.append(nodes)
        return next(results)

    monkeypatch.setattr("ci.invariants._run_pytest_evidence", run)

    checked = check_i5()

    assert checked.status is CheckStatus.PASS
    assert calls == [(invariants.I5_MEMORY_NODE,), invariants.I5_POSTGRES_NODES]


def test_i5_with_active_dsn_fails_when_physical_test_fails(monkeypatch):
    monkeypatch.setenv("TEST_POSTGRES_DSN", "postgresql://test")
    monkeypatch.setattr(
        "ci.invariants._run_pytest_evidence",
        lambda nodes: (
            pytest_evidence(passed=1)
            if nodes == (invariants.I5_MEMORY_NODE,)
            else pytest_evidence(passed=1, failed=1, returncode=1)
        ),
    )

    assert check_i5().status is CheckStatus.FAIL


def test_i5_with_active_dsn_fails_when_required_test_is_skipped(monkeypatch):
    monkeypatch.setenv("TEST_POSTGRES_DSN", "postgresql://test")
    monkeypatch.setattr(
        "ci.invariants._run_pytest_evidence",
        lambda nodes: (
            pytest_evidence(passed=1)
            if nodes == (invariants.I5_MEMORY_NODE,)
            else pytest_evidence(passed=1, skipped=1)
        ),
    )

    assert check_i5().status is CheckStatus.FAIL


def test_i5_with_active_dsn_fails_when_postgres_cannot_execute(monkeypatch):
    monkeypatch.setenv("TEST_POSTGRES_DSN", "postgresql://unreachable")
    monkeypatch.setattr(
        "ci.invariants._run_pytest_evidence",
        lambda nodes: (
            pytest_evidence(passed=1)
            if nodes == (invariants.I5_MEMORY_NODE,)
            else pytest_evidence(returncode=4)
        ),
    )

    assert check_i5().status is CheckStatus.FAIL


def test_i5_postgres_selection_is_explicit_and_only_covers_physical_uniqueness():
    assert invariants.I5_POSTGRES_NODES == (
        "tests/store/test_sql_evidence_store_postgres.py::"
        "test_physical_pk_fk_unique_and_composite_ownership",
        "tests/store/test_sql_evidence_store_postgres.py::"
        "test_concurrent_conflicting_evidence_hash_race",
    )
