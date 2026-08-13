"""P0/S0 검증을 위한 비운영 메모리 ReviewStore."""

from hashlib import sha256

from app.schemas.frozen import Claim, ClaimEvaluation, ClaimEvidence, Finding


def _body_id(kind: str, run_id: str) -> str:
    return "01" + sha256(f"{kind}|{run_id}".encode()).hexdigest().upper()[:24]


class MemoryReviewStore:
    def __init__(self) -> None:
        self._inputs: dict[str, dict] = {}
        self._input_runs: dict[str, str] = {}
        self._claims: dict[str, Claim] = {}
        self._claim_runs: dict[str, str] = {}
        self._claim_evidence: dict[tuple[str, str, str], ClaimEvidence] = {}
        self._evaluations: dict[str, ClaimEvaluation] = {}
        self._evaluation_runs: dict[str, str] = {}
        self._current_evaluations: dict[tuple[str, str], str] = {}
        self._findings: dict[str, Finding] = {}
        self._finding_runs: dict[str, str] = {}
        self._reports: dict[str, dict] = {}
        self._report_runs: dict[str, str] = {}

    async def put_input(self, run_id: str, body: dict) -> str:
        item_id = _body_id("input", run_id)
        self._inputs[item_id] = dict(body)
        self._input_runs[item_id] = run_id
        return item_id

    async def get_input(self, input_id: str) -> dict:
        return dict(self._inputs[input_id])

    async def put_claims(self, run_id: str, items: list[Claim]) -> list[str]:
        for item in items:
            old = self._claims.get(item.claim_id)
            if old is not None and (old != item or self._claim_runs[item.claim_id] != run_id):
                raise ValueError("claim_id ownership/payload conflict")
        for item in items:
            self._claims[item.claim_id], self._claim_runs[item.claim_id] = item, run_id
        return [item.claim_id for item in items]

    async def get_claims(self, claim_ids: list[str]) -> list[Claim]:
        return [self._claims[item_id] for item_id in claim_ids]

    async def put_claim_evidence(self, run_id: str, items: list[ClaimEvidence]) -> list[str]:
        for item in items:
            self._claim_evidence[(run_id, item.claim_id, item.evidence_id)] = item
        return [item.key for item in items]

    async def get_claim_evidence(self, run_id: str, claim_id: str) -> list[ClaimEvidence]:
        return [
            item
            for (owner, cid, _), item in sorted(self._claim_evidence.items())
            if owner == run_id and cid == claim_id
        ]

    async def put_claim_evaluations(self, run_id: str, items: list[ClaimEvaluation]) -> list[str]:
        for item in items:
            key = (run_id, item.claim_id)
            previous = self._current_evaluations.get(key)
            if previous is not None:
                self._evaluations.pop(previous, None)
                self._evaluation_runs.pop(previous, None)
            self._evaluations[item.claim_evaluation_id] = item
            self._evaluation_runs[item.claim_evaluation_id] = run_id
            self._current_evaluations[key] = item.claim_evaluation_id
        return [item.claim_evaluation_id for item in items]

    async def get_claim_evaluations(self, ids: list[str]) -> list[ClaimEvaluation]:
        return [self._evaluations[item_id] for item_id in ids if item_id in self._evaluations]

    async def put_findings(self, run_id: str, items: list[Finding]) -> list[str]:
        for item in items:
            old = self._findings.get(item.finding_id)
            if old is not None and (old != item or self._finding_runs[item.finding_id] != run_id):
                raise ValueError("finding_id ownership/payload conflict")
            self._findings[item.finding_id], self._finding_runs[item.finding_id] = item, run_id
        return [item.finding_id for item in items]

    async def get_findings(self, ids: list[str]) -> list[Finding]:
        return [self._findings[item_id] for item_id in ids]

    async def put_report(self, run_id: str, body: dict) -> str:
        item_id = _body_id("report", run_id)
        self._reports[item_id] = dict(body)
        self._report_runs[item_id] = run_id
        return item_id

    async def get_report(self, report_id: str) -> dict | None:
        body = self._reports.get(report_id)
        return None if body is None else dict(body)
