from __future__ import annotations

from app.agents.monitoring import monitored_node
from app.agents.nodes.rules import hotel_weights, prorate_amounts
from app.agents.state import TripState
from app.models.schemas import HotelItem


def _hotel_ratio(level: str | None) -> float:
    hotel_level = level or "舒适型"
    if "豪华" in hotel_level:
        return 0.62
    if "高档" in hotel_level or "高端" in hotel_level:
        return 0.56
    if "经济" in hotel_level:
        return 0.40
    return 0.50


@monitored_node("hotel")
def hotel_node(state: TripState) -> dict:
    request = state["request"]
    days = [day.model_copy(deep=True) for day in state.get("day_plans", [])]
    day_count = len(days) or state.get("day_count") or 1
    level = request.hotel_level or "舒适型"
    hotel_total = request.budget * _hotel_ratio(level)
    replan_count = state.get("replan_count", 0)
    if replan_count:
        hotel_total *= 0.86
    hotel_costs = prorate_amounts(hotel_total, hotel_weights(day_count, request.start_date))
    centroids = state.get("day_centroids", [])

    for index, day in enumerate(days):
        centroid = centroids[index] if index < len(centroids) else None
        location = f"{request.destination} 活动中心"
        latitude = centroid.latitude if centroid is not None and centroid.latitude else None
        longitude = centroid.longitude if centroid is not None and centroid.longitude else None
        day.hotel = HotelItem(
            name=f"{request.destination} {level}住宿 {index + 1}",
            level=level,
            estimated_cost=hotel_costs[index] if index < len(hotel_costs) else 0.0,
            location=location,
            latitude=latitude,
            longitude=longitude,
        )

    return {
        "day_plans": days,
        "_note": f"hotels={len(days)}",
    }
