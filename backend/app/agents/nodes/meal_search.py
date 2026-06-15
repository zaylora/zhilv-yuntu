from __future__ import annotations

from app.agents.monitoring import monitored_node
from app.agents.nodes.rules import stable_bucket
from app.agents.state import MealCandidate, TripState


KNOWN_MEALS: dict[str, list[str]] = {
    "大理": ["喜洲粑粑", "砂锅鱼", "白族三道茶", "乳扇小吃", "洱海边家常菜", "海景下午茶"],
    "厦门": ["沙茶面", "姜母鸭", "海蛎煎", "土笋冻", "闽南海鲜", "花生汤"],
}


def _fallback_meals(destination: str, dietary_tags: list[str], count: int) -> list[MealCandidate]:
    names = list(KNOWN_MEALS.get(destination, []))
    while len(names) < count:
        names.append(f"{destination} 特色餐饮 {len(names) + 1}")

    candidates: list[MealCandidate] = []
    for name in names[:count]:
        candidates.append(
            MealCandidate(
                name=name,
                cuisine="本地风味",
                avg_price=round(35 + stable_bucket(name, 5) * 12, 2),
                dietary_tags=dietary_tags,
                notes="根据目的地特色和饮食偏好预留的餐饮候选。",
            )
        )
    return candidates


def _search_amap_meals(destination: str, dietary_tags: list[str], limit: int) -> list[MealCandidate]:
    try:
        from app.services.map_service import search_places
    except Exception:
        return []

    queries = [f"{destination} 美食", f"{destination} 餐厅"]
    if dietary_tags:
        queries.extend(f"{destination} {tag} 餐厅" for tag in dietary_tags)

    candidates: list[MealCandidate] = []
    seen_names: set[str] = set()
    for query in queries:
        try:
            places = search_places(query, city=destination, page_size=5)
        except Exception:
            continue
        for place in places:
            name = place.get("name")
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            candidates.append(
                MealCandidate(
                    name=name,
                    latitude=place.get("latitude"),
                    longitude=place.get("longitude"),
                    cuisine=place.get("type") or "餐饮",
                    avg_price=round(45 + stable_bucket(name, 5) * 10, 2),
                    dietary_tags=dietary_tags,
                    notes=place.get("address") or "按饮食偏好检索到的餐饮候选。",
                )
            )
            if len(candidates) >= limit:
                return candidates
    return candidates


@monitored_node("meals")
def meal_search_node(state: TripState) -> dict:
    request = state["request"]
    day_count = state.get("day_count") or max((request.end_date - request.start_date).days + 1, 1)
    normalized = state.get("normalized")
    destination = normalized.city_canonical if normalized is not None else request.destination
    dietary_tags = normalized.dietary_norm if normalized is not None else request.dietary_preferences
    target_count = max(day_count * 2, 6)

    candidates = _search_amap_meals(destination, dietary_tags, target_count)
    status = "success"
    note = f"candidates={len(candidates)}"
    if len(candidates) < target_count:
        fallback = _fallback_meals(destination, dietary_tags, target_count)
        seen = {candidate.name for candidate in candidates}
        candidates.extend(candidate for candidate in fallback if candidate.name not in seen)
        candidates = candidates[:target_count]
        status = "degraded"
        note = f"amap_candidates={len(seen)}, fallback_total={len(candidates)}"

    return {
        "meal_candidates": candidates,
        "_node_status": status,
        "_note": note,
    }
