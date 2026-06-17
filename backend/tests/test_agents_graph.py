from pathlib import Path
import sys


CURRENT_FILE = Path(__file__).resolve()
BACKEND_DIR = CURRENT_FILE.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.graph import (  # noqa: E402
    FINAL_NODES,
    INITIAL_NODES,
    REPLAN_NODES,
    _run_local_graph,
    budget_router,
    run_trip_graph,
    stream_trip_graph_events,
)
from app.agents.state import BudgetReport  # noqa: E402
from app.models.schemas import TokenUsage, TripRequest  # noqa: E402


def build_trip_request() -> TripRequest:
    return TripRequest(
        destination="大理",
        start_date="2026-04-10",
        end_date="2026-04-12",
        travelers=2,
        budget=3200,
        preferences=["自然风景", "拍照", "美食"],
        pace="轻松",
        dietary_preferences=["少辣"],
        hotel_level="舒适型",
        special_notes="不想太早起床，希望安排一个适合看日落的地点",
    )


def test_budget_router_replans_only_when_over_budget_under_limit() -> None:
    """测试预算路由只在超支且未到上限时回到 schedule。"""
    over_budget = BudgetReport(
        total=3600,
        over_budget=True,
        missing_items=[],
        passed=False,
    )

    assert budget_router({"budget_report": over_budget, "replan_count": 0, "max_replan": 2}) == "replan"
    assert budget_router({"budget_report": over_budget, "replan_count": 2, "max_replan": 2}) == "ok"

    in_budget = BudgetReport(
        total=3000,
        over_budget=False,
        missing_items=[],
        passed=True,
    )

    assert budget_router({"budget_report": in_budget, "replan_count": 0, "max_replan": 2}) == "ok"


def test_run_trip_graph_returns_itinerary_with_trace_and_complete_days() -> None:
    """测试 graph 入口会返回完整 itinerary，并保留节点 trace。"""
    itinerary = run_trip_graph(build_trip_request())

    assert itinerary.destination == "大理"
    assert len(itinerary.days) == 3
    assert itinerary.budget_breakdown.total >= 0
    assert itinerary.source_notes
    assert any(note.startswith("graph_trace:") for note in itinerary.source_notes)


def test_stream_and_run_share_same_node_order() -> None:
    """collect 与 stream 两条路径引用同一组 PHASE 常量，节点顺序一致。

    校验点：
    1. 两路径「首次出现」的节点顺序一致（去重保序，允许回环导致重复）。
    2. "critic" 出现在序列中，且位于 "budget" 之后、"summarize" 之前。
    3. REPLAN_NODES 包含 critic_node（常量层面验证）。
    """
    request = build_trip_request()

    # ── collect 路径：从 trace 取节点顺序 ────────────────────────
    initial_state = {
        "request": request,
        "day_count": max((request.end_date - request.start_date).days + 1, 1),
        "replan_count": 0,
        "max_replan": 2,
        "token_usage": TokenUsage(),
        "errors": [],
        "trace": [],
    }
    final_state = _run_local_graph(initial_state)
    collect_nodes_full = [t.node for t in final_state.get("trace", [])]

    # ── stream 路径：取 type=="node" 的 node 字段 ────────────────
    stream_nodes_full = [
        e["node"]
        for e in stream_trip_graph_events(request)
        if e.get("type") == "node"
    ]

    # 去重保序：取每个节点「首次出现」的顺序（即跳过回环重复的节点）
    def dedup_preserving_order(nodes: list[str]) -> list[str]:
        seen: set[str] = set()
        result = []
        for n in nodes:
            if n not in seen:
                seen.add(n)
                result.append(n)
        return result

    collect_nodes = dedup_preserving_order(collect_nodes_full)
    stream_nodes = dedup_preserving_order(stream_nodes_full)

    # 两路径去重后节点顺序必须一致（同一组 PHASE 常量驱动）
    assert collect_nodes == stream_nodes, (
        f"collect 与 stream 去重节点顺序不一致\ncollect: {collect_nodes}\nstream:  {stream_nodes}"
    )

    # critic 必须出现在序列中（证明 critic 进入 REPLAN 回环）
    assert "critic" in collect_nodes, f"critic 不在节点序列中: {collect_nodes}"

    # critic 在 budget 之后、summarize 之前
    budget_idx = collect_nodes.index("budget")
    critic_idx = collect_nodes.index("critic")
    summarize_idx = collect_nodes.index("summarize")
    assert budget_idx < critic_idx < summarize_idx, (
        f"节点顺序异常: budget={budget_idx}, critic={critic_idx}, summarize={summarize_idx}"
    )

    # REPLAN_NODES 中包含 critic_node（常量层面验证）
    from app.agents.nodes.critic import critic_node  # noqa: PLC0415
    assert critic_node in REPLAN_NODES, "critic_node 不在 REPLAN_NODES 常量中"

    # INITIAL/REPLAN/FINAL 常量节点数量校验
    assert len(REPLAN_NODES) == 4, f"REPLAN_NODES 应有 4 个节点: {REPLAN_NODES}"
    assert len(INITIAL_NODES) == 5, f"INITIAL_NODES 应有 5 个节点: {INITIAL_NODES}"
    assert len(FINAL_NODES) == 1, f"FINAL_NODES 应有 1 个节点: {FINAL_NODES}"
