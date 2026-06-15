from pathlib import Path
import sys


CURRENT_FILE = Path(__file__).resolve()
BACKEND_DIR = CURRENT_FILE.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.graph import budget_router, run_trip_graph  # noqa: E402
from app.agents.state import BudgetReport  # noqa: E402
from app.models.schemas import TripRequest  # noqa: E402


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
