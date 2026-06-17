from __future__ import annotations

import json

from app.agents.monitoring import monitored_node
from app.agents.nodes.rules import clean_user_tips
from app.agents.state import TripState
from app.config import USE_LLM_AGENTS
from app.llm.registry import NARRATOR_SYSTEM_PROMPT
from app.llm.structured import LLMUnavailable, call_structured_llm
from app.models.schemas import BudgetBreakdown, Itinerary, NarratorResponse, TokenUsage


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


def _build_template_itinerary(state: TripState) -> Itinerary:
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

    return itinerary


def _build_narrator_messages(state: TripState) -> list[dict[str, str]]:
    request = state["request"]
    days = state.get("day_plans", [])
    payload = {
        "destination": request.destination,
        "pace": request.pace,
        "preferences": request.preferences,
        "dietary_preferences": request.dietary_preferences,
        "hotel_level": request.hotel_level,
        "special_notes": request.special_notes,
        "days": [
            {
                "day_index": day.day_index,
                "date": day.date.isoformat() if day.date else None,
                "theme": day.theme,
                "spots": [spot.name for spot in day.spots],
                "meals": [meal.name for meal in day.meals],
                "notes": day.notes,
            }
            for day in days
        ],
    }
    return [
        {"role": "system", "content": NARRATOR_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _apply_narrator_result(itinerary: Itinerary, result: NarratorResponse) -> None:
    itinerary.summary = result.summary
    itinerary.tips = clean_user_tips(result.tips, itinerary.destination)
    for day in itinerary.days:
        key = str(day.day_index)
        title = result.day_titles.get(key)
        if title:
            day.theme = title
        notes = result.day_notes.get(key, [])
        day.notes.extend(note for note in notes if note)


@monitored_node("summarize")
def summarize_node(state: TripState) -> dict:
    itinerary = _build_template_itinerary(state)
    trace_note = f"trace={len(state.get('trace', []))}"
    if not USE_LLM_AGENTS:
        return {
            "itinerary": itinerary,
            "_note": trace_note,
        }

    try:
        narrator, tokens = call_structured_llm(_build_narrator_messages(state), NarratorResponse)
    except LLMUnavailable as exc:
        return {
            "itinerary": itinerary,
            "_node_status": "degraded",
            "_note": f"narrator=degraded:{exc}",
        }

    _apply_narrator_result(itinerary, narrator)
    # Narrator token：读 itinerary.token_usage（= state.token_usage 经 reducer 已累加全量）
    # 再加上 narrator 本次调用的增量，写进 itinerary.token_usage 作为最终汇总统计。
    # summarize 是 FINAL_NODES 单节点（不并行），此处汇总完整链路 token 不会被 reducer 再加，
    # 同时不把 token_usage 放入返回 patch，避免与 reducer 交互出错。
    usage = itinerary.token_usage or TokenUsage()
    usage = TokenUsage(
        rewrite_prompt_tokens=usage.rewrite_prompt_tokens,
        rewrite_completion_tokens=usage.rewrite_completion_tokens,
        embedding_prompt_tokens=usage.embedding_prompt_tokens,
        embedding_completion_tokens=usage.embedding_completion_tokens,
        planner_prompt_tokens=usage.planner_prompt_tokens + tokens.get("prompt_tokens", 0),
        planner_completion_tokens=usage.planner_completion_tokens + tokens.get("completion_tokens", 0),
        rerank_prompt_tokens=usage.rerank_prompt_tokens,
        rerank_completion_tokens=usage.rerank_completion_tokens,
    )
    itinerary.token_usage = usage
    return {
        "itinerary": itinerary,
        "_tokens": tokens,
        "_note": "narrator=success",
    }
