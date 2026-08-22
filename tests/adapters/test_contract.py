from copy import deepcopy

import pytest

from app.schemas.frozen import PROVIDER_SOURCE_TYPE, EvidenceDraft
from tests.adapters.cases import (
    CONTRACT_ADAPTER_CASES,
    CONTRACT_ERROR_CASES,
    assert_no_fixture_secrets,
    assert_no_forbidden_imports,
    expectations_by_source_ref,
    normalized_coverage_by_source_type,
    raw_span_metrics,
)

CANONICAL_FIELDS = {
    "evidence_id", "content_sha256", "provider_request_id", "fetched_at", "as_of"
}


@pytest.mark.parametrize("case", CONTRACT_ADAPTER_CASES, ids=lambda case: case.case_id)
class TestProviderContract:
    def test_parse_returns_evidence_draft(self, case):
        drafts = case.adapter.parse_response(deepcopy(case.raw), case.query)
        assert isinstance(drafts, list)
        assert drafts and all(type(draft) is EvidenceDraft for draft in drafts)

    def test_draft_has_no_canonical_fields(self, case):
        drafts = case.adapter.parse_response(deepcopy(case.raw), case.query)
        assert all(not (CANONICAL_FIELDS & set(draft.model_dump())) for draft in drafts)

    def test_published_at_is_aware(self, case):
        drafts = case.adapter.parse_response(deepcopy(case.raw), case.query)
        assert all(
            draft.published_at is None or draft.published_at.utcoffset() is not None
            for draft in drafts
        )

    def test_published_at_not_future(self, case):
        drafts = case.adapter.parse_response(deepcopy(case.raw), case.query)
        assert all(
            draft.published_at is None or draft.published_at <= case.collected_at
            for draft in drafts
        )

    def test_source_type_matches_provider(self, case):
        drafts = case.adapter.parse_response(deepcopy(case.raw), case.query)
        assert all(
            draft.source_type == PROVIDER_SOURCE_TYPE[case.adapter.name] for draft in drafts
        )

    def test_source_url_scheme(self, case):
        drafts = case.adapter.parse_response(deepcopy(case.raw), case.query)
        assert all(
            draft.source_url is None or draft.source_url.startswith(("http://", "https://"))
            for draft in drafts
        )

    def test_raw_span_budget(self, case):
        drafts = case.adapter.parse_response(deepcopy(case.raw), case.query)
        assert all(len(draft.raw_span) <= 500 for draft in drafts)
        assert PROVIDER_SOURCE_TYPE[case.adapter.name] in raw_span_metrics(
            CONTRACT_ADAPTER_CASES
        )

    def test_normalized_value_coverage(self, case):
        coverage = normalized_coverage_by_source_type(CONTRACT_ADAPTER_CASES)
        if PROVIDER_SOURCE_TYPE[case.adapter.name] in {"dart", "quote"}:
            assert coverage[PROVIDER_SOURCE_TYPE[case.adapter.name]] >= 0.90

    def test_span_scope_declared(self, case):
        expected = expectations_by_source_ref(case.expectations)
        drafts = case.adapter.parse_response(deepcopy(case.raw), case.query)
        actual = {draft.source_ref: draft.span_scope for draft in drafts}
        assert actual == expected

    def test_error_classification(self, case):
        registry_kind = case.case_id.split("-", 1)[0]
        provider_cases = [
            error_case
            for error_case in CONTRACT_ERROR_CASES
            if error_case.case_id.startswith(
                f"{registry_kind}-{case.adapter.name}-"
            )
        ]
        assert len(provider_cases) == 5
        for error_case in provider_cases:
            assert case.adapter.classify_error(deepcopy(error_case.raw)) == (
                error_case.expected_reason_code,
                error_case.expected_retryable,
            )
            first = case.adapter.rate_limit_hint(deepcopy(error_case.raw))
            second = case.adapter.rate_limit_hint(deepcopy(error_case.raw))
            assert first == second
            if first is not None:
                assert first.provider == case.adapter.name
                assert first.source in {"header", "body_message", "status_only"}
                assert all(
                    value is None or value >= 0
                    for value in (first.retry_after_ms, first.remaining, first.window_s)
                )
            if error_case.hint_required:
                assert first is not None

    def test_no_llm_import(self, case):
        assert_no_forbidden_imports(case.adapter)

    def test_deterministic(self, case):
        raw = deepcopy(case.raw)
        first = case.adapter.parse_response(deepcopy(raw), case.query)
        second = case.adapter.parse_response(deepcopy(raw), case.query)
        assert [item.model_dump(mode="json") for item in first] == [
            item.model_dump(mode="json") for item in second
        ]
        assert case.raw == raw

    def test_no_secret_in_fixture(self, case):
        assert_no_fixture_secrets(case.fixture_paths)
