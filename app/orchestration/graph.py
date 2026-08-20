"""S0 full-thin LangGraph topology."""

from langgraph.graph import END, START, StateGraph

from app.orchestration.nodes.s0 import make_nodes
from app.orchestration.runtime import ReviewRequestContext, RuntimeDeps
from app.orchestration.state import ReviewState

VERTICES = ("n0", "n1", "n2", "intake_review", "n5", "n6", "n7", "n8", "n9", "n10", "n11", "n12")


def _blocked(state: ReviewState) -> bool:
    return any(":block:" in item for item in state["node_results"])


def build_graph(deps: RuntimeDeps, *, checkpointer=None):
    graph = StateGraph(ReviewState, context_schema=ReviewRequestContext)
    for name, function in make_nodes(deps).items():
        graph.add_node(name, function)
    graph.add_edge(START, "n0")
    graph.add_edge("n0", "n1")
    graph.add_conditional_edges("n1", lambda s: "n12" if _blocked(s) else "n2")
    graph.add_conditional_edges("n2", lambda s: "n12" if _blocked(s) else "intake_review")
    graph.add_conditional_edges("n5", lambda s: "n12" if _blocked(s) else "n6")
    for left, right in (("n6", "n7"), ("n7", "n8"), ("n8", "n9")):
        graph.add_edge(left, right)
    graph.add_conditional_edges("n9", lambda s: "n12" if _blocked(s) else "n11")
    graph.add_conditional_edges("n11", lambda s: "n12" if s.get("report_id") else "n10")
    graph.add_conditional_edges("n10", lambda s: "n11" if not _blocked(s) else "n12")
    graph.add_edge("n12", END)
    return graph.compile(checkpointer=checkpointer)
