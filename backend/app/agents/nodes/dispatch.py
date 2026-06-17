from __future__ import annotations

import json

from app.agents.monitoring import monitored_node
from app.agents.state import NormalizedDemand, TripState
from app.config import USE_LLM_AGENTS
from app.llm.registry import COORDINATOR_SYSTEM_PROMPT
from app.llm.structured import LLMUnavailable, call_structured_llm
from app.models.schemas import CoordinatorResponse, PlanningStrategy, TokenUsage


def _build_coordinator_messages(
    request,  # TripRequest
    normalized: NormalizedDemand,
    day_count: int,
) -> list[dict[str, str]]:
    """构造 Coordinator LLM 的消息列表。"""
    payload = {
        "destination": request.destination,
        "start_date": request.start_date.isoformat(),
        "end_date": request.end_date.isoformat(),
        "day_count": day_count,
        "travelers": request.travelers,
        "budget": request.budget,
        "preferences": request.preferences or [],
        "pace": request.pace,
        "dietary_preferences": request.dietary_preferences or [],
        "hotel_level": request.hotel_level,
        "special_notes": request.special_notes,
        "rule_spot_keywords": normalized.spot_keywords,
    }
    return [
        {"role": "system", "content": COORDINATOR_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _dedup_ordered(base: list[str], extra: list[str]) -> list[str]:
    """合并两个列表并去重保序（base 在前，extra 中新元素追加到末尾）。"""
    seen: set[str] = set()
    result: list[str] = []
    for kw in base + extra:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result


@monitored_node("dispatch")
def dispatch_node(state: TripState) -> dict:
    """Normalize request fields into search intents."""
    request = state["request"]
    preferences = request.preferences or []

    # ── 始终先构造规则兜底 ──────────────────────────────
    spot_keywords = [request.destination]
    spot_keywords.extend(preferences)
    if request.special_notes:
        spot_keywords.append(request.special_notes)

    normalized = NormalizedDemand(
        city_canonical=request.destination.strip(),
        spot_keywords=[kw for kw in spot_keywords if kw],
        meal_keywords=[],
        dietary_norm=request.dietary_preferences or [],
        transport_intent=f"{request.pace or '适中'}节奏，市内以打车和步行为主",
        hotel_level=request.hotel_level or "舒适型",
    )
    day_count = max((request.end_date - request.start_date).days + 1, 1)

    if not USE_LLM_AGENTS:
        return {
            "normalized": normalized,
            "day_count": day_count,
            "_note": f"days={day_count}",
        }

    # ── 调用 Coordinator LLM ────────────────────────────
    try:
        coordinator, tokens = call_structured_llm(
            _build_coordinator_messages(request, normalized, day_count),
            CoordinatorResponse,
        )
    except LLMUnavailable as exc:
        return {
            "normalized": normalized,
            "day_count": day_count,
            "_node_status": "degraded",
            "_note": f"coordinator=degraded:{exc}",
        }

    # ── 成功：补强 normalized ──────────────────────────
    normalized.spot_keywords = _dedup_ordered(
        normalized.spot_keywords, coordinator.spot_keywords
    )
    normalized.meal_keywords = _dedup_ordered(normalized.meal_keywords, coordinator.meal_keywords)

    # ── 累加 token：只返回本次调用的增量，由 state reducer 负责累加 ───────────
    delta = TokenUsage(
        planner_prompt_tokens=tokens.get("prompt_tokens", 0),
        planner_completion_tokens=tokens.get("completion_tokens", 0),
    )

    planning_strategy = PlanningStrategy.model_validate(coordinator.model_dump())

    return {
        "normalized": normalized,
        "planning_strategy": planning_strategy,
        "token_usage": delta,
        "day_count": day_count,
        "_tokens": tokens,
        "_note": "coordinator=success",
    }
