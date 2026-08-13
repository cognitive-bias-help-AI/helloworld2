"""Run each approved P0-6 mutation independently, then restore and recheck."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOCK = "app/gateway/adapters/mock.py"
CONTRACT = "tests/adapters/test_contract.py"


@dataclass(frozen=True)
class Mutation:
    name: str
    file: str
    old: str
    new: str
    test_name: str
    replace_all: bool = False


MUTATIONS = (
    Mutation(
        "M1 canonical output", MOCK, "EvidenceDraft(",
        "__import__('app.schemas.frozen', fromlist=['Evidence']).Evidence.model_construct(",
        "test_parse_returns_evidence_draft",
    ),
    Mutation(
        "M2 naive time", MOCK,
        'datetime.fromisoformat("2026-08-11T15:30:00+09:00")',
        'datetime.fromisoformat("2026-08-11T15:30:00+09:00").replace(tzinfo=None)',
        "test_published_at_is_aware",
    ),
    Mutation(
        "M3 future time", MOCK,
        'datetime.fromisoformat("2026-08-11T15:30:00+09:00")',
        'datetime.fromisoformat("2026-08-14T15:30:00+09:00")',
        "test_published_at_not_future",
    ),
    Mutation(
        "M4 source mapping", MOCK,
        "source_type=PROVIDER_SOURCE_TYPE[self.name]",
        'source_type="news"',
        "test_source_type_matches_provider",
    ),
    Mutation(
        "M5 bad URL", MOCK,
        'source_url="https://news.example.com/0001"',
        'source_url="ftp://news.example.com/0001"',
        "test_source_url_scheme",
    ),
    Mutation(
        "M6 raw_span over 500", MOCK,
        'raw_span="삼성전자 실적 전망 관련 뉴스"',
        'raw_span="X" * 501',
        "test_raw_span_budget",
    ),
    Mutation(
        "M7 normalized coverage", MOCK,
        "normalized_value={",
        'normalized_value=None if self.name in ("dart", "kiwoom") else {',
        "test_normalized_value_coverage",
        replace_all=True,
    ),
    Mutation(
        "M8 span scope", MOCK,
        'span_scope="headline_snippet"',
        'span_scope="full_text"',
        "test_span_scope_declared",
    ),
    Mutation(
        "M9 5xx mapping", MOCK,
        "return ReasonCode.UPSTREAM_5XX, True",
        "return ReasonCode.RATE_LIMIT, True",
        "test_error_classification",
    ),
    Mutation(
        "M10 429 retry", MOCK,
        "return ReasonCode.RATE_LIMIT, True",
        "return ReasonCode.RATE_LIMIT, False",
        "test_error_classification",
    ),
    Mutation(
        "M11 auth mapping", MOCK,
        "return ReasonCode.AUTH_FAILED, False",
        "return ReasonCode.AUTH_FAILED, True",
        "test_error_classification",
    ),
    Mutation(
        "M12 timeout mapping", MOCK,
        "return ReasonCode.UPSTREAM_TIMEOUT, True",
        "return ReasonCode.UPSTREAM_5XX, True",
        "test_error_classification",
    ),
    Mutation(
        "M13 invalid hint", MOCK,
        "retry_after_ms=1000, remaining=0, window_s=1",
        "retry_after_ms=1000, remaining=-1, window_s=1",
        "test_error_classification",
    ),
    Mutation(
        "M14 forbidden import", MOCK,
        "from datetime import datetime\n",
        "from datetime import datetime\n\nimport app.models\n",
        "test_no_llm_import",
    ),
    Mutation(
        "M15 nondeterminism", MOCK,
        '        published = datetime.fromisoformat("2026-08-11T15:30:00+09:00")',
        "        self.max_concurrency += 1\n"
        "        published = datetime.now().astimezone().replace(microsecond=self.max_concurrency)",
        "test_deterministic",
    ),
    Mutation(
        "M16 fixture secret", "tests/fixtures/naver/success.json",
        '  "provider": "naver",',
        '  "provider": "naver",\n  "api_key": "real-looking-value",',
        "test_no_secret_in_fixture",
    ),
)


def run_target(test_name: str) -> int:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            CONTRACT,
            "-q",
            "-p",
            "no:cacheprovider",
            "-k",
            test_name,
        ],
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    ).returncode


def main() -> int:
    detected = 0
    for mutation in MUTATIONS:
        path = ROOT / mutation.file
        original_bytes = path.read_bytes()
        original = original_bytes.decode("utf-8")
        count = original.count(mutation.old)
        expected_count = count if mutation.replace_all else 1
        if count == 0 or (not mutation.replace_all and count != 1):
            print(f"{mutation.name}: SETUP_ERROR count={count}")
            continue
        try:
            path.write_text(
                original.replace(mutation.old, mutation.new, expected_count), encoding="utf-8"
            )
            red = run_target(mutation.test_name)
        finally:
            path.write_bytes(original_bytes)
        green = run_target(mutation.test_name)
        status = "DETECTED" if red != 0 and green == 0 else f"FAILED red={red} green={green}"
        print(f"{mutation.name}: {status}")
        detected += status == "DETECTED"
    print(f"TOTAL {detected}/{len(MUTATIONS)}")
    return 0 if detected == len(MUTATIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
