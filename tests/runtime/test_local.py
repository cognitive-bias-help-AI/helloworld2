"""로컬(데모) 조립 루트 계약."""

import os

import pytest

from app.orchestration.state import ReviewState
from app.runtime.local import _capacities, initial_state, load_dotenv


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
