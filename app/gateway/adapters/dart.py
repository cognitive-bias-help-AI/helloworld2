"""Main-application bridge for the independent OpenDART core."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar, Literal

import httpx

from app.gateway.execution import ProviderExecutionError
from app.schemas.frozen import EvidenceDraft, Query, RateLimitHint, ReasonCode, Request
from providers.dart.client import (
    DISCLOSURE_LIST_URL,
    FINANCIAL_INDICATOR_URL,
    FINANCIAL_STATEMENT_URL,
    OpenDartClient,
)
from providers.dart.corp_code import DartCorpCodeResolver
from providers.dart.disclosure import parse_disclosure_list
from providers.dart.errors import DartErrorKind, classify_status
from providers.dart.financial import parse_financial_statement
from providers.dart.indicator import parse_financial_indicators
from providers.dart.models import (
    DartDisclosureRecord,
    DartFinancialIndicatorRecord,
    DartFinancialRecord,
)

_REPORT_NAMES = {
    "11013": "1분기보고서",
    "11012": "반기보고서",
    "11014": "3분기보고서",
    "11011": "사업보고서",
}
_FS_NAMES = {"CFS": "연결", "OFS": "별도"}
_INDICATOR_CLASS_CODES = {
    "profitability": "M210000",
    "stability": "M220000",
    "growth": "M230000",
    "activity": "M240000",
}
_REASON_CODES = {
    DartErrorKind.AUTH: ReasonCode.AUTH_FAILED,
    DartErrorKind.IP: ReasonCode.IP_MISMATCH,
    DartErrorKind.NO_RESULT: ReasonCode.NO_RESULT,
    DartErrorKind.RATE_LIMIT: ReasonCode.RATE_LIMIT,
    DartErrorKind.UPSTREAM: ReasonCode.UPSTREAM_5XX,
    DartErrorKind.INVALID_REQUEST: ReasonCode.SCHEMA_INVALID,
}


def record_to_evidence_draft(record: DartFinancialRecord) -> EvidenceDraft:
    current = record.current_amount if record.current_amount is not None else record.amount
    prior = record.prior_amount
    amount = "값 없음" if current is None else f"{current:,}"
    prior_text = "값 없음" if prior is None else f"{prior:,}"
    if current is None or prior is None:
        change_direction = None
    elif current > prior:
        change_direction = "increase"
    elif current < prior:
        change_direction = "decrease"
    else:
        change_direction = "unchanged"
    unit = f" {record.currency}" if record.currency else ""
    raw_span = (
        f"{record.business_year} {_REPORT_NAMES[record.report_code]} "
        f"{_FS_NAMES[record.fs_div]} {record.statement_name} "
        f"{record.account_name}: 당기 {amount}{unit} / 전기 {prior_text}{unit}"
    )
    return EvidenceDraft(
        source_type="dart",
        source_ref=(
            f"{record.receipt_no}:{record.statement_code}:"
            f"{record.account_id}:{record.fs_div}"
        ),
        source_url=(
            "https://dart.fss.or.kr/dsaf001/main.do?"
            f"rcpNo={record.receipt_no}"
        ),
        publisher=record.corp_name,
        published_at=None,
        raw_span=raw_span,
        span_scope="structured_field",
        normalized_value={
            "kind": "financial_statement",
            "account_id": record.account_id,
            "account_name": record.account_name,
            "value": record.amount,
            "current_value": current,
            "prior_value": prior,
            "current_cumulative_value": record.current_cumulative_amount,
            "prior_comparable_value": record.prior_comparable_amount,
            "comparison_available": current is not None and prior is not None,
            "change_direction": change_direction,
            "unit": record.currency,
            "business_year": record.business_year,
            "report_code": record.report_code,
            "fs_div": record.fs_div,
            "statement_code": record.statement_code,
            "statement_name": record.statement_name,
        },
    )


def disclosure_to_evidence_draft(record: DartDisclosureRecord) -> EvidenceDraft:
    receipt_date = datetime.strptime(record.receipt_date, "%Y%m%d").strftime("%Y-%m-%d")
    return EvidenceDraft(
        source_type="dart",
        source_ref=record.receipt_no,
        source_url=(
            "https://dart.fss.or.kr/dsaf001/main.do?"
            f"rcpNo={record.receipt_no}"
        ),
        publisher=record.corp_name,
        published_at=datetime.strptime(record.receipt_date, "%Y%m%d").replace(tzinfo=UTC),
        raw_span=(
            f"{receipt_date} {record.corp_name} '{record.report_name}' 공시 제출"
        ),
        span_scope="structured_field",
        normalized_value={
            "kind": "disclosure",
            "report_name": record.report_name,
            "receipt_no": record.receipt_no,
            "receipt_date": record.receipt_date,
            "submitter": record.submitter,
            "corp_class": record.corp_class,
            "remark": record.remark,
        },
    )


def indicator_to_evidence_draft(
    record: DartFinancialIndicatorRecord,
) -> EvidenceDraft:
    value = record.indicator_value_raw or "값 없음"
    return EvidenceDraft(
        source_type="dart",
        source_ref=(
            f"indicator:{record.corp_code}:{record.business_year}:"
            f"{record.report_code}:{record.indicator_class_code}:"
            f"{record.indicator_code}"
        ),
        source_url=None,
        publisher=None,
        published_at=None,
        raw_span=(
            f"{record.business_year} {_REPORT_NAMES[record.report_code]} "
            f"{record.indicator_class_name} {record.indicator_name}: {value}"
        ),
        span_scope="structured_field",
        normalized_value={
            "kind": "financial_indicator",
            "stock_code": record.stock_code,
            "business_year": record.business_year,
            "report_code": record.report_code,
            "settlement_date": record.settlement_date,
            "indicator_class_code": record.indicator_class_code,
            "indicator_class_name": record.indicator_class_name,
            "indicator_code": record.indicator_code,
            "indicator_name": record.indicator_name,
            "value_raw": record.indicator_value_raw,
            "value": (
                str(record.indicator_value)
                if record.indicator_value is not None
                else None
            ),
        },
    )


def _date_param(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
        raise ValueError(f"{name} must be YYYYMMDD")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid YYYYMMDD date") from exc
    return value


class DartAdapter:
    name: Literal["dart"] = "dart"
    max_concurrency = 3
    endpoint: ClassVar[str] = FINANCIAL_STATEMENT_URL

    def __init__(
        self,
        api_key: str,
        corp_code_resolver: DartCorpCodeResolver,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._corp_code_resolver = corp_code_resolver
        self._client = OpenDartClient(api_key, client)

    def build_request(self, q: Query, as_of: datetime) -> Request:
        del as_of
        if q.provider != self.name:
            raise ValueError("Query provider does not match DartAdapter")
        if q.endpoint == "disclosure_list":
            return self._build_disclosure_request(q)
        if q.endpoint == "financial_indicator":
            return self._build_indicator_request(q)
        if q.endpoint != "financial_statement":
            raise ValueError(f"unsupported DART endpoint: {q.endpoint}")
        params = q.params
        required = {"stock_code", "bsns_year", "reprt_code", "fs_div", "account_names"}
        if set(params) != required:
            raise ValueError("financial_statement params do not match v1 contract")
        stock_code = params["stock_code"]
        year = params["bsns_year"]
        report_code = params["reprt_code"]
        fs_div = params["fs_div"]
        account_names = params["account_names"]
        if not isinstance(stock_code, str):
            raise ValueError("stock_code must be a string")
        if not isinstance(year, str) or len(year) != 4 or not year.isdigit():
            raise ValueError("bsns_year must be four digits")
        if report_code not in _REPORT_NAMES:
            raise ValueError("unsupported reprt_code")
        if fs_div not in _FS_NAMES:
            raise ValueError("fs_div must be CFS or OFS")
        if (
            not isinstance(account_names, list)
            or not account_names
            or any(not isinstance(item, str) or not item.strip() for item in account_names)
            or len(account_names) != len(set(account_names))
        ):
            raise ValueError("account_names must be a non-empty unique string list")
        return Request(
            provider="dart",
            endpoint=FINANCIAL_STATEMENT_URL,
            method="GET",
            params={
                "corp_code": self._corp_code_resolver.resolve(stock_code),
                "bsns_year": year,
                "reprt_code": report_code,
                "fs_div": fs_div,
            },
            timeout_s=10.0,
        )

    def _build_indicator_request(self, q: Query) -> Request:
        required = {"stock_code", "bsns_year", "reprt_code", "indicator_family"}
        if set(q.params) != required:
            raise ValueError("financial_indicator params do not match v1 contract")
        stock_code = q.params["stock_code"]
        year = q.params["bsns_year"]
        report_code = q.params["reprt_code"]
        family = q.params["indicator_family"]
        if not isinstance(stock_code, str):
            raise ValueError("stock_code must be a string")
        if not isinstance(year, str) or len(year) != 4 or not year.isdigit():
            raise ValueError("bsns_year must be four digits")
        if report_code not in _REPORT_NAMES:
            raise ValueError("unsupported reprt_code")
        if family not in _INDICATOR_CLASS_CODES:
            raise ValueError("unsupported indicator_family")
        return Request(
            provider="dart",
            endpoint=FINANCIAL_INDICATOR_URL,
            method="GET",
            params={
                "corp_code": self._corp_code_resolver.resolve(stock_code),
                "bsns_year": year,
                "reprt_code": report_code,
                "idx_cl_code": _INDICATOR_CLASS_CODES[family],
            },
            timeout_s=10.0,
        )

    def _build_disclosure_request(self, q: Query) -> Request:
        allowed = {
            "stock_code",
            "bgn_de",
            "end_de",
            "last_reprt_at",
            "pblntf_ty",
            "pblntf_detail_ty",
            "sort",
            "sort_mth",
            "page_no",
            "page_count",
        }
        if not set(q.params).issubset(allowed) or "stock_code" not in q.params:
            raise ValueError("disclosure_list params do not match v1 contract")
        stock_code = q.params["stock_code"]
        if not isinstance(stock_code, str):
            raise ValueError("stock_code must be a string")
        bgn_de = _date_param(q.params.get("bgn_de"), "bgn_de")
        end_de = _date_param(q.params.get("end_de"), "end_de")
        if bgn_de is not None and end_de is not None and bgn_de > end_de:
            raise ValueError("bgn_de must not be after end_de")
        last_reprt_at = q.params.get("last_reprt_at")
        if last_reprt_at is not None and last_reprt_at not in {"Y", "N"}:
            raise ValueError("last_reprt_at must be Y or N")
        pblntf_ty = q.params.get("pblntf_ty")
        if pblntf_ty is not None and pblntf_ty not in tuple("ABCDEFGHIJ"):
            raise ValueError("pblntf_ty must be A through J")
        sort = q.params.get("sort")
        if sort is not None and sort not in {"date", "crp", "rpt"}:
            raise ValueError("sort must be date, crp, or rpt")
        sort_mth = q.params.get("sort_mth")
        if sort_mth is not None and sort_mth not in {"asc", "desc"}:
            raise ValueError("sort_mth must be asc or desc")
        page_no = q.params.get("page_no", 1)
        page_count = q.params.get("page_count", 20)
        if not isinstance(page_no, int) or isinstance(page_no, bool) or page_no < 1:
            raise ValueError("page_no must be at least 1")
        if (
            not isinstance(page_count, int)
            or isinstance(page_count, bool)
            or not 1 <= page_count <= 100
        ):
            raise ValueError("page_count must be between 1 and 100")
        params = {
            "corp_code": self._corp_code_resolver.resolve(stock_code),
            "bgn_de": bgn_de,
            "end_de": end_de,
            "last_reprt_at": last_reprt_at,
            "pblntf_ty": pblntf_ty,
            "pblntf_detail_ty": q.params.get("pblntf_detail_ty"),
            "sort": sort,
            "sort_mth": sort_mth,
            "page_no": page_no,
            "page_count": page_count,
        }
        return Request(
            provider="dart",
            endpoint=DISCLOSURE_LIST_URL,
            method="GET",
            params={key: value for key, value in params.items() if value is not None},
            timeout_s=10.0,
        )

    async def acall(self, req: Request) -> dict:
        try:
            return await self._acall(req)
        except httpx.TimeoutException as exc:
            raise ProviderExecutionError(
                reason_code=ReasonCode.UPSTREAM_TIMEOUT,
                retryable=True,
                safe_detail="DART request timed out",
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                reason_code, retryable = ReasonCode.AUTH_FAILED, False
            elif status == 408:
                reason_code, retryable = ReasonCode.UPSTREAM_TIMEOUT, True
            elif status == 429:
                reason_code, retryable = ReasonCode.RATE_LIMIT, True
            elif 500 <= status <= 599:
                reason_code, retryable = ReasonCode.UPSTREAM_5XX, True
            else:
                reason_code, retryable = ReasonCode.SCHEMA_INVALID, False
            raise ProviderExecutionError(
                reason_code=reason_code,
                retryable=retryable,
                http_status=status,
                safe_detail=f"DART request failed with HTTP {status}",
            ) from exc
        except httpx.NetworkError as exc:
            raise ProviderExecutionError(
                reason_code=ReasonCode.UPSTREAM_TIMEOUT,
                retryable=True,
                safe_detail="DART network failure",
            ) from exc

    async def _acall(self, req: Request) -> dict:
        if req.provider == self.name and req.endpoint == DISCLOSURE_LIST_URL and req.method == "GET":
            return await self._client.disclosure_list(
                corp_code=req.params["corp_code"],
                bgn_de=req.params.get("bgn_de"),
                end_de=req.params.get("end_de"),
                last_reprt_at=req.params.get("last_reprt_at"),
                pblntf_ty=req.params.get("pblntf_ty"),
                pblntf_detail_ty=req.params.get("pblntf_detail_ty"),
                sort=req.params.get("sort"),
                sort_mth=req.params.get("sort_mth"),
                page_no=req.params.get("page_no", 1),
                page_count=req.params.get("page_count", 20),
                timeout_s=req.timeout_s,
            )
        if (
            req.provider == self.name
            and req.endpoint == FINANCIAL_INDICATOR_URL
            and req.method == "GET"
        ):
            return await self._client.financial_indicator(
                corp_code=req.params["corp_code"],
                business_year=req.params["bsns_year"],
                report_code=req.params["reprt_code"],
                indicator_class_code=req.params["idx_cl_code"],
                timeout_s=req.timeout_s,
            )
        if (
            req.provider != self.name
            or req.endpoint != FINANCIAL_STATEMENT_URL
            or req.method != "GET"
        ):
            raise ValueError("Request does not belong to DartAdapter financial_statement")
        return await self._client.financial_statement(
            corp_code=req.params["corp_code"],
            business_year=req.params["bsns_year"],
            report_code=req.params["reprt_code"],
            fs_div=req.params["fs_div"],
            timeout_s=req.timeout_s,
        )

    def parse_response(self, raw: dict, q: Query) -> list[EvidenceDraft]:
        request = self.build_request(q, q.created_at)
        if q.endpoint == "disclosure_list":
            records = parse_disclosure_list(raw)
            expected_corp_code = request.params["corp_code"]
            if any(record.corp_code != expected_corp_code for record in records):
                raise ValueError("OpenDART disclosure corp_code does not match Query")
            return [disclosure_to_evidence_draft(record) for record in records]
        if q.endpoint == "financial_indicator":
            records = parse_financial_indicators(raw)
            expected = {
                "corp_code": request.params["corp_code"],
                "business_year": request.params["bsns_year"],
                "report_code": request.params["reprt_code"],
                "indicator_class_code": request.params["idx_cl_code"],
            }
            if any(
                any(getattr(record, field) != value for field, value in expected.items())
                for record in records
            ):
                raise ValueError("OpenDART indicator lineage does not match Query")
            return [indicator_to_evidence_draft(record) for record in records]
        records = parse_financial_statement(
            raw,
            corp_code=request.params["corp_code"],
            business_year=request.params["bsns_year"],
            report_code=request.params["reprt_code"],
            fs_div=request.params["fs_div"],
            account_names=q.params["account_names"],
        )
        return [record_to_evidence_draft(record) for record in records]

    def classify_error(self, raw: dict) -> tuple[ReasonCode, bool]:
        kind, retryable = classify_status(raw.get("status"))
        return _REASON_CODES[kind], retryable

    def rate_limit_hint(self, raw: dict) -> RateLimitHint | None:
        try:
            kind, _ = classify_status(raw.get("status"))
        except ValueError:
            return None
        if kind is not DartErrorKind.RATE_LIMIT:
            return None
        return RateLimitHint(provider="dart", source="body_message")
