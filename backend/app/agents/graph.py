from __future__ import annotations

from collections.abc import Iterator

from app.agents.nodes.budget_check import budget_check_node
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
from app.models.schemas import Itinerary, TokenUsage, TripRequest


def _merge_patch(state: TripState, patch: dict) -> TripState:
    merged = dict(state)
    for key, value in patch.items():
        if key in {"trace", "errors"}:
            merged[key] = [*merged.get(key, []), *value]
        else:
            merged[key] = value
    return merged  # type: ignore[return-value]


def budget_router(state: TripState) -> str:
    report = state.get("budget_report")
    if not isinstance(report, BudgetReport):
        return "ok"
    max_replan = state.get("max_replan", TRIP_MAX_REPLAN)
    if report.over_budget and state.get("replan_count", 0) < max_replan:
        return "replan"
    return "ok"


def _run_local_graph(initial_state: TripState) -> TripState:
    state = initial_state
    for node in (dispatch_node, spot_search_node, meal_search_node, transport_search_node, weather_node):
        state = _merge_patch(state, node(state))

    while True:
        state = _merge_patch(state, schedule_node(state))
        state = _merge_patch(state, hotel_node(state))
        state = _merge_patch(state, budget_check_node(state))
        if budget_router(state) != "replan":
            break

    state = _merge_patch(state, summarize_node(state))
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

    # Streaming needs a yield point after each node. This runner intentionally
    # mirrors the graph topology while keeping event delivery straightforward.
    for node in (dispatch_node, spot_search_node, meal_search_node, transport_search_node, weather_node):
        state = _merge_patch(state, node(state))
        event = _latest_trace_event(state)
        if event is not None:
            yield event

    while True:
        for node in (schedule_node, hotel_node, budget_check_node):
            state = _merge_patch(state, node(state))
            event = _latest_trace_event(state)
            if event is not None:
                if event["node"] == "budget" and budget_router(state) == "replan":
                    event = {**event, "status": "replan"}
                yield event
        if budget_router(state) != "replan":
            break

    state = _merge_patch(state, summarize_node(state))
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
    graph.add_node("weather", weather_node)
    graph.add_node("schedule", schedule_node)
    graph.add_node("hotel", hotel_node)
    graph.add_node("budget", budget_check_node)
    graph.add_node("summarize", summarize_node)

    graph.set_entry_point("dispatch")
    for node_name in ("spots", "meals", "transport", "weather"):
        graph.add_edge("dispatch", node_name)
        graph.add_edge(node_name, "schedule")
    graph.add_edge("schedule", "hotel")
    graph.add_edge("hotel", "budget")
    graph.add_conditional_edges("budget", budget_router, {"replan": "schedule", "ok": "summarize"})
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
