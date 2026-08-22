"""Parse OpenDART financial indicators without interpreting their units."""

from decimal import Decimal, InvalidOperation

from providers.dart.errors import require_success
from providers.dart.models import DartFinancialIndicatorRecord


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _decimal_or_none(value: str) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def parse_financial_indicators(raw: dict) -> list[DartFinancialIndicatorRecord]:
    require_success(raw)
    rows = raw.get("list")
    if not isinstance(rows, list):
        raise ValueError("OpenDART success response requires list")
    records = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("OpenDART financial indicator row must be an object")
        required = (
            "reprt_code",
            "bsns_year",
            "corp_code",
            "idx_cl_code",
            "idx_cl_nm",
            "idx_code",
            "idx_nm",
        )
        if any(
            not isinstance(row.get(key), str) or not row[key].strip()
            for key in required
        ):
            raise ValueError("OpenDART financial indicator row lacks required fields")
        raw_value = row.get("idx_val")
        if raw_value is None:
            value_text = ""
        elif isinstance(raw_value, str):
            value_text = raw_value.strip()
        else:
            raise ValueError("OpenDART idx_val must be a string or null")
        records.append(
            DartFinancialIndicatorRecord(
                corp_code=row["corp_code"],
                stock_code=_optional_text(row.get("stock_code")),
                business_year=row["bsns_year"],
                report_code=row["reprt_code"],
                settlement_date=_optional_text(row.get("stlm_dt")),
                indicator_class_code=row["idx_cl_code"],
                indicator_class_name=row["idx_cl_nm"],
                indicator_code=row["idx_code"],
                indicator_name=row["idx_nm"],
                indicator_value_raw=value_text,
                indicator_value=_decimal_or_none(value_text),
            )
        )
    return records
