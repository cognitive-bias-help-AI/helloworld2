"""Parse OpenDART disclosure search metadata into DART domain records."""

from providers.dart.errors import require_success
from providers.dart.models import DartDisclosureRecord


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def parse_disclosure_list(raw: dict) -> list[DartDisclosureRecord]:
    require_success(raw)
    rows = raw.get("list")
    if not isinstance(rows, list):
        raise ValueError("OpenDART success response requires list")
    records = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("OpenDART disclosure row must be an object")
        required = ("corp_code", "corp_name", "report_nm", "rcept_no", "rcept_dt")
        if any(not isinstance(row.get(key), str) or not row[key].strip() for key in required):
            raise ValueError("OpenDART disclosure row lacks required identity fields")
        records.append(
            DartDisclosureRecord(
                corp_code=row["corp_code"],
                corp_name=row["corp_name"],
                stock_code=_optional_text(row.get("stock_code")),
                corp_class=_optional_text(row.get("corp_cls")),
                report_name=row["report_nm"],
                receipt_no=row["rcept_no"],
                receipt_date=row["rcept_dt"],
                submitter=_optional_text(row.get("flr_nm")),
                remark=_optional_text(row.get("rm")),
            )
        )
    return records
