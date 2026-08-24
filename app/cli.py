"""로컬 실행 진입점.

    uv run python -m app.cli preflight              구조화 출력 스키마 수용 여부 확인
    uv run python -m app.cli corp-code              DART corp-code 매핑 내려받아 캐시
    uv run python -m app.cli resolve "삼전"          종목 해석만 확인 (LLM/네트워크 없음)
    uv run python -m app.cli review "삼성전자 살까?"   그래프를 끝까지 돌린다

`review` 는 HITL interrupt 를 만나면 표준입력으로 되묻는다. 그래프가 실제로
사람에게 되묻고 재개하는 경로를 눈으로 확인하는 것이 이 명령의 목적이다.

🔴 저장소는 메모리다(app/runtime/local.py 참조). 프로세스가 끝나면 report 도
   사라진다. Phase E 가 닫히기 전까지의 임시 경로다.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from langgraph.types import Command

from app.runtime.local import (
    DEFAULT_CORP_CACHE,
    DEFAULT_DIRECTORY,
    DEFAULT_STOCK_MASTER,
    compose_local_runtime,
    initial_state,
    load_dotenv,
)

_HITL_SCHEMA = "intake_review_hitl/v1"
_MAX_TURNS = 12


def _use_utf8() -> None:
    """Windows 기본 콘솔은 cp949 라 한글이 깨진다. 출력 스트림을 UTF-8 로 돌린다.

    chcp 65001 을 사람이 먼저 치게 만들면 데모에서 반드시 잊는다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")


def _out(text: str = "") -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _ask(prompt: str) -> str:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    line = sys.stdin.readline()
    if not line:
        raise SystemExit("\n입력이 끊겼다. 중단한다.")
    return line.strip()


# ══════════════════════════════════════════════════════════════════
# preflight — 구조화 출력이 우리 Draft 스키마를 받아주는가
# ══════════════════════════════════════════════════════════════════

def _draft_schemas() -> list[tuple[str, type]]:
    from app.orchestration.drafts import (
        FindingDraft,
        GuardScanResult,
        GuardVerdictDraft,
        RenderDraft,
        SemanticExtractionDraft,
    )
    from app.schemas.frozen import ClaimEvaluationDraft, ClaimStanceDraft

    return [
        ("n1", GuardScanResult),
        ("n3", SemanticExtractionDraft),
        ("n7", ClaimStanceDraft),
        ("n8", ClaimEvaluationDraft),
        ("n9", FindingDraft),
        ("n10", GuardVerdictDraft),
        ("n11", RenderDraft),
    ]


async def _preflight() -> int:
    """노드별 output_schema 를 API 가 받아주는지 최소 요청으로 확인한다.

    `tuple[int, int]` 이 만드는 `prefixItems`, `NonBlankStr` 이 만드는 `pattern`
    을 구조화 출력이 받아주는지는 문서만으로 확정할 수 없다. 데모 당일에
    n3·n10 만 400 으로 죽는 것을 미리 잡기 위한 명령이다.
    """
    import anthropic

    from app.models.registry import MODEL_BY_SLOT

    client = anthropic.AsyncAnthropic()
    failures = 0
    try:
        for node, schema in _draft_schemas():
            slot = {"n1": "SMALL", "n3": "SMALL", "n7": "SMALL", "n11": "MID"}.get(
                node, "LARGE"
            )
            try:
                await client.messages.parse(
                    model=MODEL_BY_SLOT[slot],
                    max_tokens=1024,
                    system="스키마 수용 여부만 확인한다. 빈 값으로 채워라.",
                    messages=[{"role": "user", "content": "{}"}],
                    output_format=schema,
                )
            except Exception as exc:  # noqa: BLE001 - 어떤 실패든 그대로 보고한다
                failures += 1
                _out(f"  [FAIL] {node:4} {schema.__name__:24} {type(exc).__name__}: {exc}")
            else:
                _out(f"  [ok]   {node:4} {schema.__name__:24} 수용")
    finally:
        await client.close()

    _out()
    if failures:
        _out(f"{failures}건 거부됨. 해당 스키마는 구조화 출력으로 못 받는다.")
        _out("→ 그 노드만 JSON 지시 + 수동 검증으로 우회해야 한다.")
    else:
        _out("7개 노드 스키마 전부 수용됨.")
    return 1 if failures else 0


# ══════════════════════════════════════════════════════════════════
# corp-code
# ══════════════════════════════════════════════════════════════════

async def _corp_code(out_path: Path) -> int:
    import os

    import httpx

    from providers.dart.corp_code_loader import (
        CorpCodeUnavailable,
        fetch_corp_code_mapping,
        save_mapping,
    )

    api_key = os.environ.get("DART_API_KEY", "").strip()
    if not api_key:
        _out("DART_API_KEY 가 없다. .env 를 확인하라.")
        return 1
    async with httpx.AsyncClient() as client:
        try:
            mapping = await fetch_corp_code_mapping(api_key, client)
        except CorpCodeUnavailable as exc:
            _out(f"실패: {exc}")
            return 1
    saved = save_mapping(out_path, mapping)
    _out(f"상장 종목 {len(mapping)}건을 {saved} 에 저장했다.")
    for code in ("005930", "000660", "035420"):
        _out(f"  {code} -> {mapping.get(code, '(없음)')}")
    return 0


async def _krx_master_sync(out_path: Path, as_of: str | None) -> int:
    import os

    import httpx

    from providers.krx.client import KrxClient
    from providers.krx.sync import sync_stock_master

    api_key = os.environ.get("KRX_API_KEY", "").strip()
    if not api_key:
        _out("KRX_API_KEY 가 없다. .env 를 확인하라.")
        return 1
    async with httpx.AsyncClient() as client:
        snapshot = await sync_stock_master(
            KrxClient(client, api_key=api_key),
            out_path,
            as_of=as_of,
        )
    _out(f"KRX 종목 {snapshot.record_count}건을 {out_path} 에 저장했다.")
    _out(f"  기준일: {snapshot.as_of}")
    return 0


# ══════════════════════════════════════════════════════════════════
# resolve
# ══════════════════════════════════════════════════════════════════

def _resolve(
    text: str,
    directory: Path | None,
    stock_master: Path,
    alias_overlay: Path,
) -> int:
    if directory is not None:
        from app.domain.stock_directory import CsvStockDirectory

        resolver = CsvStockDirectory.from_csv(directory)
    else:
        from app.domain.stock_master import (
            StockMasterResolver,
            load_alias_overlay,
            load_stock_master,
        )

        snapshot = load_stock_master(stock_master)
        resolver = StockMasterResolver(
            snapshot,
            aliases=load_alias_overlay(alias_overlay, snapshot.records),
        )
    candidates = resolver.resolve(text)
    if not candidates:
        _out(f"'{text}' 에서 종목을 찾지 못했다.")
        _out(f"→ {directory or stock_master} 에 등록된 종목만 해석된다.")
        return 1
    for item in candidates:
        _out(
            f"  {item.code}  {item.name:16} {item.market:6} "
            f"{item.match_kind:10} score={item.score}"
        )
    return 0


# ══════════════════════════════════════════════════════════════════
# review
# ══════════════════════════════════════════════════════════════════

def _render_interrupt(payload: object) -> object:
    """interrupt 페이로드를 사람에게 보여주고 재개 값을 만든다."""
    if isinstance(payload, dict) and payload.get("schema_version") == _HITL_SCHEMA:
        questions = payload.get("questions") or []
        _out()
        _out("── 추가 질문 ─────────────────────────────")
        answers = []
        for item in questions:
            _out(f"  (slot {item['slot_id']}) {item['question']}")
            answer = ""
            while not answer:
                answer = _ask("  > ")
                if not answer:
                    _out("  빈 답은 받을 수 없다.")
            answers.append({"ask_id": item["ask_id"], "answer": answer})
        return {"answers": answers}

    if isinstance(payload, dict) and "candidates" in payload:
        options = payload["candidates"]
        _out()
        _out("── 어느 종목인가 ─────────────────────────")
        for index, option in enumerate(options, start=1):
            _out(
                f"  {index}. {option['display_name']} "
                f"({option['selected_code']}, {option['market']})"
            )
        while True:
            raw = _ask("  번호 > ")
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return {"selected_code": options[int(raw) - 1]["selected_code"]}
            _out("  목록에 있는 번호를 입력하라.")

    _out()
    _out("알 수 없는 interrupt 페이로드다. 그대로 보여준다:")
    _out(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return _ask("재개 값(JSON) > ")


async def _review(
    text: str,
    directory: Path | None,
    stock_master: Path,
    alias_overlay: Path,
    corp_cache: Path,
) -> int:
    async with compose_local_runtime(
        directory_path=directory,
        stock_master_path=stock_master,
        alias_overlay_path=alias_overlay,
        corp_cache=corp_cache,
    ) as runtime:
        from app.orchestration.runtime import ReviewRequestContext

        _out("── 구성 ─────────────────────────────────")
        for note in runtime.notes:
            _out(f"  {note}")
        _out(f"  연결된 provider: {sorted(runtime.deps.adapters) or '(없음)'}")
        if runtime.missing:
            _out(f"  빠진 provider: {list(runtime.missing)}")
        _out()

        run_id = f"run-{uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": run_id}}
        payload: object = initial_state(run_id, run_id, now=datetime.now(UTC))
        context = ReviewRequestContext(raw_text=text)

        for turn in range(_MAX_TURNS):
            result = (
                await runtime.graph.ainvoke(payload, config, context=context)
                if turn == 0
                else await runtime.graph.ainvoke(payload, config)
            )
            interrupts = result.get("__interrupt__")
            if not interrupts:
                return await _print_report(runtime, result)
            payload = Command(resume=_render_interrupt(interrupts[0].value))

        _out(f"HITL 이 {_MAX_TURNS}회를 넘겼다. 중단한다.")
        return 1


async def _print_report(runtime, result: dict) -> int:
    _out()
    _out("── 실행 경로 ────────────────────────────")
    for item in result.get("node_results", []):
        _out(f"  {item}")
    counters = result.get("counters") or {}
    if counters:
        _out(f"  counters: {counters}")
    collections = result.get("collections") or {}
    for provider, value in sorted(collections.items()):
        _out(
            f"  {provider}: status={value.get('status')} "
            f"fetched={value.get('items_fetched')} adopted={value.get('items_adopted')}"
        )

    report_id = result.get("report_id")
    if not report_id:
        _out()
        _out("report 가 생성되지 않았다.")
        blocked = [x for x in result.get("node_results", []) if ":block:" in x]
        if blocked:
            _out(f"  차단 지점: {blocked}")
        return 1

    report = await runtime.deps.review_store.get_report(report_id)
    _out()
    _out(f"── 보고서 {report_id} ──────────────────")
    if not isinstance(report, dict):
        _out("  (본문을 읽지 못했다)")
        return 1
    for banner in report.get("banners") or []:
        _out(f"  [{banner}]")
    for slot in report.get("rendered_slots") or []:
        _out()
        _out(f"  · slot {slot.get('slot_no')}")
        _out(f"    {slot.get('text')}")
        for citation in slot.get("citations") or []:
            _out(f"      └ {citation.get('evidence_id')}: {citation.get('span')}")
    citations = report.get("citations") or []
    if citations:
        _out()
        _out("  ── 출처 ──")
        for item in citations:
            _out(
                f"    {item.get('evidence_id')} {item.get('publisher') or ''} "
                f"{item.get('source_url') or ''}"
            )
    return 0


# ══════════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="투자 판단 검토 로컬 실행")
    parser.add_argument("--env", default=".env", help="환경변수 파일 (기본 .env)")
    parser.add_argument("--directory", help="명시적 demo/test 종목 CSV 경로")
    parser.add_argument(
        "--stock-master",
        default=str(DEFAULT_STOCK_MASTER),
        help="검증된 KRX stock-master snapshot 경로",
    )
    parser.add_argument(
        "--alias-overlay",
        default=str(DEFAULT_DIRECTORY),
        help="검색 alias overlay CSV 경로",
    )
    parser.add_argument(
        "--corp-cache", default=str(DEFAULT_CORP_CACHE), help="DART corp-code 캐시 경로"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="구조화 출력 스키마 수용 여부 확인")
    sub.add_parser("corp-code", help="DART corp-code 매핑 내려받아 캐시")
    krx_parser = sub.add_parser("krx-master-sync", help="KRX stock master 동기화")
    krx_parser.add_argument("--as-of", help="기준일 YYYYMMDD; 없으면 최근 7일 탐색")
    resolve_parser = sub.add_parser("resolve", help="종목 해석만 확인")
    resolve_parser.add_argument("text")
    review_parser = sub.add_parser("review", help="그래프를 끝까지 실행")
    review_parser.add_argument("text")

    _use_utf8()
    args = parser.parse_args(argv)
    loaded = load_dotenv(args.env)
    if loaded:
        _out(f"{args.env} 에서 환경변수 {loaded}개 로드")

    directory = Path(args.directory) if args.directory else None
    stock_master = Path(args.stock_master)
    alias_overlay = Path(args.alias_overlay)
    corp_cache = Path(args.corp_cache)

    # 설정 누락은 버그가 아니라 준비 부족이다. 트레이스백 대신 할 일을 알려준다.
    try:
        if args.command == "resolve":
            return _resolve(args.text, directory, stock_master, alias_overlay)
        if args.command == "preflight":
            return asyncio.run(_preflight())
        if args.command == "corp-code":
            return asyncio.run(_corp_code(corp_cache))
        if args.command == "krx-master-sync":
            return asyncio.run(_krx_master_sync(stock_master, args.as_of))
        if args.command == "review":
            return asyncio.run(
                _review(args.text, directory, stock_master, alias_overlay, corp_cache)
            )
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        _out(f"중단: {exc}")
        return 1
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
