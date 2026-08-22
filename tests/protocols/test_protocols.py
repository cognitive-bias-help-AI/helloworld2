"""P0-3 팀원 병렬개발용 Protocol exact signature 회귀."""

from __future__ import annotations

import inspect
from datetime import datetime
from typing import Literal, get_type_hints

import pytest
from pydantic import BaseModel

from app.domain.ask_history import AskRecord
from app.domain.resume_source import ResumeSemanticSource
from app.domain.slot_context import SlotValueObservation
from app.gateway.protocols import ProviderAdapter, ReplayCache
from app.models.protocols import ModelGateway
from app.schemas.frozen import (
    Claim,
    ClaimEvaluation,
    ClaimEvidence,
    Evidence,
    EvidenceDraft,
    EvidenceQueryLink,
    Finding,
    ProviderCall,
    Query,
    RateLimitHint,
    ReasonCode,
    Request,
    Usage,
)
from app.store.protocols import EvidenceStore, ReviewStore


def method_parameters(method) -> list[str]:
    return list(inspect.signature(method).parameters)


def test_Protocol_5종은_구현체가_아니라_runtime_checkable_인터페이스다():
    for protocol in (EvidenceStore, ReviewStore, ProviderAdapter, ReplayCache, ModelGateway):
        assert protocol._is_protocol
        assert protocol._is_runtime_protocol
        with pytest.raises(TypeError):
            protocol()


def test_EvidenceStore는_acquisition의_12개_async_메서드와_domain_type을_고정한다():
    expected_parameters = {
        "put_queries": ["self", "run_id", "queries"],
        "get_queries": ["self", "query_ids"],
        "put_provider_calls": ["self", "run_id", "calls"],
        "get_provider_calls": ["self", "provider_request_ids"],
        "provider_calls_for_query": ["self", "query_id"],
        "put_many": ["self", "run_id", "evs"],
        "get_many": ["self", "ids"],
        "find_by_sha256": ["self", "run_id", "hashes"],
        "link": ["self", "pairs"],
        "put_evidence_batch": ["self", "run_id", "evidence", "links"],
        "evidence_ids_for_claim": ["self", "claim_id"],
        "evidence_ids_for_queries": ["self", "query_ids"],
    }
    assert {name for name in EvidenceStore.__dict__ if not name.startswith("_")} == set(
        expected_parameters
    )
    for name, parameters in expected_parameters.items():
        method = getattr(EvidenceStore, name)
        assert inspect.iscoroutinefunction(method)
        assert method_parameters(method) == parameters

    assert get_type_hints(EvidenceStore.put_queries) == {
        "run_id": str,
        "queries": list[Query],
        "return": list[str],
    }
    assert get_type_hints(EvidenceStore.get_queries)["return"] == list[Query]
    assert get_type_hints(EvidenceStore.put_provider_calls) == {
        "run_id": str,
        "calls": list[ProviderCall],
        "return": list[str],
    }
    assert get_type_hints(EvidenceStore.get_provider_calls)["return"] == list[ProviderCall]
    assert get_type_hints(EvidenceStore.provider_calls_for_query)["return"] == list[
        ProviderCall
    ]
    assert get_type_hints(EvidenceStore.put_many) == {
        "run_id": str,
        "evs": list[Evidence],
        "return": list[str],
    }
    assert get_type_hints(EvidenceStore.get_many)["return"] == list[Evidence]
    assert get_type_hints(EvidenceStore.find_by_sha256)["return"] == dict[str, str]
    assert get_type_hints(EvidenceStore.link)["pairs"] == list[EvidenceQueryLink]
    assert get_type_hints(EvidenceStore.put_evidence_batch) == {
        "run_id": str,
        "evidence": list[Evidence],
        "links": list[EvidenceQueryLink],
        "return": list[str],
    }


def test_ReviewStore는_판단_본문_7영역의_14개_async_메서드를_고정한다():
    expected = {
        "put_input": ["self", "run_id", "body"],
        "get_input": ["self", "input_id"],
        "put_claims": ["self", "run_id", "items"],
        "get_claims": ["self", "claim_ids"],
        "put_claim_evidence": ["self", "run_id", "items"],
        "get_claim_evidence": ["self", "run_id", "claim_id"],
        "put_claim_evaluations": ["self", "run_id", "items"],
        "get_claim_evaluations": ["self", "ids"],
        "put_findings": ["self", "run_id", "items"],
        "get_findings": ["self", "ids"],
        "put_report": ["self", "run_id", "body"],
        "get_report": ["self", "report_id"],
        "put_slot_observations": ["self", "run_id", "items"],
        "get_slot_observations": ["self", "run_id"],
        "put_semantic_batch": ["self", "run_id", "observations", "claims"],
        "put_resume_sources": ["self", "run_id", "items"],
        "get_resume_sources": ["self", "run_id"],
        "put_ask_records": ["self", "run_id", "items"],
        "get_ask_records": ["self", "run_id"],
    }
    assert {name for name in ReviewStore.__dict__ if not name.startswith("_")} == set(expected)
    for name, parameters in expected.items():
        method = getattr(ReviewStore, name)
        assert inspect.iscoroutinefunction(method)
        assert method_parameters(method) == parameters

    assert get_type_hints(ReviewStore.put_claims)["items"] == list[Claim]
    assert get_type_hints(ReviewStore.get_claims)["return"] == list[Claim]
    assert get_type_hints(ReviewStore.put_claim_evidence)["items"] == list[ClaimEvidence]
    assert get_type_hints(ReviewStore.get_claim_evidence)["return"] == list[ClaimEvidence]
    assert get_type_hints(ReviewStore.put_claim_evaluations)["items"] == list[ClaimEvaluation]
    assert get_type_hints(ReviewStore.get_claim_evaluations)["return"] == list[ClaimEvaluation]
    assert get_type_hints(ReviewStore.put_findings)["items"] == list[Finding]
    assert get_type_hints(ReviewStore.get_findings)["return"] == list[Finding]
    assert get_type_hints(ReviewStore.put_slot_observations)["items"] == list[
        SlotValueObservation
    ]
    assert get_type_hints(ReviewStore.get_slot_observations)["return"] == list[
        SlotValueObservation
    ]
    assert get_type_hints(ReviewStore.put_semantic_batch) == {
        "run_id": str,
        "observations": list[SlotValueObservation],
        "claims": list[Claim],
        "return": tuple[list[str], list[str]],
    }
    assert get_type_hints(ReviewStore.put_resume_sources)["items"] == list[
        ResumeSemanticSource
    ]
    assert get_type_hints(ReviewStore.get_resume_sources)["return"] == list[
        ResumeSemanticSource
    ]
    assert get_type_hints(ReviewStore.put_ask_records)["items"] == list[AskRecord]
    assert get_type_hints(ReviewStore.get_ask_records)["return"] == list[AskRecord]


def test_ProviderAdapter는_Evidence가_아닌_EvidenceDraft_경계를_고정한다():
    expected = {
        "build_request": ["self", "q", "as_of"],
        "acall": ["self", "req"],
        "parse_response": ["self", "raw", "q"],
        "classify_error": ["self", "raw"],
        "rate_limit_hint": ["self", "raw"],
    }
    assert get_type_hints(ProviderAdapter) == {
        "name": Literal["dart", "naver", "kiwoom"],
        "max_concurrency": int,
    }
    for name, parameters in expected.items():
        assert method_parameters(getattr(ProviderAdapter, name)) == parameters

    assert get_type_hints(ProviderAdapter.build_request) == {
        "q": Query,
        "as_of": datetime,
        "return": Request,
    }
    assert inspect.iscoroutinefunction(ProviderAdapter.acall)
    assert get_type_hints(ProviderAdapter.parse_response)["return"] == list[EvidenceDraft]
    assert get_type_hints(ProviderAdapter.classify_error)["return"] == tuple[ReasonCode, bool]
    assert get_type_hints(ProviderAdapter.rate_limit_hint)["return"] == RateLimitHint | None


def test_ReplayCache는_결정론적_key와_raw_response_4메서드를_고정한다():
    expected = {
        "make_key": ["self", "provider", "endpoint", "params", "as_of"],
        "get": ["self", "key"],
        "put": ["self", "key", "raw", "ttl_s"],
        "record": ["self", "key", "raw"],
    }
    for name, parameters in expected.items():
        assert method_parameters(getattr(ReplayCache, name)) == parameters
    assert not inspect.iscoroutinefunction(ReplayCache.make_key)
    assert all(
        inspect.iscoroutinefunction(getattr(ReplayCache, name))
        for name in ("get", "put", "record")
    )
    assert get_type_hints(ReplayCache.make_key)["return"] is str
    assert get_type_hints(ReplayCache.get)["return"] == dict | None


def test_ModelGateway는_dict가_아닌_BaseModel_View와_Usage를_고정한다():
    assert method_parameters(ModelGateway.invoke) == [
        "self",
        "slot",
        "prompt_version",
        "input_view",
        "output_schema",
    ]
    assert inspect.iscoroutinefunction(ModelGateway.invoke)
    hints = get_type_hints(ModelGateway.invoke)
    assert hints == {
        "slot": Literal["SMALL", "MID", "LARGE"],
        "prompt_version": str,
        "input_view": BaseModel,
        "output_schema": type[BaseModel],
        "return": tuple[BaseModel, Usage],
    }
