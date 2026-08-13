from __future__ import annotations

from pathlib import Path

import pytest

from ci.invariants import (
    CheckResult,
    CheckStatus,
    InvariantSpec,
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
