"""Run the approved P0-5 mutations independently and restore every source file."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    name: str
    file: str
    old: str
    new: str
    test: str


DRAFTS = "app/orchestration/drafts.py"
ERRORS = "app/assemblers/errors.py"
N7 = "app/assemblers/claim_evidence.py"
N8 = "app/assemblers/claim_evaluation.py"
N9 = "app/assemblers/findings.py"
MOCK = "app/models/mock.py"

MUTATIONS = [
    Mutation("M-S1", DRAFTS, 'extra="forbid"', 'extra="ignore"', "tests/orchestration/test_drafts.py"),
    Mutation("M-S2", DRAFTS, 'if self.kind != "missing" and self.claim_evaluation_id is None:', 'if False:', "tests/orchestration/test_drafts.py"),
    Mutation("M-S3", DRAFTS, "class FindingDraft(OutputModel):\n    slot_id: SlotId", "class FindingDraft(OutputModel):\n    finding_id: ULID | None = None\n    slot_id: SlotId", "tests/orchestration/test_drafts.py"),
    Mutation("M-S4", DRAFTS, "    ] | None = None\n\n\nclass ExtractedClaimDraft", "    ] | ReasonCode | None = None\n\n\nclass ExtractedClaimDraft", "tests/orchestration/test_drafts.py"),
    Mutation("M-E1", ERRORS, '"duplicate_reference": ReasonCode.CONTRACT_VIOLATION', '"duplicate_reference": ReasonCode.SCHEMA_INVALID', "tests/orchestration/test_assemblers.py::test_AssemblyError_kind_reason_mapping과_retryability를_보존한다"),
    Mutation("M-E2", N7, 'raise AssemblyError("contract_violation", retryable=False)', 'raise AssemblyError("contract_violation", retryable=True)', "tests/orchestration/test_assemblers.py::test_n7은_packet과_mapping의_caller_duplicate_mismatch를_거부한다"),
    Mutation("M-7A", N7, '    if set(draft_ids) != packet:\n        raise AssemblyError("coverage_mismatch", retryable=True)\n', "", "tests/orchestration/test_assemblers.py::test_n7은_누락과_unknown을_재시도가능_오류로_구분한다"),
    Mutation("M-7B", N7, '    if unknown:\n        raise AssemblyError("unknown_reference", retryable=True)\n', "", "tests/orchestration/test_assemblers.py::test_n7은_누락과_unknown을_재시도가능_오류로_구분한다"),
    Mutation("M-7C", N7, '    if set(query_id_by_evidence) != packet:\n        raise AssemblyError("contract_violation", retryable=False)\n', "", "tests/orchestration/test_assemblers.py::test_n7은_packet과_mapping의_caller_duplicate_mismatch를_거부한다"),
    Mutation("M-7D", N7, "for item in sorted(draft.stances, key=lambda value: value.evidence_id)", "for item in draft.stances", "tests/orchestration/test_assemblers.py::test_n7은_exact_coverage와_query_lineage를_ID순으로_조립한다"),
    Mutation("M-8A", N8, '    if set(buckets) != packet:\n        raise AssemblyError("coverage_mismatch", retryable=True)\n', "", "tests/orchestration/test_assemblers.py::test_n8은_packet_duplicate_unknown_missing_numeric_unknown을_구분한다"),
    Mutation("M-8B", N8, '    if len(buckets) != len(set(buckets)):\n        raise AssemblyError("duplicate_reference", retryable=True)\n', "", "tests/orchestration/test_assemblers.py::test_n8은_bypass된_bucket_overlap과_citation_unknown을_잡는다"),
    Mutation("M-8C", N8, '    if any(citation.evidence_id not in set(buckets) for citation in draft.citations):\n        raise AssemblyError("unknown_reference", retryable=True)\n', "", "tests/orchestration/test_assemblers.py::test_n8은_bypass된_bucket_overlap과_citation_unknown을_잡는다"),
    Mutation("M-8D", N8, '    if any(check.evidence_id not in packet for check in numeric_checks):\n        raise AssemblyError("unknown_reference", retryable=False)\n', "", "tests/orchestration/test_assemblers.py::test_n8은_packet_duplicate_unknown_missing_numeric_unknown을_구분한다"),
    Mutation("M-8E", N8, "        verdict=draft.verdict,", '        verdict="contradicted" if any(item.result == "inconsistent" for item in checks) else draft.verdict,', "tests/orchestration/test_assemblers.py::test_n8은_inconsistent_NumericCheck로_LLM_verdict를_바꾸지_않는다"),
    Mutation("M-9A", N9, '        if evaluation is None:\n            raise AssemblyError("unknown_reference", retryable=True)\n', "", "tests/orchestration/test_assemblers.py::test_n9은_unknown_eval_evidence와_mismatch_no_citation을_구분한다"),
    Mutation("M-9B", N9, '        if any(citation.evidence_id not in allowed for citation in draft.citations):\n            raise AssemblyError("unknown_reference", retryable=True)\n', "", "tests/orchestration/test_assemblers.py::test_n9은_unknown_eval_evidence와_mismatch_no_citation을_구분한다"),
    Mutation("M-9C", N9, '    if len(keys) != len(set(keys)):\n        raise AssemblyError("duplicate_reference", retryable=True)\n', "", "tests/orchestration/test_assemblers.py::test_n9은_semantic_duplicate와_caller_ID_contract를_거부한다"),
    Mutation("M-9D", N9, "    ordered = sorted(drafts, key=_key)", "    ordered = drafts", "tests/orchestration/test_assemblers.py::test_n9은_semantic_sort후_ID를_주입하고_missing_None을_허용한다"),
    Mutation("M-9E", N9, '    if len(finding_ids) != len(drafts):\n        raise AssemblyError("contract_violation", retryable=False)\n', "", "tests/orchestration/test_assemblers.py::test_n9은_semantic_duplicate와_caller_ID_contract를_거부한다"),
    Mutation("M-9F", N9, '    if len(finding_ids) != len(set(finding_ids)):\n        raise AssemblyError("duplicate_reference", retryable=False)\n', "", "tests/orchestration/test_assemblers.py::test_n9은_semantic_duplicate와_caller_ID_contract를_거부한다"),
    Mutation("M-M1", MOCK, "        RenderDraft,\n    }", "        RenderDraft,\n        __import__('app.schemas.frozen', fromlist=['Finding']).Finding,\n    }", "tests/models/test_mock_gateway.py::test_MockModelGateway는_canonical_schema와_BaseModel아닌_input을_거부한다"),
    Mutation("M-M2", MOCK, "ctx_chars=ctx_chars(input_view)", "ctx_chars=input_view.ctx_chars()", "tests/models/test_mock_gateway.py::test_MockModelGateway는_정확히_8종_Draft를_반환하고_Usage를_계산한다"),
    Mutation("M-M3", MOCK, '        if not isinstance(input_view, BaseModel):\n            raise TypeError("input_view는 BaseModel이어야 함")\n', "", "tests/models/test_mock_gateway.py::test_MockModelGateway는_canonical_schema와_BaseModel아닌_input을_거부한다"),
]


def pytest(test: str) -> int:
    return subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    ).returncode


def main() -> int:
    killed = 0
    for mutation in MUTATIONS:
        path = ROOT / mutation.file
        original = path.read_text(encoding="utf-8")
        if original.count(mutation.old) != 1:
            print(f"{mutation.name} SETUP_ERROR replacement count={original.count(mutation.old)}")
            continue
        try:
            path.write_text(original.replace(mutation.old, mutation.new), encoding="utf-8")
            red = pytest(mutation.test)
        finally:
            path.write_text(original, encoding="utf-8")
        green = pytest(mutation.test)
        status = "DETECTED" if red != 0 and green == 0 else f"FAILED red={red} green={green}"
        print(f"{mutation.name} {status}")
        killed += status == "DETECTED"
    print(f"TOTAL {killed}/{len(MUTATIONS)}")
    return 0 if killed == len(MUTATIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
