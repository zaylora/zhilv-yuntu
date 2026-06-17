from __future__ import annotations

from collections.abc import Iterator

from app.agents.nodes.budget_check import budget_check_node
from app.agents.nodes.critic import critic_node
from app.agents.nodes.dispatch import dispatch_node
from app.agents.nodes.hotel import hotel_node
from app.agents.nodes.meal_search import meal_search_node
from app.agents.nodes.schedule import schedule_node
from app.agents.nodes.spot_search import spot_search_node
from app.agents.nodes.summarize import summarize_node
from app.agents.nodes.transport_search import transport_search_node
from app.agents.nodes.weather import weather_node
from app.agents.state import BudgetReport, TripState
from app.config import TRIP_MAX_REPLAN
from app.models.schemas import CriticResponse, Itinerary, TokenUsage, TripRequest

# ── 阶段常量（唯一权威拓扑，collect 与 stream 均引用此处） ────────────────────
INITIAL_NODES = (dispatch_node, spot_search_node, meal_search_node, transport_search_node, weather_node)
REPLAN_NODES  = (schedule_node, hotel_node, budget_check_node, critic_node)   # critic 进回环
FINAL_NODES   = (summarize_node,)


def _merge_patch(state: TripState, patch: dict) -> TripState:
    merged = dict(state)
    for key, value in patch.items():
        if key in {"trace", "errors"}:
            merged[key] = [*merged.get(key, []), *value]
        else:
            merged[key] = value
    return merged  # type: ignore[return-value]


def budget_router(state: TripState) -> str:
    """保留兼容：现有测试 test_budget_router_replans_only_when_over_budget_under_limit 依赖此函数。"""
    report = state.get("budget_report")
    if not isinstance(report, BudgetReport):
        return "ok"
    max_replan = state.get("max_replan", TRIP_MAX_REPLAN)
    if report.over_budget and state.get("replan_count", 0) < max_replan:
        return "replan"
    return "ok"


def replan_router(state: TripState) -> str:
    """统一回环路由：预算超支 或 Critic revise，且未达 max_replan 时回 schedule。

    budget_check 在超支且未达上限时已自增 replan_count；
    critic 在 revise 未达上限时也已自增（达上限时强制 accept 不增）。
    此函数只读 state 判断方向，不自增。
    回环必然终止：达到 max_replan 后，budget 侧与 critic 侧均因
    `replan_count < max_replan` 为假而不触发，直接返回 "ok"。
    """
    max_replan = state.get("max_replan", TRIP_MAX_REPLAN)
    replan_count = state.get("replan_count", 0)
    report = state.get("budget_report")
    if isinstance(report, BudgetReport) and report.over_budget and replan_count < max_replan:
        return "replan"
    critic_rep = state.get("critic_report")
    if critic_rep is not None and getattr(critic_rep, "verdict", None) == "revise" and replan_count < max_replan:
        return "replan"
    return "ok"


def _run_local_graph(initial_state: TripState) -> TripState:
    state = initial_state
    for node in INITIAL_NODES:
        state = _merge_patch(state, node(state))

    while True:
        for node in REPLAN_NODES:
            state = _merge_patch(state, node(state))
        if replan_router(state) != "replan":
            break

    for node in FINAL_NODES:
        state = _merge_patch(state, node(state))
    return state


def _latest_trace_event(state: TripState) -> dict | None:
    traces = state.get("trace", [])
    if not traces:
        return None
    trace = traces[-1]
    return {
        "type": "node",
        "node": trace.node,
        "status": trace.status,
        "elapsed_ms": trace.elapsed_ms,
        "note": trace.note,
    }


def stream_trip_graph_events(request: TripRequest) -> Iterator[dict]:
    """Yield graph progress events followed by the final itinerary payload."""
    state: TripState = {
        "request": request,
        "day_count": max((request.end_date - request.start_date).days + 1, 1),
        "replan_count": 0,
        "max_replan": TRIP_MAX_REPLAN,
        "token_usage": TokenUsage(),
        "errors": [],
        "trace": [],
    }

    for node in INITIAL_NODES:
        state = _merge_patch(state, node(state))
        event = _latest_trace_event(state)
        if event is not None:
            yield event

    while True:
        iteration_events = []
        for node in REPLAN_NODES:
            state = _merge_patch(state, node(state))
            event = _latest_trace_event(state)
            if event is not None:
                iteration_events.append(event)
        is_replanning = replan_router(state) == "replan"
        # 把 replan 标记打在 critic 决策节点的事件上（按节点名匹配，
        # 避免 REPLAN_NODES 末尾变动时标记位置漂移）。
        for event in iteration_events:
            if is_replanning and event.get("node") == "critic":
                event = {**event, "status": "replan"}
            yield event
        if not is_replanning:
            break

    for node in FINAL_NODES:
        state = _merge_patch(state, node(state))
        event = _latest_trace_event(state)
        if event is not None:
            yield event

    itinerary = state.get("itinerary")
    if itinerary is None:
        raise RuntimeError("Trip graph stream finished without an itinerary.")
    yield {
        "type": "done",
        "itinerary": itinerary.model_dump(mode="json"),
    }


def build_trip_graph():
    """Build and compile the LangGraph graph when the dependency is installed."""
    try:
        from langgraph.graph import END, StateGraph
    except ImportError:
        return None

    graph = StateGraph(TripState)
    graph.add_node("dispatch", dispatch_node)
    graph.add_node("spots", spot_search_node)
    graph.add_node("meals", meal_search_node)
    graph.add_node("transport", transport_search_node)
    graph.add_node("weather_lookup", weather_node)   # 改名避开 TripState.weather 字段冲突
    graph.add_node("schedule", schedule_node)
    graph.add_node("hotel", hotel_node)
    graph.add_node("budget", budget_check_node)
    graph.add_node("critic", critic_node)
    graph.add_node("summarize", summarize_node)

    graph.set_entry_point("dispatch")
    for node_name in ("spots", "meals", "transport", "weather_lookup"):
        graph.add_edge("dispatch", node_name)
        graph.add_edge(node_name, "schedule")
    graph.add_edge("schedule", "hotel")
    graph.add_edge("hotel", "budget")
    graph.add_edge("budget", "critic")
    graph.add_conditional_edges("critic", replan_router, {"replan": "schedule", "ok": "summarize"})
    graph.add_edge("summarize", END)
    return graph.compile()


def run_trip_graph(request: TripRequest) -> Itinerary:
    """Run trip orchestration and return the final itinerary."""
    initial_state: TripState = {
        "request": request,
        "day_count": max((request.end_date - request.start_date).days + 1, 1),
        "replan_count": 0,
        "max_replan": TRIP_MAX_REPLAN,
        "token_usage": TokenUsage(),
        "errors": [],
        "trace": [],
    }
    compiled_graph = build_trip_graph()
    if compiled_graph is not None:
        final_state = compiled_graph.invoke(initial_state)
    else:
        final_state = _run_local_graph(initial_state)

    itinerary = final_state.get("itinerary")
    if itinerary is None:
        raise RuntimeError("Trip graph finished without an itinerary.")
    return itinerary
