"""Validated KRX stock-master snapshot and resolver."""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.stock_matcher import MatchableStock, StockMatcher, normalize
from app.domain.stock_scope import AssetType
from app.schemas.frozen import KRXCode, NonBlankStr

_OBSERVED_EXCLUDED_SECURITY_GROUPS = frozenset(
    {
        "부동산투자회사",
        "사회간접자본투융자회사",
        "투자회사",
        "외국주권",
        "주식예탁증권",
    }
)
_OBSERVED_PREFERRED_CERTIFICATE_TYPES = frozenset(
    {"구형우선주", "신형우선주"}
)


class StockMasterRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: KRXCode
    name: NonBlankStr
    market: Literal["KOSPI", "KOSDAQ"]
    asset_type: AssetType
    listing_date: str = Field(pattern=r"^[0-9]{8}$")


class ExcludedKrxRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: KRXCode
    security_group: NonBlankStr


class StockMasterSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["krx_stock_master/v1"]
    source: Literal["KRX_OPEN_API"]
    as_of: str = Field(pattern=r"^[0-9]{8}$")
    generated_at: NonBlankStr
    record_count: int = Field(ge=0)
    records: tuple[StockMasterRecord, ...]

    @model_validator(mode="after")
    def validate_complete_snapshot(self):
        if not self.records:
            raise ValueError("stock master records must not be empty")
        codes = [item.code for item in self.records]
        if len(codes) != len(set(codes)):
            raise ValueError("duplicate stock master code")
        if self.record_count != len(self.records):
            raise ValueError("record_count does not match records")
        if tuple(sorted(codes)) != tuple(codes):
            raise ValueError("stock master records must use deterministic code ordering")
        return self


class StockMasterResolver:
    def __init__(
        self,
        snapshot: StockMasterSnapshot,
        *,
        aliases: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        overlay = aliases or {}
        self._matcher = StockMatcher(
            tuple(
                MatchableStock(
                    code=item.code,
                    name=item.name,
                    market=item.market,
                    asset_type=item.asset_type,
                    aliases=overlay.get(item.code, ()),
                    is_delisted=False,
                    is_managed=False,
                )
                for item in snapshot.records
            )
        )

    def resolve(self, text: str, limit: int = 5):
        return self._matcher.resolve(text, limit)

    def resolve_exact(self, code: str):
        return self._matcher.resolve_exact(code)


def _required_text(row: Mapping[str, object], key: str, label: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"KRX row {label} must be non-blank")
    return value.strip()


def parse_krx_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    expected_market: Literal["KOSPI", "KOSDAQ"],
) -> tuple[tuple[StockMasterRecord, ...], tuple[ExcludedKrxRecord, ...]]:
    supported: list[StockMasterRecord] = []
    excluded: list[ExcludedKrxRecord] = []
    seen: set[str] = set()
    for row in rows:
        code = _required_text(row, "ISU_SRT_CD", "code")
        name = _required_text(row, "ISU_ABBRV", "name")
        market = _required_text(row, "MKT_TP_NM", "market")
        listing_date = _required_text(row, "LIST_DD", "listing date").replace("/", "")
        security_group = _required_text(row, "SECUGRP_NM", "security group")
        certificate_type = _required_text(row, "KIND_STKCERT_TP_NM", "certificate type")
        record_identity = ExcludedKrxRecord(code=code, security_group=security_group)
        if market != expected_market:
            raise ValueError(f"KRX row market mismatch: {market}")
        if code in seen:
            raise ValueError(f"duplicate KRX code: {code}")
        seen.add(code)
        if security_group in _OBSERVED_EXCLUDED_SECURITY_GROUPS:
            excluded.append(record_identity)
            continue
        if security_group != "주권":
            raise ValueError(f"unknown KRX security group: {security_group}")
        if "SPAC" in str(row.get("SECT_TP_NM") or "").upper():
            excluded.append(record_identity)
            continue
        if certificate_type == "보통주":
            asset_type = AssetType.COMMON_STOCK
        elif certificate_type in _OBSERVED_PREFERRED_CERTIFICATE_TYPES:
            asset_type = AssetType.PREFERRED_STOCK
        else:
            raise ValueError(f"unknown KRX certificate type: {certificate_type}")
        supported.append(
            StockMasterRecord(
                code=code,
                name=name,
                market=market,
                asset_type=asset_type,
                listing_date=listing_date,
            )
        )
    return tuple(sorted(supported, key=lambda item: item.code)), tuple(
        sorted(excluded, key=lambda item: item.code)
    )


def load_alias_overlay(
    path: str | Path,
    records: Iterable[StockMasterRecord],
) -> dict[str, tuple[str, ...]]:
    authoritative_records = tuple(records)
    authoritative_codes = {item.code for item in authoritative_records}
    canonical_owner = {
        normalize(identity): item.code
        for item in authoritative_records
        for identity in (item.code, item.name)
    }
    by_code: dict[str, tuple[str, ...]] = {}
    alias_owner: dict[str, str] = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = str(row.get("code") or "").strip()
            if code not in authoritative_codes:
                raise ValueError(f"alias overlay references unknown KRX code: {code}")
            aliases = tuple(
                item.strip()
                for item in str(row.get("aliases") or "").split("|")
                if item.strip()
            )
            for alias in aliases:
                key = normalize(alias)
                canonical_code = canonical_owner.get(key)
                if canonical_code is not None and canonical_code != code:
                    raise ValueError(
                        f"alias conflicts with KRX canonical identity: {alias}"
                    )
                owner = alias_owner.get(key)
                if owner is not None and owner != code:
                    raise ValueError(f"alias maps to multiple codes: {alias}")
                alias_owner[key] = code
            by_code[code] = aliases
    return by_code


def load_stock_master(path: str | Path) -> StockMasterSnapshot:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"KRX stock master snapshot not found: {target}")
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid KRX stock master snapshot: {target}") from exc
    return StockMasterSnapshot.model_validate(body)


def write_stock_master_atomic(path: str | Path, snapshot: StockMasterSnapshot) -> None:
    target = Path(path)
    validated = StockMasterSnapshot.model_validate(snapshot.model_dump(mode="json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(validated.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
