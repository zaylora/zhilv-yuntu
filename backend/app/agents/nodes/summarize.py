from __future__ import annotations

from app.agents.monitoring import monitored_node
from app.agents.nodes.rules import clean_user_tips
from app.agents.state import TripState
from app.models.schemas import BudgetBreakdown, Itinerary, TokenUsage


def _budget_breakdown_from_report(state: TripState) -> BudgetBreakdown:
    report = state.get("budget_report")
    values = report.breakdown if report is not None else {}
    return BudgetBreakdown(
        transport=values.get("transport", 0.0),
        hotel=values.get("hotel", 0.0),
        meals=values.get("meals", 0.0),
        tickets=values.get("tickets", 0.0),
        other=values.get("other", 0.0),
        total=values.get("total", 0.0),
    )


@monitored_node("summarize")
def summarize_node(state: TripState) -> dict:
    request = state["request"]
    days = state.get("day_plans", [])
    preference_text = "、".join(request.preferences) if request.preferences else "常规旅行体验"
    summary = f"这是一份为 {request.destination} 生成的 {len(days)} 日行程，偏好重点为：{preference_text}。"
    tips = clean_user_tips(
        [
            f"建议根据{request.destination}当天实时天气准备雨具或薄外套。",
            "古镇、生态廊道和石板路更适合慢慢走，鞋子尽量选择舒适防滑的款式。",
            "热门景点建议错峰出发，给拍照、用餐和交通预留更从容的缓冲时间。",
        ],
        request.destination,
    )
    trace_notes = [
        f"graph_trace:{trace.node}:{trace.status}:{trace.elapsed_ms}ms"
        for trace in state.get("trace", [])
    ]
    errors = [f"graph_error:{error}" for error in state.get("errors", [])]
    source_notes = ["Itinerary is assembled by LangGraph trip orchestration."]
    source_notes.extend(trace_notes)
    source_notes.extend(errors)

    breakdown = _budget_breakdown_from_report(state)
    itinerary = Itinerary(
        trip_id=f"trip_{request.destination}_{request.start_date.isoformat()}",
        destination=request.destination,
        summary=summary,
        days=days,
        estimated_budget=breakdown.total,
        budget_breakdown=breakdown,
        tips=tips,
        source_notes=source_notes,
        token_usage=state.get("token_usage") or TokenUsage(),
    )

    return {
        "itinerary": itinerary,
        "_note": f"trace={len(trace_notes)}",
    }
