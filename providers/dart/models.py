"""OpenDART-only domain records."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class DartFinancialRecord:
    corp_code: str
    corp_name: str
    receipt_no: str
    account_id: str
    account_name: str
    statement_code: str
    statement_name: str
    amount: int | None
    currency: str | None
    business_year: str
    report_code: str
    fs_div: str


@dataclass(frozen=True, slots=True)
class DartDisclosureRecord:
    corp_code: str
    corp_name: str
    stock_code: str | None
    corp_class: str | None
    report_name: str
    receipt_no: str
    receipt_date: str
    submitter: str | None
    remark: str | None


@dataclass(frozen=True, slots=True)
class DartFinancialIndicatorRecord:
    corp_code: str
    stock_code: str | None
    business_year: str
    report_code: str
    settlement_date: str | None
    indicator_class_code: str
    indicator_class_name: str
    indicator_code: str
    indicator_name: str
    indicator_value_raw: str
    indicator_value: Decimal | None
