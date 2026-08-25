"""Parse OpenDART full financial statements into DART domain records."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.domain.account_concepts import (
    ACCOUNT_CONCEPTS,
    dart_account_matches,
    resolve_account_concepts,
)
from providers.dart.errors import require_success
from providers.dart.models import DartFinancialRecord


def normalize_dart_amount(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("DART amount cannot be boolean")
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        raise ValueError("DART amount must be an integer string")
    normalized = value.replace(",", "").strip()
    if not normalized:
        return None
    try:
        return int(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid DART amount: {value!r}") from exc


def parse_financial_statement(
    raw: dict,
    *,
    corp_code: str,
    business_year: str,
    report_code: str,
    fs_div: str,
    account_names: Iterable[str],
) -> list[DartFinancialRecord]:
    require_success(raw)
    selected = frozenset(account_names)
    if not selected:
        raise ValueError("account_names must not be empty")
    selected_concepts = tuple(
        concept
        for concept, spec in ACCOUNT_CONCEPTS.items()
        if any(account_id in selected for account_id in spec.dart_account_ids)
    ) + resolve_account_concepts(" ".join(selected))
    rows = raw.get("list")
    if not isinstance(rows, list):
        raise ValueError("OpenDART success response requires list")
    records = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        account_name = row.get("account_nm")
        account_id_value = row.get("account_id")
        selected_match = account_name in selected
        concept_match = any(
            dart_account_matches(
                concept,
                account_name=account_name if isinstance(account_name, str) else "",
                account_id=account_id_value if isinstance(account_id_value, str) else None,
            )
            for concept in selected_concepts
        )
        if not selected_match and not concept_match:
            continue
        required = ("rcept_no", "sj_div", "sj_nm", "account_nm")
        if any(not isinstance(row.get(key), str) or not row[key].strip() for key in required):
            raise ValueError("OpenDART financial row lacks required identity fields")
        account_id = row.get("account_id")
        if not isinstance(account_id, str) or not account_id.strip():
            account_id = row["account_nm"]
        currency = row.get("currency")
        records.append(
            DartFinancialRecord(
                corp_code=corp_code,
                corp_name=(
                    row["corp_name"].strip()
                    if isinstance(row.get("corp_name"), str) and row["corp_name"].strip()
                    else None
                ),
                receipt_no=row["rcept_no"],
                account_id=account_id,
                account_name=row["account_nm"],
                statement_code=row["sj_div"],
                statement_name=row["sj_nm"],
                amount=normalize_dart_amount(row.get("thstrm_amount")),
                currency=(
                    currency.strip()
                    if isinstance(currency, str) and currency.strip()
                    else None
                ),
                business_year=business_year,
                report_code=report_code,
                fs_div=fs_div,
                current_amount=normalize_dart_amount(row.get("thstrm_amount")),
                prior_amount=normalize_dart_amount(row.get("frmtrm_amount")),
                current_cumulative_amount=normalize_dart_amount(row.get("thstrm_add_amount")),
                prior_comparable_amount=normalize_dart_amount(row.get("frmtrm_add_amount")),
            )
        )
    return records
