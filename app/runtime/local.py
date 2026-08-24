"""Postgres 없이 도는 로컬/데모 조립 루트.

🔴 이것은 production 이 아니다.

`compose_production_runtime()` 은 의도적으로 fail-fast 다 — ReviewStore·
ModelGateway·StockResolver·corp mapping 이 없으면 켜지지 않는다. "실행 가능"
을 "운영 가능"으로 착각하지 않기 위한 설계이고, 그 설계는 옳다.

이 파일은 그 판단을 뒤집지 않는다. 대신 **아직 없는 것을 분명히 드러낸 채로**
그래프를 한 번 끝까지 돌려보기 위한 별도 경로다. 차이는 두 가지뿐이다.

    EvidenceStore   SqlEvidenceStore  ->  MemoryEvidenceStore   (프로세스 종료 시 소멸)
    ReviewStore     (운영 구현 없음)   ->  MemoryReviewStore     (프로세스 종료 시 소멸)

그래서 이름이 `local` 이다. 이 경로로 만든 report 는 **재시작하면 사라진다.**
Phase E(Production ReviewStore)가 닫히면 이 파일은 폐기 대상이다.

■ Provider 선택

자격증명이 있는 provider 만 조립한다. 키움은 계좌 개설 + IP 등록이 선행돼야
하고 DART 는 corp-code 매핑이 있어야 하므로, 셋 다 갖춰지기 전에도 뉴스
경로만으로 그래프를 돌려볼 수 있어야 한다.

단 **조용히 빠지지는 않는다.** 빠진 provider 는 `LocalRuntime.missing` 에 남고,
n5 가 그 provider 로 Query 를 만들면 게이트웨이가 계약 위반으로 멈춘다.
"없는 걸 없다고 말하는" 쪽이 "가짜로 채우는" 쪽보다 낫다는 원칙 그대로다.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anthropic
import httpx

from app.domain.stock_directory import CsvStockDirectory
from app.domain.stock_master import (
    StockMasterResolver,
    load_alias_overlay,
    load_stock_master,
)
from app.gateway.adapters.dart import DartAdapter
from app.gateway.adapters.kiwoom import KiwoomAdapter
from app.gateway.adapters.naver import NaverAdapter
from app.gateway.admission import ProviderAdmissionController
from app.gateway.protocols import ProviderAdapter
from app.models.anthropic_gateway import AnthropicModelGateway
from app.models.mlapi_gateway import (
    MLAPI_MODEL_BY_SLOT,
    MlApiEndpoint,
    MlApiModelGateway,
)
from app.models.registry import MODEL_REGISTRY
from app.orchestration.checkpoint import MeasuringInMemorySaver
from app.orchestration.graph import build_graph
from app.orchestration.reporting import RenderCandidateStore
from app.orchestration.runtime import RuntimeDeps
from app.runtime.ids import generate_ulid
from app.schemas.frozen import ModelSpec
from app.store.memory_evidence_store import MemoryEvidenceStore
from app.store.memory_review_store import MemoryReviewStore
from providers.dart.corp_code import DartCorpCodeResolver
from providers.dart.corp_code_loader import CorpCodeUnavailable, load_mapping
from providers.kiwoom.core import Environment as KiwoomEnvironment
from providers.kiwoom.core import KiwoomAdapter as KiwoomCoreAdapter
from providers.kiwoom.core import KiwoomCredentials

DEFAULT_DIRECTORY = Path("data/stock_directory.csv")
DEFAULT_STOCK_MASTER = Path("data/krx_stock_master.json")
DEFAULT_CORP_CACHE = Path("data/dart_corp_code.json")


@dataclass(frozen=True)
class LocalRuntime:
    deps: RuntimeDeps
    graph: Any
    checkpointer: Any
    model_registry: Mapping[str, ModelSpec]
    missing: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class LocalModelRuntime:
    gateway: Any
    model_registry: Mapping[str, ModelSpec]
    http_client: httpx.AsyncClient


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def load_dotenv(path: str | Path = ".env") -> int:
    """`.env` 를 os.environ 에 얹는다. 이미 설정된 값은 덮지 않는다.

    python-dotenv 를 의존성에 추가하지 않는 이유: 이 한 가지를 위해 패키지를
    늘릴 이유가 없고, pyproject 의 의존성 범위를 넓히면 uv.lock 정책 논의와
    엮인다. 12줄로 끝나는 일이다.
    """
    target = Path(path)
    if not target.exists():
        return 0
    applied = 0
    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
            applied += 1
    return applied


def _adapters(
    client: httpx.AsyncClient,
    *,
    corp_cache: Path,
) -> tuple[dict[str, ProviderAdapter], list[str], list[str]]:
    adapters: dict[str, ProviderAdapter] = {}
    missing: list[str] = []
    notes: list[str] = []

    naver_id = _env("NAVER_CLIENT_ID")
    naver_secret = _env("NAVER_CLIENT_SECRET")
    if naver_id and naver_secret:
        adapters["naver"] = NaverAdapter(naver_id, naver_secret, client)
    else:
        missing.append("naver")
        notes.append("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 없음 - 뉴스 근거 수집 불가")

    dart_key = _env("DART_API_KEY")
    if dart_key:
        try:
            mapping = load_mapping(corp_cache)
        except CorpCodeUnavailable as exc:
            missing.append("dart")
            notes.append(f"DART corp-code 매핑 없음 - {exc}")
        else:
            adapters["dart"] = DartAdapter(
                dart_key, DartCorpCodeResolver(mapping), client
            )
            notes.append(f"DART corp-code {len(mapping)}건 로드")
    else:
        missing.append("dart")
        notes.append("DART_API_KEY 없음 - 재무·공시 근거 수집 불가")

    kiwoom_key = _env("KIWOOM_APP_KEY")
    kiwoom_secret = _env("KIWOOM_APP_SECRET")
    if kiwoom_key and kiwoom_secret:
        environment = (
            KiwoomEnvironment.PRODUCTION
            if _env("APP_ENV") == "production"
            else KiwoomEnvironment.MOCK
        )
        adapters["kiwoom"] = KiwoomAdapter(
            KiwoomCoreAdapter(
                client,
                KiwoomCredentials(app_key=kiwoom_key, secret_key=kiwoom_secret),
            ),
            environment=environment,
        )
    else:
        missing.append("kiwoom")
        notes.append("KIWOOM_APP_KEY / KIWOOM_APP_SECRET 없음 - 시세·수급 근거 수집 불가")

    return adapters, missing, notes


def _capacities(adapters: Mapping[str, ProviderAdapter]) -> dict[str, int]:
    """composition._provider_capacities 와 같은 검사.

    private 함수를 import 하지 않고 다시 쓴 이유: 남의 파일의 비공개 이름에
    묶이면 그쪽이 이름을 바꿀 때 이 파일이 조용히 깨진다. 6줄이다.
    """
    capacities: dict[str, int] = {}
    for provider, adapter in adapters.items():
        if adapter.name != provider:
            raise ValueError(f"adapter ownership mismatch: {provider}")
        capacity = adapter.max_concurrency
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError(f"provider admission capacity must be positive: {provider}")
        capacities[provider] = capacity
    return capacities


def _model_backend() -> str:
    backend = (_env("MODEL_BACKEND") or "anthropic").lower()
    if backend not in {"anthropic", "mlapi"}:
        raise RuntimeError("MODEL_BACKEND must be 'anthropic' or 'mlapi'")
    return backend


def _mlapi_endpoints() -> dict[str, MlApiEndpoint]:
    endpoints: dict[str, MlApiEndpoint] = {}
    for slot, prefix in (("SMALL", "LUNA"), ("MID", "TERRA"), ("LARGE", "SOL")):
        url_name = f"{prefix}_API_URL"
        key_name = f"{prefix}_API_KEY"
        url = _env(url_name)
        api_key = _env(key_name)
        if not url:
            raise RuntimeError(f"{url_name} is required when MODEL_BACKEND=mlapi")
        if not api_key:
            raise RuntimeError(f"{key_name} is required when MODEL_BACKEND=mlapi")
        endpoints[slot] = MlApiEndpoint(url, api_key, MLAPI_MODEL_BY_SLOT[slot])
    return endpoints


@asynccontextmanager
async def compose_local_model_runtime(
    *,
    anthropic_client: anthropic.AsyncAnthropic | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> AsyncIterator[LocalModelRuntime]:
    """조달·저장소와 무관하게 실제 선택된 모델 backend만 조립한다."""
    backend = _model_backend()
    mlapi_endpoints = _mlapi_endpoints() if backend == "mlapi" else None
    if backend == "anthropic" and anthropic_client is None and not _env("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY 가 없다. .env 에 넣거나 환경변수로 export 하라."
        )

    owns_http = http_client is None
    owns_model = backend == "anthropic" and anthropic_client is None
    client = http_client or httpx.AsyncClient()
    model_client = (
        anthropic_client if anthropic_client is not None else anthropic.AsyncAnthropic()
    ) if backend == "anthropic" else None
    model_gateway = (
        AnthropicModelGateway(model_client)
        if backend == "anthropic"
        else MlApiModelGateway(client, endpoints=mlapi_endpoints or {})
    )
    model_registry = (
        MODEL_REGISTRY
        if backend == "anthropic"
        else {slot: model_gateway.model_spec_for(slot) for slot in ("SMALL", "MID", "LARGE")}
    )

    try:
        yield LocalModelRuntime(
            gateway=model_gateway, model_registry=model_registry, http_client=client
        )
    finally:
        if owns_http:
            await client.aclose()
        if owns_model and model_client is not None:
            await model_client.close()


@asynccontextmanager
async def compose_local_runtime(
    *,
    directory_path: str | Path | None = None,
    stock_master_path: str | Path = DEFAULT_STOCK_MASTER,
    alias_overlay_path: str | Path = DEFAULT_DIRECTORY,
    corp_cache: str | Path = DEFAULT_CORP_CACHE,
    anthropic_client: anthropic.AsyncAnthropic | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> AsyncIterator[LocalRuntime]:
    """실 provider + 실 LLM + 메모리 저장소로 그래프를 조립한다.

    HTTP client 와 Anthropic client 는 **만든 쪽이 닫는다.** 주입받은 경우
    닫지 않는다 — production.py 가 shared client 를 다루는 규칙과 같다.
    """
    async with compose_local_model_runtime(
        anthropic_client=anthropic_client,
        http_client=http_client,
    ) as model_runtime:
        client = model_runtime.http_client
        adapters, missing, notes = _adapters(client, corp_cache=Path(corp_cache))
        checkpointer = MeasuringInMemorySaver()
        if directory_path is not None:
            stock_resolver = CsvStockDirectory.from_csv(directory_path)
        else:
            stock_master = load_stock_master(stock_master_path)
            aliases = load_alias_overlay(alias_overlay_path, stock_master.records)
            stock_resolver = StockMasterResolver(stock_master, aliases=aliases)
        deps = RuntimeDeps(
            review_store=MemoryReviewStore(),
            evidence_store=MemoryEvidenceStore(),
            provider_admission=ProviderAdmissionController(_capacities(adapters)),
            model_gateway=model_runtime.gateway,
            stock_resolver=stock_resolver,
            adapters=adapters,
            clock=lambda: datetime.now(UTC),
            id_factory=generate_ulid,
            render_candidates=RenderCandidateStore(),
        )
        yield LocalRuntime(
            deps=deps,
            graph=build_graph(deps, checkpointer=checkpointer),
            checkpointer=checkpointer,
            model_registry=model_runtime.model_registry,
            missing=tuple(missing),
            notes=tuple(notes),
        )


def initial_state(run_id: str, thread_id: str, *, now: datetime | None = None) -> dict:
    """ReviewState 의 19채널 초기값. 리듀서가 붙은 채널은 빈 값으로 시작한다."""
    stamp = (now or datetime.now(UTC)).isoformat()
    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "as_of": stamp,
        "snapshot_version": 0,
        "input_id": None,
        "stock": None,
        "user_action": None,
        "slots": [],
        "claim_ids": [],
        "conflicts": [],
        "query_ids": [],
        "collections": {},
        "claim_evaluation_ids": [],
        "finding_ids": [],
        "oppose": None,
        "report_id": None,
        "node_results": [],
        "counters": {},
        "started_at": stamp,
    }


__all__ = [
    "DEFAULT_CORP_CACHE",
    "DEFAULT_DIRECTORY",
    "DEFAULT_STOCK_MASTER",
    "LocalRuntime",
    "LocalModelRuntime",
    "compose_local_model_runtime",
    "compose_local_runtime",
    "initial_state",
    "load_dotenv",
]
