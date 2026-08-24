from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.domain.stock_master import (
    StockMasterRecord,
    StockMasterResolver,
    StockMasterSnapshot,
    load_alias_overlay,
    load_stock_master,
    parse_krx_rows,
    write_stock_master_atomic,
)
from app.domain.stock_scope import AssetType, evaluate_stock_scope


def row(
    code: str,
    name: str,
    market: str,
    *,
    security_group: str = "주권",
    certificate_type: str = "보통주",
    sector: str = "일반기업",
) -> dict[str, str]:
    return {
        "ISU_CD": f"KR7{code}0000",
        "ISU_SRT_CD": code,
        "ISU_NM": f"{name}{certificate_type}",
        "ISU_ABBRV": name,
        "ISU_ENG_NM": name,
        "LIST_DD": "20200102",
        "MKT_TP_NM": market,
        "SECUGRP_NM": security_group,
        "SECT_TP_NM": sector,
        "KIND_STKCERT_TP_NM": certificate_type,
        "PARVAL": "5000",
        "LIST_SHRS": "1000000",
    }


def records() -> tuple[StockMasterRecord, ...]:
    return (
        StockMasterRecord(code="000660", name="SK하이닉스", market="KOSPI", asset_type=AssetType.COMMON_STOCK, listing_date="19831220"),
        StockMasterRecord(code="005930", name="삼성전자", market="KOSPI", asset_type=AssetType.COMMON_STOCK, listing_date="19750611"),
        StockMasterRecord(code="005935", name="삼성전자우", market="KOSPI", asset_type=AssetType.PREFERRED_STOCK, listing_date="19750611"),
        StockMasterRecord(code="006400", name="삼성SDI", market="KOSPI", asset_type=AssetType.COMMON_STOCK, listing_date="19790227"),
        StockMasterRecord(code="034020", name="두산에너빌리티", market="KOSPI", asset_type=AssetType.COMMON_STOCK, listing_date="20001025"),
        StockMasterRecord(code="03473K", name="SK우", market="KOSPI", asset_type=AssetType.PREFERRED_STOCK, listing_date="20200102"),
        StockMasterRecord(code="035420", name="NAVER", market="KOSPI", asset_type=AssetType.COMMON_STOCK, listing_date="20081001"),
    )


def snapshot() -> StockMasterSnapshot:
    return StockMasterSnapshot(
        schema_version="krx_stock_master/v1",
        source="KRX_OPEN_API",
        as_of="20260821",
        generated_at="2026-08-24T00:00:00Z",
        record_count=len(records()),
        records=records(),
    )


@pytest.mark.parametrize(
    ("query", "code", "market"),
    [
        ("삼성전자", "005930", "KOSPI"),
        ("SK하이닉스", "000660", "KOSPI"),
        ("두산에너빌리티", "034020", "KOSPI"),
        ("삼성SDI", "006400", "KOSPI"),
        ("NAVER", "035420", "KOSPI"),
    ],
)
def test_full_master가_대표_종목을_resolve한다(query, code, market):
    result = StockMasterResolver(snapshot()).resolve(query)
    assert [(item.code, item.market) for item in result] == [(code, market)]


def test_full_master는_없는_회사와_긴_이름_masking을_보존한다():
    resolver = StockMasterResolver(snapshot())
    assert resolver.resolve("없는회사XYZ") == []
    assert [item.code for item in resolver.resolve("삼성전자우")] == ["005935"]
    assert resolver.resolve_exact("03473K")[0].code == "03473K"


@pytest.mark.parametrize("query", ["삼성에피스홀딩스", "0126Z0", "0126Z0 살까?"])
def test_full_master는_다섯번째_영문코드를_이름과_코드로_resolve한다(query):
    record = StockMasterRecord(
        code="0126Z0",
        name="삼성에피스홀딩스",
        market="KOSPI",
        asset_type=AssetType.COMMON_STOCK,
        listing_date="20251124",
    )
    value = StockMasterSnapshot(
        schema_version="krx_stock_master/v1",
        source="KRX_OPEN_API",
        as_of="20260821",
        generated_at="2026-08-24T00:00:00Z",
        record_count=1,
        records=(record,),
    )
    result = StockMasterResolver(value).resolve(query)
    assert [(item.code, item.match_kind) for item in result] == [
        ("0126Z0", "exact_name" if query == "삼성에피스홀딩스" else "exact_code")
    ]
    assert StockMasterResolver(value).resolve_exact("0126Z0")[0].name == "삼성에피스홀딩스"


def test_alias_overlay는_검색만_보조하고_canonical_identity를_바꾸지_않는다(tmp_path):
    overlay = tmp_path / "aliases.csv"
    overlay.write_text(
        "code,name,market,asset_type,aliases,is_delisted,is_managed\n"
        "035420,틀린이름,NASDAQ,ETF,네이버,1,1\n",
        encoding="utf-8",
    )
    aliases = load_alias_overlay(overlay, records())
    resolver = StockMasterResolver(snapshot(), aliases=aliases)

    resolved = resolver.resolve("네이버")[0]
    exact = resolver.resolve_exact("035420")[0]
    assert (resolved.name, resolved.market) == ("NAVER", "KOSPI")
    assert (exact.asset_type, exact.is_delisted, exact.is_managed) == (
        AssetType.COMMON_STOCK,
        False,
        False,
    )
    assert evaluate_stock_scope(exact).supported is True


def test_alias_overlay는_unknown_code와_중복_alias를_거부한다(tmp_path):
    unknown = tmp_path / "unknown.csv"
    unknown.write_text("code,aliases\n999999,없는별칭\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown KRX code"):
        load_alias_overlay(unknown, records())

    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text(
        "code,aliases\n005930,겹침\n035420,겹침\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="multiple codes"):
        load_alias_overlay(duplicate, records())

    canonical_collision = tmp_path / "canonical-collision.csv"
    canonical_collision.write_text(
        "code,aliases\n035420,삼성전자\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="canonical identity"):
        load_alias_overlay(canonical_collision, records())


def test_KRX_rows는_supported를_mapping하고_observed_unsupported를_제외한다():
    supported, excluded = parse_krx_rows(
        [
            row("005930", "삼성전자", "KOSPI"),
            row("338100", "NH프라임리츠", "KOSPI", security_group="부동산투자회사"),
            row("900140", "엘브이엠씨홀딩스", "KOSPI", security_group="외국주권"),
        ],
        expected_market="KOSPI",
    )
    assert supported[0].asset_type is AssetType.COMMON_STOCK
    assert supported[0].name == "삼성전자"
    assert {item.security_group for item in excluded} == {"부동산투자회사", "외국주권"}


def test_0126Z0_KOSPI_보통주는_COMMON_STOCK_snapshot으로_round_trip한다(tmp_path):
    supported, excluded = parse_krx_rows(
        [row("0126Z0", "삼성에피스홀딩스", "KOSPI")],
        expected_market="KOSPI",
    )
    assert excluded == ()
    assert supported[0].asset_type is AssetType.COMMON_STOCK

    value = StockMasterSnapshot(
        schema_version="krx_stock_master/v1",
        source="KRX_OPEN_API",
        as_of="20260821",
        generated_at="2026-08-24T00:00:00Z",
        record_count=1,
        records=supported,
    )
    path = tmp_path / "master.json"
    write_stock_master_atomic(path, value)
    assert load_stock_master(path) == value


def test_unsupported_security_group의_non_equity_identifier는_제외_기록으로_보존한다():
    supported, excluded = parse_krx_rows(
        [row("0030R0", "리츠", "KOSPI", security_group="부동산투자회사")],
        expected_market="KOSPI",
    )

    assert supported == ()
    assert excluded[0].code == "0030R0"


@pytest.mark.parametrize(
    ("certificate_type", "code"),
    [("구형우선주", "005935"), ("신형우선주", "00680K")],
)
def test_KRX에서_관측된_우선주_종류만_preferred로_mapping한다(
    certificate_type, code
):
    supported, excluded = parse_krx_rows(
        [row(code, "우선주", "KOSPI", certificate_type=certificate_type)],
        expected_market="KOSPI",
    )

    assert excluded == ()
    assert supported[0].asset_type is AssetType.PREFERRED_STOCK


def test_종류주권은_preferred로_추론하지_않고_explicit_unsupported로_제외한다():
    supported, excluded = parse_krx_rows(
        [row("03473K", "SK우", "KOSPI", certificate_type="종류주권")],
        expected_market="KOSPI",
    )

    assert supported == ()
    assert [(item.code, item.security_group) for item in excluded] == [
        ("03473K", "주권")
    ]


@pytest.mark.parametrize(
    "change,match",
    [
        ({"ISU_SRT_CD": "5930"}, "code"),
        ({"ISU_ABBRV": ""}, "name"),
        ({"MKT_TP_NM": "KONEX"}, "market"),
        ({"SECUGRP_NM": "처음보는증권"}, "security group"),
        ({"KIND_STKCERT_TP_NM": "처음보는주권"}, "certificate"),
    ],
)
def test_KRX_row의_invalid_unknown_value는_fail_closed한다(change, match):
    value = row("005930", "삼성전자", "KOSPI") | change
    with pytest.raises((ValueError, ValidationError), match=match):
        parse_krx_rows([value], expected_market="KOSPI")


def test_snapshot은_duplicate_empty_count_mismatch를_거부한다():
    base = snapshot().model_dump(mode="json")
    with pytest.raises(ValidationError, match="duplicate"):
        StockMasterSnapshot.model_validate(base | {"records": [base["records"][0]] * 2, "record_count": 2})
    with pytest.raises(ValidationError, match="empty"):
        StockMasterSnapshot.model_validate(base | {"records": [], "record_count": 0})
    with pytest.raises(ValidationError, match="record_count"):
        StockMasterSnapshot.model_validate(base | {"record_count": 99})


def test_snapshot_round_trip과_corruption_fail_closed(tmp_path):
    path = tmp_path / "master.json"
    write_stock_master_atomic(path, snapshot())
    assert load_stock_master(path) == snapshot()

    path.write_text(json.dumps({"schema_version": "wrong"}), encoding="utf-8")
    with pytest.raises((ValueError, ValidationError)):
        load_stock_master(path)


def test_atomic_write_validation_failure는_existing_snapshot을_보존한다(tmp_path):
    path = tmp_path / "master.json"
    write_stock_master_atomic(path, snapshot())
    before = path.read_bytes()

    with pytest.raises(ValidationError):
        write_stock_master_atomic(
            path,
            snapshot().model_copy(update={"record_count": 99}),
        )

    assert path.read_bytes() == before
