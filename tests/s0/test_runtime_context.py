import ast
from pathlib import Path
from typing import get_type_hints

import pytest

from app.orchestration.checkpoint import MeasuringInMemorySaver
from app.orchestration.graph import VERTICES, build_graph
from app.orchestration.runtime import ReviewRequestContext
from app.orchestration.state import ReviewState
from tests.s0.runtime_fixtures import RAW, deps, initial_state


@pytest.mark.asyncio
async def test_runtime_context_n0_ownership과_checkpoint_leakage():
    runtime_deps = deps()
    saver = MeasuringInMemorySaver()
    graph = build_graph(runtime_deps, checkpointer=saver)
    config = {"configurable": {"thread_id": "thread-s0"}}

    result = await graph.ainvoke(
        initial_state(), config, context=ReviewRequestContext(raw_text=RAW)
    )

    assert result["node_results"][-1] == "n12:end"
    assert result["report_id"]
    assert await runtime_deps.review_store.get_report(result["report_id"])
    assert "raw_text" not in get_type_hints(ReviewState)
    assert RAW.encode() not in b"".join(saver.serialized_payloads)
    assert max(saver.serialized_sizes) <= 5120
    assert result["thread_id"] == "thread-s0"


@pytest.mark.asyncio
async def test_context가_없으면_n0는_put_input전에_결정적으로_실패한다():
    runtime_deps = deps()
    graph = build_graph(runtime_deps)
    with pytest.raises(ValueError, match="raw_text is required"):
        await graph.ainvoke(initial_state())
    assert runtime_deps.review_store._inputs == {}


def test_14_vertices와_n0만_raw_text를_읽는_architecture():
    assert VERTICES == (
        "n0",
        "n1",
        "n2",
        "n3",
        "n3b",
        "n4",
        "n5",
        "n6",
        "n7",
        "n8",
        "n9",
        "n10",
        "n11",
        "n12",
    )
    path = Path("app/orchestration/nodes/s0.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    owners = set()
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in VERTICES
    }
    for name, node in functions.items():
        if any(
            isinstance(child, ast.Attribute) and child.attr == "raw_text"
            for child in ast.walk(node)
        ):
            owners.add(name)
    assert owners == {"n0"}
