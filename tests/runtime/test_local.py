"""로컬(데모) 조립 루트 계약."""

import os

import httpx
import pytest
from pydantic import TypeAdapter

from app.domain.stock_master import StockMasterResolver, write_stock_master_atomic
from app.models.anthropic_gateway import AnthropicModelGateway
from app.models.mlapi_gateway import MlApiModelGateway
from app.orchestration.state import ReviewState
from app.runtime.ids import generate_ulid
from app.runtime.local import (
    DEFAULT_DIRECTORY,
    _capacities,
    _select_stock_resolver,
    compose_local_runtime,
    initial_state,
    load_dotenv,
)
from app.schemas.frozen import ULID
from tests.domain.test_stock_master import snapshot


def test_local_stock_resolver_uses_snapshot_when_present(tmp_path):
    snapshot_path = tmp_path / "krx.json"
    write_stock_master_atomic(snapshot_path, snapshot())
    resolver = _select_stock_resolver(snapshot_path, tmp_path / "aliases.csv")
    assert isinstance(resolver, StockMasterResolver)


def test_local_stock_resolver_falls_back_to_csv_when_snapshot_absent(tmp_path):
    csv_path = tmp_path / "stocks.csv"
    csv_path.write_text(
        "code,name,market,asset_type,aliases,is_delisted,is_managed\n"
        "005930,삼성전자,KOSPI,COMMON_STOCK,삼전,0,0\n", encoding="utf-8"
    )
    resolver = _select_stock_resolver(tmp_path / "missing.json", csv_path)
    assert resolver.resolve_exact("005930")[0].name == "삼성전자"


def test_local_stock_resolver_fails_clearly_when_both_sources_absent(tmp_path):
    with pytest.raises(FileNotFoundError, match="stock resolver"):
        _select_stock_resolver(tmp_path / "missing.json", tmp_path / "missing.csv")


def test_initial_state가_ReviewState의_모든_채널을_채운다():
    """채널 하나가 비면 노드가 KeyError 로 죽는다. 그것도 실행 중반에 죽는다.

    ReviewState 에 채널이 추가되면 이 테스트가 먼저 깨져야 한다.
    """
    assert set(initial_state("run", "thread")) == set(ReviewState.__annotations__)


def test_initial_state의_누적_채널은_빈_값으로_시작한다():
    state = initial_state("run", "thread")
    assert state["claim_ids"] == []
    assert state["node_results"] == []
    assert state["counters"] == {}
    assert state["collections"] == {}
    assert state["report_id"] is None


def test_initial_state는_as_of와_started_at을_같은_시각으로_둔다():
    state = initial_state("run", "thread")
    assert state["as_of"] == state["started_at"]


# ── .env 적재 ─────────────────────────────────────────────────────


def test_dotenv는_주석과_따옴표를_처리한다(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "# 주석\n\nFOO=bar\nBAZ=\"quoted\"\nQUX='single'\n빈줄무시\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAZ", raising=False)
    monkeypatch.delenv("QUX", raising=False)
    assert load_dotenv(path) == 3
    assert os.environ["FOO"] == "bar"
    assert os.environ["BAZ"] == "quoted"
    assert os.environ["QUX"] == "single"


def test_dotenv는_이미_설정된_값을_덮지_않는다(tmp_path, monkeypatch):
    """export 로 준 값이 파일에 먹히면 데모 중에 왜 안 바뀌는지 알 수 없다."""
    path = tmp_path / ".env"
    path.write_text("FOO=from_file\n", encoding="utf-8")
    monkeypatch.setenv("FOO", "from_shell")
    load_dotenv(path)
    assert os.environ["FOO"] == "from_shell"


def test_dotenv_파일이_없어도_실패하지_않는다(tmp_path):
    assert load_dotenv(tmp_path / "없다.env") == 0


# ── provider 수용량 ───────────────────────────────────────────────


class _Adapter:
    def __init__(self, name: str, max_concurrency: object) -> None:
        self.name = name
        self.max_concurrency = max_concurrency


def test_수용량을_어댑터에서_읽는다():
    assert _capacities({"naver": _Adapter("naver", 3)}) == {"naver": 3}


def test_어댑터_소유권이_어긋나면_거부한다():
    """키가 'dart' 인데 NaverAdapter 가 앉아 있으면 lineage 가 통째로 틀어진다."""
    with pytest.raises(ValueError, match="ownership"):
        _capacities({"dart": _Adapter("naver", 3)})


@pytest.mark.parametrize("capacity", [0, -1, True, "3", None])
def test_수용량이_양의_정수가_아니면_거부한다(capacity):
    with pytest.raises(ValueError, match="capacity"):
        _capacities({"naver": _Adapter("naver", capacity)})


def test_provider가_하나도_없어도_조립_자체는_가능하다():
    """자격증명이 아직 없는 단계에서도 그래프를 켜볼 수 있어야 한다."""
    assert _capacities({}) == {}


@pytest.mark.asyncio
async def test_injected_Anthropic_client는_environment_API_key를_요구하지_않는다(
    monkeypatch,
):
    class FalseyClient:
        def __bool__(self):
            return False

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    injected = FalseyClient()
    async with httpx.AsyncClient() as http_client, compose_local_runtime(
        anthropic_client=injected,
        http_client=http_client,
        directory_path=DEFAULT_DIRECTORY,
    ) as runtime:
        assert runtime.deps.model_gateway._client is injected


@pytest.mark.asyncio
async def test_Anthropic_client를_생성할때는_environment_API_key가_필수다(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        async with compose_local_runtime():
            pass


def _set_mlapi_environment(monkeypatch):
    monkeypatch.setenv("MODEL_BACKEND", "mlapi")
    for model in ("LUNA", "TERRA", "SOL"):
        monkeypatch.setenv(f"{model}_API_URL", f"https://{model.lower()}.mlapi.run")
        monkeypatch.setenv(f"{model}_API_KEY", f"{model.lower()}-key")


@pytest.mark.asyncio
async def test_MODEL_BACKEND_mlapi는_shared_http_client로_gateway를_조립한다(monkeypatch):
    _set_mlapi_environment(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    async with httpx.AsyncClient() as client, compose_local_runtime(
        http_client=client,
        directory_path=DEFAULT_DIRECTORY,
    ) as runtime:
        assert isinstance(runtime.deps.model_gateway, MlApiModelGateway)
        assert runtime.deps.model_gateway._client is client
        assert runtime.model_registry["SMALL"].model_id == "gpt-5.6-luna"
        assert runtime.model_registry["MID"].model_id == "gpt-5.6-terra"
        assert runtime.model_registry["LARGE"].model_id == "gpt-5.6-sol"
        assert all(
            spec.price_cached_in_krw_per_1m is None
            for spec in runtime.model_registry.values()
        )


@pytest.mark.parametrize(
    "missing",
    [
        "LUNA_API_URL",
        "LUNA_API_KEY",
        "TERRA_API_URL",
        "TERRA_API_KEY",
        "SOL_API_URL",
        "SOL_API_KEY",
    ],
)
@pytest.mark.asyncio
async def test_MODEL_BACKEND_mlapi는_각_필수설정_누락을_fail_fast한다(monkeypatch, missing):
    _set_mlapi_environment(monkeypatch)
    monkeypatch.delenv(missing)

    with pytest.raises(RuntimeError, match=missing):
        async with compose_local_runtime():
            pass


@pytest.mark.asyncio
async def test_MODEL_BACKEND_미설정은_기존_Anthropic_backend를_유지한다(monkeypatch):
    monkeypatch.delenv("MODEL_BACKEND", raising=False)

    class InjectedClient:
        pass

    injected = InjectedClient()
    async with httpx.AsyncClient() as http_client, compose_local_runtime(
        anthropic_client=injected,
        http_client=http_client,
        directory_path=DEFAULT_DIRECTORY,
    ) as runtime:
        assert isinstance(runtime.deps.model_gateway, AnthropicModelGateway)
        assert runtime.deps.model_gateway._client is injected


@pytest.mark.asyncio
async def test_MODEL_BACKEND_anthropic_명시는_기존_registry와_client_소유권을_유지한다(
    monkeypatch,
):
    monkeypatch.setenv("MODEL_BACKEND", "anthropic")

    class InjectedClient:
        pass

    injected = InjectedClient()
    http_client = httpx.AsyncClient()
    async with compose_local_runtime(
        anthropic_client=injected,
        http_client=http_client,
        directory_path=DEFAULT_DIRECTORY,
    ) as runtime:
        assert isinstance(runtime.deps.model_gateway, AnthropicModelGateway)
        assert runtime.model_registry["SMALL"].model_id.startswith("claude-")
    assert not http_client.is_closed
    await http_client.aclose()


@pytest.mark.asyncio
async def test_default_runtime은_KRX_snapshot을_resolver_authority로_사용한다(
    tmp_path, monkeypatch
):
    _set_mlapi_environment(monkeypatch)
    master = tmp_path / "master.json"
    aliases = tmp_path / "aliases.csv"
    write_stock_master_atomic(master, snapshot())
    aliases.write_text("code,aliases\n005930,삼전\n", encoding="utf-8")

    async with httpx.AsyncClient() as client, compose_local_runtime(
        http_client=client,
        stock_master_path=master,
        alias_overlay_path=aliases,
    ) as runtime:
        assert isinstance(runtime.deps.stock_resolver, StockMasterResolver)
        assert runtime.deps.stock_resolver.resolve("삼전")[0].code == "005930"
        assert runtime.deps.id_factory is generate_ulid
        TypeAdapter(ULID).validate_python(runtime.deps.id_factory())


@pytest.mark.asyncio
async def test_default_runtime은_snapshot_missing_malformed_empty를_CSV로_fallback하지_않는다(
    tmp_path, monkeypatch
):
    _set_mlapi_environment(monkeypatch)
    for index, body in enumerate((None, "not-json", '{"records": []}')):
        master = tmp_path / f"master-{index}.json"
        if body is not None:
            master.write_text(body, encoding="utf-8")
        with pytest.raises((FileNotFoundError, ValueError)):
            async with compose_local_runtime(
                stock_master_path=master,
                alias_overlay_path=tmp_path / f"missing-{index}.csv",
            ):
                pass


@pytest.mark.asyncio
async def test_알수없는_MODEL_BACKEND는_조용히_fallback하지_않는다(monkeypatch):
    monkeypatch.setenv("MODEL_BACKEND", "unknown")

    with pytest.raises(RuntimeError, match="MODEL_BACKEND"):
        async with compose_local_runtime():
            pass
