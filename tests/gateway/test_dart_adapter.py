from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.gateway.adapters.dart import (
    DartAdapter,
    indicator_to_evidence_draft,
    record_to_evidence_draft,
)
from app.gateway.adapters.dart_corp_code import DartCorpCodeResolver
from app.gateway.assemble import assemble_evidence
from app.gateway.execution import ProviderExecutionError
from app.schemas.frozen import ProviderCall, Query, ReasonCode
from app.store.memory_evidence_store import MemoryEvidenceStore
from providers.dart.disclosure import parse_disclosure_list
from providers.dart.financial import parse_financial_statement
from providers.dart.indicator import parse_financial_indicators
from providers.dart.models import (
    DartDisclosureRecord,
    DartFinancialIndicatorRecord,
    DartFinancialRecord,
)

NOW = datetime(2026, 3, 31, tzinfo=UTC)
QUERY_ID = "01K5ZTQ9X7WPCVN2M4H8JRAB1D"
CALL_ID = "01K5ZTQ9X7WPCVN2M4H8JRAB2D"


def query() -> Query:
    return Query(
        query_id=QUERY_ID,
        scope="stock",
        intent="context",
        provider="dart",
        endpoint="financial_statement",
        params={
            "stock_code": "005930",
            "bsns_year": "2025",
            "reprt_code": "11011",
            "fs_div": "CFS",
            "account_names": ["영업이익"],
        },
        created_at=NOW,
    )


def success_response() -> dict:
    return {
        "status": "000",
        "message": "정상",
        "list": [
            {
                "rcept_no": "20260331001234",
                "corp_name": "삼성전자",
                "sj_div": "IS",
                "sj_nm": "손익계산서",
                "account_id": "dart_OperatingIncomeLoss",
                "account_nm": "영업이익",
                "thstrm_nm": "제57기",
                "thstrm_amount": "9,178,955,000,000",
                "currency": "KRW",
            },
            {
                "rcept_no": "20260331001234",
                "corp_name": "삼성전자",
                "sj_div": "BS",
                "sj_nm": "재무상태표",
                "account_id": "dart_Assets",
                "account_nm": "자산총계",
                "thstrm_nm": "제57기",
                "thstrm_amount": "-120,000",
                "currency": "KRW",
            },
        ],
    }


def disclosure_query(**changes) -> Query:
    params = {
        "stock_code": "005930",
        "bgn_de": "20260801",
        "end_de": "20260820",
        "last_reprt_at": "Y",
        "pblntf_ty": "B",
        "page_no": 1,
        "page_count": 20,
    }
    params.update(changes)
    return Query(
        query_id=QUERY_ID,
        scope="stock",
        intent="context",
        provider="dart",
        endpoint="disclosure_list",
        params=params,
        created_at=NOW,
    )


def disclosure_response() -> dict:
    return {
        "status": "000",
        "message": "정상",
        "page_no": 1,
        "page_count": 20,
        "total_count": 1,
        "total_page": 1,
        "list": [
            {
                "corp_cls": "Y",
                "corp_name": "삼성전자",
                "corp_code": "00126380",
                "stock_code": "005930",
                "report_nm": "단일판매ㆍ공급계약체결",
                "rcept_no": "20260820001234",
                "flr_nm": "삼성전자",
                "rcept_dt": "20260820",
                "rm": "유",
            }
        ],
    }


def indicator_query(**changes) -> Query:
    params = {
        "stock_code": "005930",
        "bsns_year": "2025",
        "reprt_code": "11011",
        "indicator_family": "profitability",
    }
    params.update(changes)
    return Query(
        query_id=QUERY_ID,
        scope="stock",
        intent="context",
        provider="dart",
        endpoint="financial_indicator",
        params=params,
        created_at=NOW,
    )


def indicator_response() -> dict:
    return {
        "status": "000",
        "message": "정상",
        "list": [
            {
                "reprt_code": "11011",
                "bsns_year": "2025",
                "corp_code": "00126380",
                "stock_code": "005930",
                "stlm_dt": "20251231",
                "idx_cl_code": "M210000",
                "idx_cl_nm": "수익성지표",
                "idx_code": "M211000",
                "idx_nm": "영업이익률",
                "idx_val": "12.3400",
            }
        ],
    }


def test_stock_code는_loaded_mapping의_corp_code로만_resolve한다():
    resolver = DartCorpCodeResolver({"005930": "00126380"})

    assert resolver.resolve("005930") == "00126380"
    with pytest.raises(ValueError, match="unknown DART stock_code"):
        resolver.resolve("000000")


@pytest.mark.asyncio
async def test_build_request는_secret을_제외하고_acall에서만_api_key를_추가한다():
    observed = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.update(dict(request.url.params))
        return httpx.Response(200, json=success_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DartAdapter(
            "secret-key",
            DartCorpCodeResolver({"005930": "00126380"}),
            client=client,
        )
        request = adapter.build_request(query(), NOW)
        assert "crtfc_key" not in request.params
        raw = await adapter.acall(request)

    assert raw["status"] == "000"
    assert observed == {
        "corp_code": "00126380",
        "bsns_year": "2025",
        "reprt_code": "11011",
        "fs_div": "CFS",
        "crtfc_key": "secret-key",
    }


@pytest.mark.asyncio
async def test_disclosure_list는_한_page만_list_json으로_호출한다():
    observed = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url.copy_with(query=None))
        observed["params"] = dict(request.url.params)
        return httpx.Response(200, json=disclosure_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DartAdapter(
            "secret-key", DartCorpCodeResolver({"005930": "00126380"}), client=client
        )
        request = adapter.build_request(disclosure_query(), NOW)
        assert "crtfc_key" not in request.params
        await adapter.acall(request)

    assert observed == {
        "url": "https://opendart.fss.or.kr/api/list.json",
        "params": {
            "corp_code": "00126380",
            "bgn_de": "20260801",
            "end_de": "20260820",
            "last_reprt_at": "Y",
            "pblntf_ty": "B",
            "page_no": "1",
            "page_count": "20",
            "crtfc_key": "secret-key",
        },
    }


def test_financial_statement는_요청한_계정만_EvidenceDraft로_parse한다():
    adapter = DartAdapter("secret-key", DartCorpCodeResolver({"005930": "00126380"}))

    drafts = adapter.parse_response(success_response(), query())

    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.source_ref == "20260331001234:IS:dart_OperatingIncomeLoss:CFS"
    assert draft.publisher == "삼성전자"
    assert draft.published_at is None
    assert draft.raw_span == "2025 사업보고서 연결 손익계산서 영업이익: 당기 9,178,955,000,000 KRW / 전기 값 없음 KRW"
    assert draft.normalized_value == {
        "kind": "financial_statement",
        "account_id": "dart_OperatingIncomeLoss",
        "account_name": "영업이익",
        "value": 9178955000000,
        "current_value": 9178955000000,
        "prior_value": None,
        "current_period_value": 9178955000000,
        "prior_period_value": None,
        "current_cumulative_value": None,
        "prior_cumulative_value": None,
        "prior_comparable_value": None,
        "comparison_basis": "ANNUAL",
        "comparison_available": False,
        "change_direction": None,
        "unit": "KRW",
        "business_year": "2025",
        "report_code": "11011",
        "fs_div": "CFS",
        "statement_code": "IS",
        "statement_name": "손익계산서",
    }


def test_financial_statement는_당기와전기_비교값을_보존한다():
    raw = success_response()
    raw["list"][0].update({
        "frmtrm_amount": "8,000,000,000,000",
        "thstrm_add_amount": "9,178,955,000,000",
        "frmtrm_add_amount": "8,000,000,000,000",
    })
    record = parse_financial_statement(
        raw,
        corp_code="00126380",
        business_year="2025",
        report_code="11011",
        fs_div="CFS",
        account_names=("영업이익",),
    )[0]
    assert record.current_amount == 9178955000000
    assert record.prior_amount == 8000000000000
    assert record.current_cumulative_amount == 9178955000000
    assert record.prior_comparable_amount == 8000000000000


def interim_response(*, statement_code="IS") -> dict:
    raw = deepcopy(success_response())
    row = raw["list"][0]
    row.update(
        {
            "sj_div": statement_code,
            "thstrm_amount": "30",
            "frmtrm_amount": "100",
            "frmtrm_q_amount": "20",
            "thstrm_add_amount": "90",
            "frmtrm_add_amount": "80",
        }
    )
    return raw


def test_financial_statement는_분기_동기기간을_명시하면_frmtrm_q_amount와_비교한다():
    record = parse_financial_statement(
        interim_response(),
        corp_code="00126380", business_year="2025", report_code="11014", fs_div="CFS",
        account_names=("영업이익",), comparison_basis="INTERIM_PERIOD",
    )[0]
    draft = record_to_evidence_draft(record)
    assert record.prior_period_amount == 20
    assert draft.normalized_value["current_value"] == 30
    assert draft.normalized_value["prior_value"] == 20
    assert draft.normalized_value["comparison_basis"] == "INTERIM_PERIOD"
    assert draft.normalized_value["change_direction"] == "increase"


def test_financial_statement는_분기_동기기간이_없으면_전기값으로_대체하지_않는다():
    raw = interim_response()
    raw["list"][0].pop("frmtrm_q_amount")
    record = parse_financial_statement(
        raw,
        corp_code="00126380", business_year="2025", report_code="11014", fs_div="CFS",
        account_names=("영업이익",), comparison_basis="INTERIM_PERIOD",
    )[0]
    normalized = record_to_evidence_draft(record).normalized_value
    assert normalized["comparison_available"] is False
    assert normalized["change_direction"] is None
    assert normalized["prior_value"] is None


def test_financial_statement는_누적기간은_누적필드끼리만_비교한다():
    raw = interim_response()
    raw["list"][0]["frmtrm_add_amount"] = "100"
    record = parse_financial_statement(
        raw,
        corp_code="00126380", business_year="2025", report_code="11014", fs_div="CFS",
        account_names=("영업이익",), comparison_basis="INTERIM_CUMULATIVE",
    )[0]
    normalized = record_to_evidence_draft(record).normalized_value
    assert normalized["current_value"] == 90
    assert normalized["prior_value"] == 100
    assert normalized["comparison_basis"] == "INTERIM_CUMULATIVE"
    assert normalized["change_direction"] == "decrease"


def test_financial_statement는_누적_이전값이_없으면_분기값으로_대체하지_않는다():
    raw = interim_response()
    raw["list"][0].pop("frmtrm_add_amount")
    record = parse_financial_statement(
        raw,
        corp_code="00126380", business_year="2025", report_code="11014", fs_div="CFS",
        account_names=("영업이익",), comparison_basis="INTERIM_CUMULATIVE",
    )[0]
    normalized = record_to_evidence_draft(record).normalized_value
    assert normalized["comparison_available"] is False
    assert normalized["change_direction"] is None


def test_financial_statement는_분기_비교기준이_없으면_fail_closed한다():
    record = parse_financial_statement(
        interim_response(),
        corp_code="00126380", business_year="2025", report_code="11014", fs_div="CFS",
        account_names=("영업이익",),
    )[0]
    normalized = record_to_evidence_draft(record).normalized_value
    assert normalized["comparison_basis"] == "NOT_COMPARABLE"
    assert normalized["comparison_available"] is False
    assert normalized["change_direction"] is None


def test_financial_statement는_BS에_분기_IS_비교규칙을_적용하지_않는다():
    raw = interim_response(statement_code="BS")
    record = parse_financial_statement(
        raw,
        corp_code="00126380", business_year="2025", report_code="11014", fs_div="CFS",
        account_names=("영업이익",), comparison_basis="INTERIM_PERIOD",
    )[0]
    normalized = record_to_evidence_draft(record).normalized_value
    assert normalized["comparison_basis"] == "NOT_COMPARABLE"
    assert normalized["comparison_available"] is False


def test_financial_statement는_frmtrm_q_amount를_엄격하게_파싱한다():
    raw = interim_response()
    raw["list"][0]["frmtrm_q_amount"] = "not-a-number"
    with pytest.raises(ValueError, match="invalid DART amount"):
        parse_financial_statement(
            raw,
            corp_code="00126380", business_year="2025", report_code="11014", fs_div="CFS",
            account_names=("영업이익",), comparison_basis="INTERIM_PERIOD",
        )


def test_financial_statement의_비교값은_음수와_누락을_그대로_보존한다():
    raw = success_response()
    raw["list"][0].update({"thstrm_amount": "-10", "frmtrm_amount": ""})
    record = parse_financial_statement(
        raw,
        corp_code="00126380",
        business_year="2025",
        report_code="11011",
        fs_div="CFS",
        account_names=("영업이익",),
    )[0]
    assert record.current_amount == -10
    assert record.prior_amount is None


def test_financial_statement의_비교값이_비정상이면_fail_closed한다():
    raw = success_response()
    raw["list"][0]["frmtrm_amount"] = "not-a-number"
    with pytest.raises(ValueError, match="invalid DART amount"):
        parse_financial_statement(
            raw,
            corp_code="00126380",
            business_year="2025",
            report_code="11011",
            fs_div="CFS",
            account_names=("영업이익",),
        )


def test_financial_statement는_명시된_account_id와_name만_채택한다():
    raw = success_response()
    raw["list"].append({
        "rcept_no": "20260331001234",
        "corp_name": "삼성전자",
        "sj_div": "IS",
        "sj_nm": "손익계산서",
        "account_id": "dart_Unknown",
        "account_nm": "조정 EBITDA",
        "thstrm_amount": "999",
    })
    records = parse_financial_statement(
        raw,
        corp_code="00126380",
        business_year="2025",
        report_code="11011",
        fs_div="CFS",
        account_names=("영업이익",),
    )
    assert [record.account_name for record in records] == ["영업이익"]


def test_financial_statement는_OpenDART에_corp_name이_없어도_행을_보존한다():
    raw = success_response()
    raw["list"][0].pop("corp_name")
    record = parse_financial_statement(
        raw,
        corp_code="00126380",
        business_year="2025",
        report_code="11011",
        fs_div="CFS",
        account_names=("영업이익",),
    )[0]
    assert record.corp_name is None


def test_financial_statement는_허용된_account_id로도_행을_채택한다():
    raw = success_response()
    records = parse_financial_statement(
        raw,
        corp_code="00126380",
        business_year="2025",
        report_code="11011",
        fs_div="CFS",
        account_names=("dart_OperatingIncomeLoss",),
    )
    assert [record.account_name for record in records] == ["영업이익"]


def test_financial_statement는_canonical_concept로_공식_account_id와_영업손익을_채택한다():
    raw = success_response()
    raw["list"][0].update({"account_nm": "영업손익"})
    records = parse_financial_statement(
        raw,
        corp_code="00126380",
        business_year="2025",
        report_code="11011",
        fs_div="CFS",
        account_names=("영업이익",),
    )
    assert [record.account_name for record in records] == ["영업손익"]


def test_financial_statement는_알려지지_않은_account_id를_concept로_추론하지_않는다():
    raw = success_response()
    raw["list"][0].update({"account_nm": "영업손익", "account_id": "dart_Unknown"})
    records = parse_financial_statement(
        raw,
        corp_code="00126380",
        business_year="2025",
        report_code="11011",
        fs_div="CFS",
        account_names=("영업이익",),
    )
    assert records == []


def test_financial_evidence는_당기전기와_결정적방향을_정규화한다():
    raw = success_response()
    raw["list"][0]["frmtrm_amount"] = "8,000,000,000,000"
    record = parse_financial_statement(
        raw,
        corp_code="00126380",
        business_year="2025",
        report_code="11011",
        fs_div="CFS",
        account_names=("영업이익",),
    )[0]
    normalized = record_to_evidence_draft(record).normalized_value
    assert normalized["current_value"] == 9178955000000
    assert normalized["prior_value"] == 8000000000000
    assert normalized["comparison_available"] is True
    assert normalized["change_direction"] == "increase"


@pytest.mark.parametrize(
    ("current", "prior", "direction"),
    [("100", "120", "decrease"), ("100", "100", "unchanged")],
)
def test_financial_statement_annual_change_direction_is_deterministic(current, prior, direction):
    raw = success_response()
    raw["list"][0].update({"thstrm_amount": current, "frmtrm_amount": prior})
    record = parse_financial_statement(
        raw,
        corp_code="00126380", business_year="2025", report_code="11011", fs_div="CFS",
        account_names=("영업이익",),
    )[0]
    normalized = record_to_evidence_draft(record).normalized_value
    assert normalized["comparison_available"] is True
    assert normalized["change_direction"] == direction


def test_financial_statement_annual_missing_prior_is_unavailable():
    raw = success_response()
    raw["list"][0]["frmtrm_amount"] = ""
    record = parse_financial_statement(
        raw,
        corp_code="00126380", business_year="2025", report_code="11011", fs_div="CFS",
        account_names=("영업이익",),
    )[0]
    normalized = record_to_evidence_draft(record).normalized_value
    assert normalized["comparison_available"] is False
    assert normalized["change_direction"] is None


def test_DART_Core는_raw를_app_type없이_DartFinancialRecord로_parse한다():
    records = parse_financial_statement(
        success_response(),
        corp_code="00126380",
        business_year="2025",
        report_code="11011",
        fs_div="CFS",
        account_names=("영업이익",),
    )

    assert records == [
        DartFinancialRecord(
            corp_code="00126380",
            corp_name="삼성전자",
            receipt_no="20260331001234",
            account_id="dart_OperatingIncomeLoss",
            account_name="영업이익",
            statement_code="IS",
            statement_name="손익계산서",
            amount=9178955000000,
            currency="KRW",
            business_year="2025",
            report_code="11011",
            fs_div="CFS",
            current_amount=9178955000000,
        )
    ]


def test_Main_Bridge는_DartFinancialRecord만_EvidenceDraft로_mapping한다():
    record = DartFinancialRecord(
        corp_code="00126380",
        corp_name="삼성전자",
        receipt_no="20260331001234",
        account_id="dart_OperatingIncomeLoss",
        account_name="영업이익",
        statement_code="IS",
        statement_name="손익계산서",
        amount=9178955000000,
        currency="KRW",
        business_year="2025",
        report_code="11011",
        fs_div="CFS",
    )

    draft = record_to_evidence_draft(record)

    assert draft.source_type == "dart"
    assert draft.source_ref == "20260331001234:IS:dart_OperatingIncomeLoss:CFS"
    assert draft.raw_span == "2025 사업보고서 연결 손익계산서 영업이익: 당기 9,178,955,000,000 KRW / 전기 값 없음 KRW"


def test_DART_Core는_disclosure_raw를_DartDisclosureRecord로_parse한다():
    assert parse_disclosure_list(disclosure_response()) == [
        DartDisclosureRecord(
            corp_code="00126380",
            corp_name="삼성전자",
            stock_code="005930",
            corp_class="Y",
            report_name="단일판매ㆍ공급계약체결",
            receipt_no="20260820001234",
            receipt_date="20260820",
            submitter="삼성전자",
            remark="유",
        )
    ]


def test_Main_Adapter는_disclosure_record를_metadata_EvidenceDraft로_mapping한다():
    adapter = DartAdapter("secret-key", DartCorpCodeResolver({"005930": "00126380"}))

    drafts = adapter.parse_response(disclosure_response(), disclosure_query())

    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.source_ref == "20260820001234"
    assert draft.publisher == "삼성전자"
    assert draft.source_url == (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260820001234"
    )
    assert draft.published_at == datetime(2026, 8, 20, tzinfo=UTC)
    assert draft.raw_span == "2026-08-20 삼성전자 '단일판매ㆍ공급계약체결' 공시 제출"
    assert draft.normalized_value == {
        "kind": "disclosure",
        "report_name": "단일판매ㆍ공급계약체결",
        "receipt_no": "20260820001234",
        "receipt_date": "20260820",
        "submitter": "삼성전자",
        "corp_class": "Y",
        "remark": "유",
    }


def test_disclosure_page_count_101은_외부호출전에_fail_closed한다():
    adapter = DartAdapter("secret-key", DartCorpCodeResolver({"005930": "00126380"}))

    with pytest.raises(ValueError, match="page_count"):
        adapter.build_request(disclosure_query(page_count=101), NOW)


@pytest.mark.asyncio
async def test_financial_indicator는_semantic_family를_DART_code로_mapping한다():
    observed = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.update(dict(request.url.params))
        return httpx.Response(200, json=indicator_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DartAdapter(
            "secret-key",
            DartCorpCodeResolver({"005930": "00126380"}),
            client=client,
        )
        request = adapter.build_request(indicator_query(), NOW)
        assert request.params["idx_cl_code"] == "M210000"
        assert "crtfc_key" not in request.params
        await adapter.acall(request)

    assert observed == {
        "corp_code": "00126380",
        "bsns_year": "2025",
        "reprt_code": "11011",
        "idx_cl_code": "M210000",
        "crtfc_key": "secret-key",
    }


@pytest.mark.parametrize(
    ("family", "code"),
    [
        ("profitability", "M210000"),
        ("stability", "M220000"),
        ("growth", "M230000"),
        ("activity", "M240000"),
    ],
)
def test_financial_indicator_family_registry는_deterministic하다(family, code):
    adapter = DartAdapter("secret-key", DartCorpCodeResolver({"005930": "00126380"}))

    request = adapter.build_request(indicator_query(indicator_family=family), NOW)

    assert request.params["idx_cl_code"] == code


def test_financial_indicator는_Core_record와_EvidenceDraft를_보존한다():
    records = parse_financial_indicators(indicator_response())
    assert records == [
        DartFinancialIndicatorRecord(
            corp_code="00126380",
            stock_code="005930",
            business_year="2025",
            report_code="11011",
            settlement_date="20251231",
            indicator_class_code="M210000",
            indicator_class_name="수익성지표",
            indicator_code="M211000",
            indicator_name="영업이익률",
            indicator_value_raw="12.3400",
            indicator_value=Decimal("12.3400"),
        )
    ]

    draft = indicator_to_evidence_draft(records[0])

    assert draft.source_type == "dart"
    assert draft.source_ref == "indicator:00126380:2025:11011:M210000:M211000"
    assert draft.normalized_value == {
        "kind": "financial_indicator",
        "stock_code": "005930",
        "business_year": "2025",
        "report_code": "11011",
        "settlement_date": "20251231",
        "indicator_class_code": "M210000",
        "indicator_class_name": "수익성지표",
        "indicator_code": "M211000",
        "indicator_name": "영업이익률",
        "value_raw": "12.3400",
        "value": "12.3400",
    }


def test_financial_indicator_unknown_family는_외부호출전에_fail_closed한다():
    adapter = DartAdapter("secret-key", DartCorpCodeResolver({"005930": "00126380"}))

    with pytest.raises(ValueError, match="indicator_family"):
        adapter.build_request(indicator_query(indicator_family="valuation"), NOW)


@pytest.mark.parametrize(
    ("status", "reason", "retryable"),
    [
        ("010", ReasonCode.AUTH_FAILED, False),
        ("013", ReasonCode.NO_RESULT, False),
        ("020", ReasonCode.RATE_LIMIT, True),
    ],
)
def test_DART_application_status를_대표_ReasonCode로_mapping한다(
    status, reason, retryable
):
    adapter = DartAdapter("secret-key", DartCorpCodeResolver({"005930": "00126380"}))

    assert adapter.classify_error({"status": status}) == (reason, retryable)


@pytest.mark.asyncio
async def test_DART_draft는_기존_assembler를_통해_canonical_Evidence가_된다():
    adapter = DartAdapter("secret-key", DartCorpCodeResolver({"005930": "00126380"}))
    q = query()
    store = MemoryEvidenceStore()
    await store.put_queries("run-dart", [q])
    call = ProviderCall(
        provider_request_id=CALL_ID,
        run_id="run-dart",
        provider="dart",
        endpoint=q.endpoint,
        query_id=q.query_id,
        latency_ms=1,
        idempotency_key="a" * 64,
        created_at=NOW,
    )
    await store.put_provider_calls("run-dart", [call])

    evidence, duplicates = await assemble_evidence(
        adapter.parse_response(success_response(), q),
        q,
        call,
        NOW,
        "run-dart",
        NOW,
        store,
    )

    assert duplicates == 0
    assert len(evidence) == 1
    assert evidence[0].source_type == "dart"
    assert evidence[0].provider_request_id == CALL_ID
    assert await store.evidence_ids_for_queries([QUERY_ID]) == [evidence[0].evidence_id]


class TimeoutDartClient:
    async def financial_statement(self, **kwargs):
        del kwargs
        raise httpx.ReadTimeout("timed out")


@pytest.mark.asyncio
async def test_DART_timeout은_typed_execution_error로_normalize된다():
    adapter = DartAdapter("secret-key", DartCorpCodeResolver({"005930": "00126380"}))
    adapter._client = TimeoutDartClient()

    with pytest.raises(ProviderExecutionError) as caught:
        await adapter.acall(adapter.build_request(query(), NOW))

    assert caught.value.reason_code is ReasonCode.UPSTREAM_TIMEOUT
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_DART_network_failure는_typed_execution_error로_normalize된다():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DartAdapter(
            "secret-key",
            DartCorpCodeResolver({"005930": "00126380"}),
            client=client,
        )

        with pytest.raises(ProviderExecutionError) as caught:
            await adapter.acall(adapter.build_request(query(), NOW))

    assert caught.value.reason_code is ReasonCode.UPSTREAM_TIMEOUT
    assert caught.value.retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "reason", "retryable"),
    [
        (400, ReasonCode.SCHEMA_INVALID, False),
        (401, ReasonCode.AUTH_FAILED, False),
        (408, ReasonCode.UPSTREAM_TIMEOUT, True),
        (429, ReasonCode.RATE_LIMIT, True),
        (500, ReasonCode.UPSTREAM_5XX, True),
    ],
)
async def test_DART_HTTP_failure는_status별_typed_execution_error로_normalize된다(
    status, reason, retryable
):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, request=request, json={"status": "800"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DartAdapter(
            "secret-key",
            DartCorpCodeResolver({"005930": "00126380"}),
            client=client,
        )

        with pytest.raises(ProviderExecutionError) as caught:
            await adapter.acall(adapter.build_request(query(), NOW))

    assert caught.value.reason_code is reason
    assert caught.value.retryable is retryable
    assert caught.value.http_status == status
