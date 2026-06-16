from pathlib import Path
import sys
from datetime import date


CURRENT_FILE = Path(__file__).resolve()
BACKEND_DIR = CURRENT_FILE.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.nodes.spot_search import is_relevant_spot_place  # noqa: E402
from app.agents.nodes.summarize import _apply_narrator_result, summarize_node  # noqa: E402
from app.models.schemas import DayPlan, NarratorResponse, TokenUsage, TripRequest  # noqa: E402


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
