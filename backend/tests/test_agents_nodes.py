from pathlib import Path
import sys
from datetime import date


CURRENT_FILE = Path(__file__).resolve()
BACKEND_DIR = CURRENT_FILE.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.nodes.spot_search import is_relevant_spot_place  # noqa: E402
from app.agents.nodes.spot_search import spot_search_node  # noqa: E402
from app.agents.nodes.meal_search import meal_search_node  # noqa: E402
from app.agents.nodes.summarize import _apply_narrator_result, summarize_node  # noqa: E402
from app.agents.nodes.dispatch import dispatch_node  # noqa: E402
from app.agents.state import NormalizedDemand, SpotCandidate, MealCandidate  # noqa: E402
from app.models.schemas import (  # noqa: E402
    CoordinatorResponse,
    CriticResponse,
    DayPlan,
    MealCuratorResponse,
    MealSelection,
    NarratorResponse,
    SpotCuratorResponse,
    SpotSelection,
    TokenUsage,
    TripRequest,
)


def build_trip_request() -> TripRequest:
    return TripRequest(
        destination="大理",
        start_date="2026-04-10",
        end_date="2026-04-12",
        travelers=2,
        budget=3200,
        preferences=["自然风景", "拍照"],
        pace="轻松",
    )


def test_spot_search_filters_non_tourism_places() -> None:
    """测试景点检索会过滤学校、机关等非游玩 POI。"""
    assert not is_relevant_spot_place({"name": "大理市公安局", "type": "政府机构及社会团体"})
    assert not is_relevant_spot_place({"name": "大理市大庄完小", "type": "科教文化服务;学校"})
    assert not is_relevant_spot_place({"name": "花与菌野生菌火锅(大理古城人民路店)", "type": "餐饮服务"})
    assert is_relevant_spot_place({"name": "崇圣寺三塔文化旅游区", "type": "风景名胜;风景名胜相关"})


def test_summarize_node_keeps_template_output_when_llm_disabled(monkeypatch) -> None:
    monkeypatch.setattr("app.agents.nodes.summarize.USE_LLM_AGENTS", False)
    patch = summarize_node(
        {
            "request": build_trip_request(),
            "day_plans": [
                DayPlan(day_index=1, date=date(2026, 4, 10), theme="规则主题", notes=["规则提示"])
            ],
            "budget_report": None,
            "token_usage": TokenUsage(),
            "trace": [],
            "errors": [],
        }
    )

    itinerary = patch["itinerary"]
    assert itinerary.summary
    assert itinerary.days[0].theme == "规则主题"
    assert itinerary.token_usage.total_tokens == 0


def test_summarize_node_degrades_when_llm_enabled_without_key(monkeypatch) -> None:
    monkeypatch.setattr("app.agents.nodes.summarize.USE_LLM_AGENTS", True)
    monkeypatch.setattr("app.llm.client.LLM_API_KEY", "", raising=False)

    patch = summarize_node(
        {
            "request": build_trip_request(),
            "day_plans": [
                DayPlan(day_index=1, date=date(2026, 4, 10), theme="规则主题", notes=["规则提示"])
            ],
            "budget_report": None,
            "token_usage": TokenUsage(),
            "trace": [],
            "errors": [],
        }
    )

    assert patch["itinerary"].days[0].theme == "规则主题"
    assert patch["itinerary"].token_usage.total_tokens == 0
    assert patch["trace"][0].node == "summarize"
    assert patch["trace"][0].status == "degraded"


def test_apply_narrator_result_filters_tips_and_appends_day_notes(monkeypatch) -> None:
    monkeypatch.setattr("app.agents.nodes.summarize.USE_LLM_AGENTS", False)
    itinerary = summarize_node(
        {
            "request": build_trip_request(),
            "day_plans": [
                DayPlan(day_index=1, date=date(2026, 4, 10), theme="规则主题", notes=["规则提示"])
            ],
            "budget_report": None,
            "token_usage": TokenUsage(),
            "trace": [],
            "errors": [],
        }
    )["itinerary"]

    _apply_narrator_result(
        itinerary,
        NarratorResponse(
            summary="这是一段游客可读的行程总结。",
            tips=["建议穿舒适鞋", "不要暴露 LLM 实现"],
            day_titles={"1": "慢游古城"},
            day_notes={"1": ["下午适合放慢节奏"]},
        ),
    )

    assert itinerary.summary == "这是一段游客可读的行程总结。"
    assert itinerary.tips == ["建议穿舒适鞋"]
    assert itinerary.days[0].theme == "慢游古城"
    assert itinerary.days[0].notes == ["规则提示", "下午适合放慢节奏"]


def _make_dispatch_state(token_usage=None):
    """构造 dispatch_node 的最小合法 state。"""
    return {
        "request": build_trip_request(),
        "token_usage": token_usage or TokenUsage(),
    }


def test_dispatch_node_uses_llm_strategy_when_enabled(monkeypatch) -> None:
    """LLM 启用时，dispatch 应写入 planning_strategy 并补强关键词与 token。"""
    fake_coordinator = CoordinatorResponse(
        strategy="以自然风景为主轴",
        daily_themes=["苍山洱海", "古城漫步"],
        pace_normalized="轻松",
        spot_keywords=["苍山", "洱海", "大理古城"],
        meal_keywords=["白族菜", "乳扇"],
        budget_hint={"hotel": 0.5},
        hard_constraints=[],
    )
    fake_tokens = {"prompt_tokens": 12, "completion_tokens": 34}

    monkeypatch.setattr("app.agents.nodes.dispatch.USE_LLM_AGENTS", True)
    monkeypatch.setattr(
        "app.agents.nodes.dispatch.call_structured_llm",
        lambda messages, model: (fake_coordinator, fake_tokens),
    )

    patch = dispatch_node(_make_dispatch_state(TokenUsage()))

    # planning_strategy 已写入
    assert patch["planning_strategy"] is not None
    assert patch["planning_strategy"].strategy == "以自然风景为主轴"

    # spot_keywords 含 LLM 关键词且仍含原规则关键词（"大理" 为目的地）
    spot_kws = patch["normalized"].spot_keywords
    assert "苍山" in spot_kws
    assert "洱海" in spot_kws
    assert "大理" in spot_kws  # 原规则关键词

    # meal_keywords 来自 LLM
    assert patch["normalized"].meal_keywords == ["白族菜", "乳扇"]

    # token 已累加
    usage = patch["token_usage"]
    assert usage.planner_prompt_tokens == 12
    assert usage.planner_completion_tokens == 34


def test_dispatch_node_degrades_when_llm_unavailable(monkeypatch) -> None:
    """LLM 不可用时，dispatch 应静默降级，返回规则 normalized，不抛异常。"""
    from app.llm.structured import LLMUnavailable

    monkeypatch.setattr("app.agents.nodes.dispatch.USE_LLM_AGENTS", True)
    monkeypatch.setattr(
        "app.agents.nodes.dispatch.call_structured_llm",
        lambda messages, model: (_ for _ in ()).throw(LLMUnavailable("fake error")),
    )

    patch = dispatch_node(_make_dispatch_state())

    # 不应抛异常，返回规则 normalized
    assert patch["normalized"] is not None
    assert "大理" in patch["normalized"].spot_keywords
    # meal_keywords 保持空（规则侧不填）
    assert patch["normalized"].meal_keywords == []
    # planning_strategy 不存在（降级时不设置）
    assert "planning_strategy" not in patch


# ──────────────────────────────────────────────────────────────────────────────
# Task 4: SpotCurator / MealCurator 测试
# ──────────────────────────────────────────────────────────────────────────────


def _make_spot_state():
    """构造 spot_search_node 的最小合法 state。"""
    return {
        "request": TripRequest(
            destination="大理",
            start_date="2026-04-10",
            end_date="2026-04-11",
            travelers=2,
            budget=3200,
        ),
        "day_count": 2,
        "normalized": NormalizedDemand(
            city_canonical="大理",
            spot_keywords=["大理 景点"],
            meal_keywords=[],
        ),
        "token_usage": TokenUsage(),
    }


def _make_meal_state():
    """构造 meal_search_node 的最小合法 state。"""
    return {
        "request": TripRequest(
            destination="大理",
            start_date="2026-04-10",
            end_date="2026-04-11",
            travelers=2,
            budget=3200,
        ),
        "day_count": 2,
        "normalized": NormalizedDemand(
            city_canonical="大理",
            spot_keywords=[],
            meal_keywords=["白族菜"],
            dietary_norm=[],
        ),
        "token_usage": TokenUsage(),
    }


def test_spot_curator_rejects_names_outside_candidate_pool(monkeypatch) -> None:
    """SpotCurator 只接受候选池内名称，池外名称必须被过滤掉；token 必须累加。"""
    import app.agents.nodes.spot_search as spot_mod

    # 固定候选池：只含 "大理古城" 和 "喜洲古镇"
    fake_candidates = [
        SpotCandidate(name="大理古城", latitude=25.69, longitude=100.16, category="古城街区"),
        SpotCandidate(name="喜洲古镇", latitude=25.85, longitude=100.13, category="古镇"),
    ]

    # LLM 返回：选中一个池内名 + 一个池外名
    fake_curator = SpotCuratorResponse(
        selected=[
            SpotSelection(name="大理古城", reason="经典", is_indoor=False, suggested_hours=2.0),
            SpotSelection(name="虚构景点", reason="编造", is_indoor=False, suggested_hours=1.0),
        ],
        rejected_names=["喜洲古镇"],
    )
    fake_tokens = {"prompt_tokens": 10, "completion_tokens": 5}

    monkeypatch.setattr(spot_mod, "USE_LLM_AGENTS", True)
    monkeypatch.setattr(spot_mod, "search_places", lambda *a, **kw: [
        {"name": "大理古城", "latitude": 25.69, "longitude": 100.16, "type": "古城街区"},
        {"name": "喜洲古镇", "latitude": 25.85, "longitude": 100.13, "type": "古镇"},
    ])
    monkeypatch.setattr(spot_mod, "call_structured_llm", lambda msgs, model: (fake_curator, fake_tokens))

    patch = spot_search_node(_make_spot_state())

    candidates = patch["spot_candidates"]
    names = [c.name for c in candidates]

    # 池外名必须被拒绝
    assert "虚构景点" not in names, "池外名称不应出现在候选中"
    # 池内被选中的名称应出现
    assert "大理古城" in names
    # token 必须累加
    usage = patch.get("token_usage")
    assert usage is not None
    assert usage.planner_prompt_tokens >= 10
    assert usage.planner_completion_tokens >= 5


def test_meal_curator_uses_bocha_notes_when_available(monkeypatch) -> None:
    """MealCurator 被选中餐厅的 notes 应含博查招牌菜信息且含高德评分；rating 必须来自高德。"""
    import app.agents.nodes.meal_search as meal_mod

    # 高德候选带 rating
    fake_amap_places = [
        {
            "name": "大理老字号砂锅鱼",
            "latitude": 25.69,
            "longitude": 100.16,
            "type": "云南菜",
            "rating": "4.6",
            "avg_cost": "68",
            "address": "大理古城人民路",
        }
    ]
    # LLM 选中池内名称
    fake_curator = MealCuratorResponse(
        selected=[
            MealSelection(
                name="大理老字号砂锅鱼",
                cuisine="云南菜",
                rating=3.0,  # LLM 编的评分，不应被采用
                signature_dishes=["砂锅鱼"],
                dietary_ok=True,
            )
        ],
        rejected_names=[],
    )
    fake_tokens = {"prompt_tokens": 8, "completion_tokens": 4}
    # 博查返回招牌菜片段
    fake_web_hits = [
        {"title": "大理老字号砂锅鱼", "url": "http://example.com", "snippet": "招牌菜：砂锅鱼、炸洋芋；评价：汤鲜味美"}
    ]

    monkeypatch.setattr(meal_mod, "USE_LLM_AGENTS", True)
    monkeypatch.setattr(meal_mod, "search_places", lambda *a, **kw: fake_amap_places)
    monkeypatch.setattr(meal_mod, "call_structured_llm", lambda msgs, model: (fake_curator, fake_tokens))
    monkeypatch.setattr(meal_mod, "search_web", lambda *a, **kw: fake_web_hits)

    patch = meal_search_node(_make_meal_state())

    candidates = patch["meal_candidates"]
    assert len(candidates) >= 1
    sel = next((c for c in candidates if c.name == "大理老字号砂锅鱼"), None)
    assert sel is not None

    # rating 必须来自高德（4.6），不能用 LLM 的 3.0
    assert sel.rating is not None
    assert abs(sel.rating - 4.6) < 0.01, f"rating 应为高德的 4.6，实际={sel.rating}"

    # notes 应含博查招牌菜/摘要信息
    assert sel.notes is not None
    assert "砂锅鱼" in sel.notes or "招牌" in sel.notes or "汤鲜味美" in sel.notes

    # token 已累加
    usage = patch.get("token_usage")
    assert usage is not None
    assert usage.planner_prompt_tokens >= 8


def test_meal_curator_bocha_failure_does_not_block(monkeypatch) -> None:
    """博查返回 [] 时流程不阻断，仍能正常产出候选，notes 可含高德评分。"""
    import app.agents.nodes.meal_search as meal_mod

    fake_amap_places = [
        {
            "name": "喜洲粑粑摊",
            "latitude": 25.85,
            "longitude": 100.13,
            "type": "小吃快餐",
            "rating": "4.2",
            "avg_cost": "20",
            "address": "喜洲",
        }
    ]
    fake_curator = MealCuratorResponse(
        selected=[MealSelection(name="喜洲粑粑摊", cuisine="小吃快餐", dietary_ok=True)],
        rejected_names=[],
    )
    fake_tokens = {"prompt_tokens": 6, "completion_tokens": 3}

    monkeypatch.setattr(meal_mod, "USE_LLM_AGENTS", True)
    monkeypatch.setattr(meal_mod, "search_places", lambda *a, **kw: fake_amap_places)
    monkeypatch.setattr(meal_mod, "call_structured_llm", lambda msgs, model: (fake_curator, fake_tokens))
    # 博查返回空列表（降级场景）
    monkeypatch.setattr(meal_mod, "search_web", lambda *a, **kw: [])

    patch = meal_search_node(_make_meal_state())

    candidates = patch["meal_candidates"]
    assert len(candidates) >= 1
    sel = next((c for c in candidates if c.name == "喜洲粑粑摊"), None)
    assert sel is not None
    # 流程不中断，rating 来自高德
    assert sel.rating is not None
    assert abs(sel.rating - 4.2) < 0.01


def test_spot_curator_tokens_cumulated_when_all_selected_outside_pool(monkeypatch) -> None:
    """LLM 返回全部是池外名时：候选回退为规则高德候选，token 仍被累加。"""
    import app.agents.nodes.spot_search as spot_mod

    fake_amap_places = [
        {"name": "大理古城", "latitude": 25.69, "longitude": 100.16, "type": "古城街区"},
        {"name": "喜洲古镇", "latitude": 25.85, "longitude": 100.13, "type": "古镇"},
    ]
    # LLM 只返回池外名称
    fake_curator = SpotCuratorResponse(
        selected=[
            SpotSelection(name="虚构景点A", reason="编造", is_indoor=False, suggested_hours=1.0),
            SpotSelection(name="虚构景点B", reason="编造", is_indoor=False, suggested_hours=1.0),
        ],
        rejected_names=["大理古城", "喜洲古镇"],
    )
    fake_tokens = {"prompt_tokens": 20, "completion_tokens": 8}

    monkeypatch.setattr(spot_mod, "USE_LLM_AGENTS", True)
    monkeypatch.setattr(spot_mod, "search_places", lambda *a, **kw: fake_amap_places)
    monkeypatch.setattr(spot_mod, "call_structured_llm", lambda msgs, model: (fake_curator, fake_tokens))

    patch = spot_search_node(_make_spot_state())

    candidates = patch["spot_candidates"]
    names = [c.name for c in candidates]

    # (a) 池外名不出现，规则高德候选保留
    assert "虚构景点A" not in names
    assert "虚构景点B" not in names
    assert "大理古城" in names
    assert "喜洲古镇" in names

    # (b) token 仍被累加（即使 picked_ordered 为空）
    usage = patch.get("token_usage")
    assert usage is not None, "token_usage 不应为 None"
    assert usage.planner_prompt_tokens >= 20
    assert usage.planner_completion_tokens >= 8


# ──────────────────────────────────────────────────────────────────────────────
# Task 5: Critic 节点 + Schedule 消费 hints 测试
# ──────────────────────────────────────────────────────────────────────────────


def _make_critic_state(replan_count=0, max_replan=2, revise_hints=None):
    """构造 critic_node 的最小合法 state。"""
    from app.agents.state import SpotCandidate, MealCandidate, WeatherContext
    return {
        "request": TripRequest(
            destination="大理",
            start_date="2026-04-10",
            end_date="2026-04-12",
            travelers=2,
            budget=3200,
            preferences=["自然风景"],
            pace="适中",
        ),
        "day_plans": [
            DayPlan(
                day_index=1,
                date=date(2026, 4, 10),
                theme="苍山洱海",
                notes=["天气晴朗"],
            ),
            DayPlan(
                day_index=2,
                date=date(2026, 4, 11),
                theme="古城漫步",
                notes=["建议步行"],
            ),
        ],
        "budget_report": None,
        "replan_count": replan_count,
        "max_replan": max_replan,
        "token_usage": TokenUsage(),
        "revise_hints": revise_hints or [],
        "trace": [],
        "errors": [],
    }


def test_critic_sets_revise_hints_and_schedule_consumes_them(monkeypatch) -> None:
    """
    第一段：critic_node 正常 revise 路径 → revise_hints 非空、replan_count 自增、token 累加。
    第二段：schedule_node 消费 revise_hints=["减少景点"] → 每天景点数降到 1。
    """
    import app.agents.nodes.critic as critic_mod
    from app.agents.nodes.critic import critic_node
    from app.agents.nodes.schedule import schedule_node
    from app.agents.state import SpotCandidate, MealCandidate, WeatherContext

    # ── 第一段：critic 正常 revise ──────────────────────────
    fake_report = CriticResponse(
        verdict="revise",
        score=0.5,
        issues=["景点过多"],
        revise_hints=["减少景点"],
    )
    fake_tokens = {"input_tokens": 10, "output_tokens": 5}

    monkeypatch.setattr(critic_mod, "USE_LLM_AGENTS", True)
    monkeypatch.setattr(
        critic_mod,
        "call_structured_llm",
        lambda messages, model: (fake_report, fake_tokens),
    )

    state = _make_critic_state(replan_count=0, max_replan=2)
    patch = critic_node(state)

    assert patch["revise_hints"], "revise 路径下 revise_hints 应非空"
    assert patch["critic_report"].verdict == "revise"
    assert patch["replan_count"] == 1, "revise 未达上限时 replan_count 应自增 1"
    usage = patch.get("token_usage")
    assert usage is not None
    assert usage.planner_prompt_tokens + usage.planner_completion_tokens > 0, "token 应已累加"

    # ── 第二段：schedule 消费 hints → 景点数降到 1 ──────────
    spot_candidates = [
        SpotCandidate(name=f"景点{i}", latitude=25.6 + i * 0.01, longitude=100.1 + i * 0.01,
                      is_indoor=False, ticket_estimate=30)
        for i in range(10)
    ]
    meal_candidates = [
        MealCandidate(name="餐厅A", latitude=25.69, longitude=100.16, avg_price=50)
    ]
    schedule_state = {
        "request": TripRequest(
            destination="大理",
            start_date="2026-04-10",
            end_date="2026-04-11",
            travelers=2,
            budget=3200,
            preferences=[],
            pace="适中",
        ),
        "day_count": 2,
        "spot_candidates": spot_candidates,
        "meal_candidates": meal_candidates,
        "weather": WeatherContext(days=[]),
        "transport_options": None,
        "replan_count": 1,
        "revise_hints": ["减少景点"],
        "token_usage": TokenUsage(),
    }
    schedule_patch = schedule_node(schedule_state)
    day_plans = schedule_patch["day_plans"]
    for day in day_plans:
        assert len(day.spots) <= 1, f"有 revise_hints=['减少景点'] 时每天景点数应 <=1，实际={len(day.spots)}"


def test_critic_force_accept_at_max_replan(monkeypatch) -> None:
    """replan_count >= max_replan 时，即使 LLM 返回 revise，critic 应强制接受，不自增 replan_count。"""
    import app.agents.nodes.critic as critic_mod
    from app.agents.nodes.critic import critic_node

    fake_report = CriticResponse(
        verdict="revise",
        score=0.4,
        issues=["还需改进"],
        revise_hints=["减少景点"],
    )
    fake_tokens = {"input_tokens": 8, "output_tokens": 4}

    monkeypatch.setattr(critic_mod, "USE_LLM_AGENTS", True)
    monkeypatch.setattr(
        critic_mod,
        "call_structured_llm",
        lambda messages, model: (fake_report, fake_tokens),
    )

    state = _make_critic_state(replan_count=2, max_replan=2)
    patch = critic_node(state)

    assert patch["revise_hints"] == [], "达上限时强制接受，revise_hints 应为 []"
    assert patch["critic_report"].verdict == "accept", "达上限时强制接受，verdict 应为 accept"
    assert "replan_count" not in patch, "达上限时强制接受路径不应写入 replan_count"


def test_critic_degraded_when_llm_disabled(monkeypatch) -> None:
    """USE_LLM_AGENTS=False 时，critic_node 直接返回等价接受，不抛异常，不触发回环。"""
    import app.agents.nodes.critic as critic_mod
    from app.agents.nodes.critic import critic_node

    monkeypatch.setattr(critic_mod, "USE_LLM_AGENTS", False)

    state = _make_critic_state(replan_count=0, max_replan=2)
    patch = critic_node(state)  # 不应抛异常

    assert patch["revise_hints"] == [], "降级时 revise_hints 应为空"
    assert patch["critic_report"].verdict == "accept", "降级时 verdict 应为 accept"
    assert patch.get("replan_count", 0) == 0, "降级时不应自增 replan_count"


def test_critic_no_double_increment_when_budget_over(monkeypatch) -> None:
    """[Important #2 TDD] budget 本轮已因超支自增 replan_count 时，
    critic revise 不应再次自增（一轮最多消耗 1 次回环额度）。
    revise_hints 仍应非空（供下轮 schedule 消费）。
    """
    import app.agents.nodes.critic as critic_mod
    from app.agents.nodes.critic import critic_node
    from app.agents.state import BudgetReport

    fake_report = CriticResponse(
        verdict="revise",
        score=0.5,
        issues=["行程不够紧凑"],
        revise_hints=["增加景点"],
    )
    fake_tokens = {"input_tokens": 10, "output_tokens": 5}

    monkeypatch.setattr(critic_mod, "USE_LLM_AGENTS", True)
    monkeypatch.setattr(critic_mod, "call_structured_llm",
                        lambda messages, model: (fake_report, fake_tokens))

    # budget_report.over_budget=True 表示本轮 budget_check 已自增 replan_count
    # 模拟 budget_check 已把 replan_count 从 0 增到 1
    state = _make_critic_state(replan_count=1, max_replan=3)
    state = dict(state)
    state["budget_report"] = BudgetReport(
        total=9999,
        over_budget=True,
        missing_items=[],
        passed=False,
    )
    patch = critic_node(state)

    # critic 不应再自增（budget 本轮已经增了一次）
    assert "replan_count" not in patch or patch.get("replan_count") == 1, (
        f"budget 本轮已自增时 critic 不应再自增，patch.replan_count={patch.get('replan_count')}"
    )
    # revise_hints 应仍非空（critic 仍提供反馈供下轮消费）
    assert patch.get("revise_hints"), "budget 超支时 critic 虽不自增，revise_hints 仍应非空"


def test_critic_does_increment_when_budget_ok(monkeypatch) -> None:
    """[Important #2 TDD] budget 本轮不超支时，critic revise 应正常自增 replan_count +1。"""
    import app.agents.nodes.critic as critic_mod
    from app.agents.nodes.critic import critic_node
    from app.agents.state import BudgetReport

    fake_report = CriticResponse(
        verdict="revise",
        score=0.6,
        issues=["餐饮选择单一"],
        revise_hints=["增加餐饮多样性"],
    )
    fake_tokens = {"input_tokens": 8, "output_tokens": 4}

    monkeypatch.setattr(critic_mod, "USE_LLM_AGENTS", True)
    monkeypatch.setattr(critic_mod, "call_structured_llm",
                        lambda messages, model: (fake_report, fake_tokens))

    # budget_report.over_budget=False：budget 本轮没有自增
    state = _make_critic_state(replan_count=0, max_replan=3)
    state = dict(state)
    state["budget_report"] = BudgetReport(
        total=1500,
        over_budget=False,
        missing_items=[],
        passed=True,
    )
    patch = critic_node(state)

    # critic 应正常自增 +1
    assert patch.get("replan_count") == 1, (
        f"budget 不超支时 critic revise 应自增 replan_count=1，实际={patch.get('replan_count')}"
    )
    assert patch.get("revise_hints"), "critic revise 路径 revise_hints 应非空"


def test_critic_degraded_when_llm_unavailable(monkeypatch) -> None:
    """LLM 可用但 call_structured_llm 抛 LLMUnavailable 时，critic_node 应走只读接受降级：
    不抛异常、verdict==accept、revise_hints==[]、patch 不含 replan_count（不触发回环）。
    """
    import app.agents.nodes.critic as critic_mod
    from app.agents.nodes.critic import critic_node
    from app.llm.structured import LLMUnavailable

    def _raise_llm_unavailable(messages, model):
        raise LLMUnavailable("模拟超时")

    monkeypatch.setattr(critic_mod, "USE_LLM_AGENTS", True)
    monkeypatch.setattr(critic_mod, "call_structured_llm", _raise_llm_unavailable)

    state = _make_critic_state(replan_count=0, max_replan=2)
    patch = critic_node(state)  # 不应抛异常

    assert patch["critic_report"].verdict == "accept", "LLMUnavailable 降级时 verdict 应为 accept"
    assert patch["revise_hints"] == [], "LLMUnavailable 降级时 revise_hints 应为 []"
    assert "replan_count" not in patch, "LLMUnavailable 降级时不应写入 replan_count，避免触发回环"
