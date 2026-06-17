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


def test_run_trip_graph_accumulates_tokens_with_llm_enabled(monkeypatch) -> None:
    """[Critical #1 TDD] 并行 fan-out 节点同时写 token_usage 不应抛 InvalidUpdateError，
    最终 itinerary.token_usage.planner_prompt_tokens 应等于各节点增量之和（无重复计数）。

    设计约束（确保可控的 token 期望值）：
    1. search_places 返回 1 个合法 POI，让 amap_candidates 非空，确保 spots/meals LLM 被调用。
    2. request.budget 设置足够大（999999），确保 budget_check 不触发超支回环，
       critic 只被调用一次（accept），不产生非预期的额外 token 消耗。
    """
    import app.agents.nodes.dispatch as dispatch_mod
    import app.agents.nodes.spot_search as spot_mod
    import app.agents.nodes.meal_search as meal_mod
    import app.agents.nodes.critic as critic_mod
    import app.agents.nodes.summarize as summarize_mod

    from app.models.schemas import (
        CoordinatorResponse,
        CriticResponse,
        MealCuratorResponse,
        NarratorResponse,
        SpotCuratorResponse,
        TripRequest as TR,
    )

    # ── 给每个节点 patch USE_LLM_AGENTS=True ──────────────────────────────────
    monkeypatch.setattr(dispatch_mod, "USE_LLM_AGENTS", True)
    monkeypatch.setattr(spot_mod,     "USE_LLM_AGENTS", True)
    monkeypatch.setattr(meal_mod,     "USE_LLM_AGENTS", True)
    monkeypatch.setattr(critic_mod,   "USE_LLM_AGENTS", True)
    monkeypatch.setattr(summarize_mod,"USE_LLM_AGENTS", True)

    # ── 各节点的 fake LLM 返回（每个节点 prompt_tokens 各不相同，方便精确计算总量） ──
    DISPATCH_PROMPT = 100
    SPOT_PROMPT     = 200
    MEAL_PROMPT     = 300
    CRITIC_PROMPT   = 50
    NARRATOR_PROMPT = 75
    COMPLETION = 10  # 所有节点 completion_tokens 统一为 10

    # 仅当 budget 足够大（不触发超支回环）且各 LLM 都被调用时，期望值才成立：
    EXPECTED_TOTAL_PROMPT = DISPATCH_PROMPT + SPOT_PROMPT + MEAL_PROMPT + CRITIC_PROMPT + NARRATOR_PROMPT

    def _fake_dispatch_llm(messages, model):
        return (
            CoordinatorResponse(
                strategy="测试策略",
                daily_themes=["主题1", "主题2", "主题3"],
                pace_normalized="轻松",
                spot_keywords=["大理古城"],
                meal_keywords=["白族菜"],
            ),
            {"prompt_tokens": DISPATCH_PROMPT, "completion_tokens": COMPLETION},
        )

    def _fake_spot_llm(messages, model):
        return (
            SpotCuratorResponse(selected=[], rejected_names=[]),
            {"prompt_tokens": SPOT_PROMPT, "completion_tokens": COMPLETION},
        )

    def _fake_meal_llm(messages, model):
        return (
            MealCuratorResponse(selected=[], rejected_names=[]),
            {"prompt_tokens": MEAL_PROMPT, "completion_tokens": COMPLETION},
        )

    def _fake_critic_llm(messages, model):
        return (
            CriticResponse(verdict="accept"),
            {"prompt_tokens": CRITIC_PROMPT, "completion_tokens": COMPLETION},
        )

    def _fake_narrator_llm(messages, model):
        return (
            NarratorResponse(
                summary="测试行程总结",
                tips=["建议穿舒适鞋"],
                day_titles={},
                day_notes={},
            ),
            {"prompt_tokens": NARRATOR_PROMPT, "completion_tokens": COMPLETION},
        )

    monkeypatch.setattr(dispatch_mod,  "call_structured_llm", _fake_dispatch_llm)
    monkeypatch.setattr(spot_mod,      "call_structured_llm", _fake_spot_llm)
    monkeypatch.setattr(meal_mod,      "call_structured_llm", _fake_meal_llm)
    monkeypatch.setattr(critic_mod,    "call_structured_llm", _fake_critic_llm)
    monkeypatch.setattr(summarize_mod, "call_structured_llm", _fake_narrator_llm)

    # ── patch 外部 IO ────────────────────────────────────────────────────────
    # search_places 返回 1 个合法景点 POI（pass is_relevant_spot_place），
    # 让 amap_candidates 非空，确保 SpotCurator / MealCurator LLM 实际被调用。
    FAKE_SPOT = {"name": "大理古城", "latitude": 25.69, "longitude": 100.16, "type": "风景名胜"}
    FAKE_MEAL = {"name": "砂锅鱼馆", "latitude": 25.69, "longitude": 100.16, "type": "餐饮服务"}
    monkeypatch.setattr(spot_mod, "search_places", lambda *a, **kw: [FAKE_SPOT])
    monkeypatch.setattr(meal_mod, "search_places", lambda *a, **kw: [FAKE_MEAL])
    monkeypatch.setattr(meal_mod, "search_web",    lambda *a, **kw: [])

    # ── 使用极大预算，确保 budget_check 不触发超支回环（critic 只被调用一次） ──
    request = TR(
        destination="大理",
        start_date="2026-04-10",
        end_date="2026-04-12",
        travelers=2,
        budget=999999,   # 足够大，绝不超支
        preferences=["自然风景"],
        pace="轻松",
    )

    # ── 执行（走 LangGraph 编译图；若 langgraph 未安装则走 local graph） ────────
    itinerary = run_trip_graph(request)

    # (a) 不抛 InvalidUpdateError（能走到这里即通过）
    assert itinerary is not None, "run_trip_graph 应返回 itinerary"

    # (b) token_usage 非 None 且 planner_prompt_tokens > 0
    usage = itinerary.token_usage
    assert usage is not None, "itinerary.token_usage 不应为 None"
    assert usage.planner_prompt_tokens > 0, (
        f"planner_prompt_tokens 应 > 0，实际={usage.planner_prompt_tokens}"
    )

    # (c) 无重复计数：总量应等于各节点增量之和
    assert usage.planner_prompt_tokens == EXPECTED_TOTAL_PROMPT, (
        f"planner_prompt_tokens 期望={EXPECTED_TOTAL_PROMPT}（各节点增量之和，无重复计数），"
        f"实际={usage.planner_prompt_tokens}\n"
        f"  若 > 期望则说明节点返回了全量而非增量（重复计数）；\n"
        f"  若 < 期望则说明某些节点 LLM 未被调用（可能 amap_candidates 为空或 budget 触发额外回环）"
    )


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
