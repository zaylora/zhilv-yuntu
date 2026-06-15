from __future__ import annotations

from app.agents.monitoring import monitored_node
from app.agents.nodes.rules import estimate_ticket_cost
from app.agents.state import SpotCandidate, TripState


KNOWN_DESTINATION_SPOTS: dict[str, list[tuple[str, float, float, str]]] = {
    "大理": [
        ("大理古城", 25.6929, 100.1612, "古城街区，适合慢游和傍晚散步。"),
        ("喜洲古镇", 25.8517, 100.1293, "白族民居与田园风景集中，适合拍照。"),
        ("崇圣寺三塔", 25.7049, 100.1487, "大理代表性人文地标，节奏可控。"),
        ("洱海生态廊道", 25.7870, 100.2260, "适合骑行、看海和轻松散步。"),
        ("双廊古镇", 25.9093, 100.2784, "靠近洱海，适合安排日落时段。"),
        ("苍山洗马潭索道", 25.6845, 100.0967, "山景体验，天气好时更适合。"),
    ],
    "厦门": [
        ("鼓浪屿", 24.4472, 118.0619, "经典海岛街区，适合步行。"),
        ("厦门园林植物园", 24.4525, 118.1082, "适合自然风景和拍照。"),
        ("沙坡尾", 24.4395, 118.0881, "街区氛围轻松，餐饮选择多。"),
        ("曾厝垵", 24.4333, 118.1282, "临海街区，适合晚间轻松逛。"),
    ],
}


INDOOR_KEYWORDS = ("博物馆", "寺", "三塔", "美术馆", "展馆", "商场", "书店")
NON_TOURISM_NAME_KEYWORDS = (
    "公安",
    "派出所",
    "政府",
    "学校",
    "小学",
    "中学",
    "医院",
    "银行",
    "公司",
    "停车场",
    "火锅",
    "餐厅",
    "饭店",
    "酒店",
    "客栈",
    "民宿",
)
NON_TOURISM_TYPE_KEYWORDS = ("政府机构", "学校", "医疗保健", "公司企业", "金融保险", "餐饮服务", "住宿服务")
TOURISM_TYPE_KEYWORDS = ("风景名胜", "体育休闲", "科教文化服务;博物馆", "寺庙", "公园")
TOURISM_NAME_KEYWORDS = (
    "景区",
    "公园",
    "古城",
    "古镇",
    "博物馆",
    "寺",
    "三塔",
    "廊道",
    "码头",
    "观景",
    "山",
    "海",
    "湖",
    "湾",
)


def _is_indoor(name: str, category: str | None = None) -> bool:
    text = f"{name} {category or ''}"
    return any(keyword in text for keyword in INDOOR_KEYWORDS)


def is_relevant_spot_place(place: dict) -> bool:
    """Return whether an Amap POI looks like a visitable spot."""
    name = str(place.get("name") or "")
    category = str(place.get("type") or "")
    text = f"{name} {category}"

    # Amap keyword search can return public services for broad city queries.
    # Keep this filter explicit so schedule never treats offices as attractions.
    if any(keyword in text for keyword in NON_TOURISM_NAME_KEYWORDS):
        return False
    if any(keyword in category for keyword in NON_TOURISM_TYPE_KEYWORDS):
        return False
    if any(keyword in category for keyword in TOURISM_TYPE_KEYWORDS):
        return True
    return any(keyword in name for keyword in TOURISM_NAME_KEYWORDS)


def _fallback_spots(destination: str, day_count: int) -> list[SpotCandidate]:
    known = KNOWN_DESTINATION_SPOTS.get(destination, [])
    candidates = [
        SpotCandidate(
            name=name,
            latitude=latitude,
            longitude=longitude,
            category="景点",
            is_indoor=_is_indoor(name),
            ticket_estimate=estimate_ticket_cost(name, description),
            description=description,
            address=destination,
        )
        for name, latitude, longitude, description in known
    ]

    target_count = max(day_count * 3, 8)
    while len(candidates) < target_count:
        index = len(candidates) + 1
        name = f"{destination} 推荐景点 {index}"
        candidates.append(
            SpotCandidate(
                name=name,
                latitude=None,
                longitude=None,
                category="景点",
                is_indoor=False,
                ticket_estimate=estimate_ticket_cost(name),
                description="根据旅行偏好补充的候选景点。",
                address=destination,
            )
        )

    return candidates[:target_count]


def _search_amap_spots(destination: str, keywords: list[str], limit: int) -> list[SpotCandidate]:
    try:
        from app.services.map_service import search_places
    except Exception:
        return []

    candidates: list[SpotCandidate] = []
    seen_names: set[str] = set()
    search_keywords = [f"{destination} 景点", f"{destination} 旅游景点", *keywords]
    for keyword in search_keywords:
        query = keyword if destination in keyword else f"{destination} {keyword}"
        try:
            places = search_places(query, city=destination, page_size=5)
        except Exception:
            continue
        for place in places:
            if not is_relevant_spot_place(place):
                continue
            name = place.get("name")
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            category = place.get("type")
            candidates.append(
                SpotCandidate(
                    name=name,
                    latitude=place.get("latitude"),
                    longitude=place.get("longitude"),
                    poi_id=place.get("poi_id"),
                    category=category,
                    is_indoor=_is_indoor(name, category),
                    ticket_estimate=estimate_ticket_cost(name, category),
                    description=f"围绕“{keyword}”检索到的候选地点。",
                    address=place.get("address") or destination,
                    image_url=place.get("image_url"),
                )
            )
            if len(candidates) >= limit:
                return candidates
    return candidates


@monitored_node("spots")
def spot_search_node(state: TripState) -> dict:
    """Collect spot candidates with coordinates when tools are available."""
    request = state["request"]
    day_count = state.get("day_count") or max((request.end_date - request.start_date).days + 1, 1)
    normalized = state.get("normalized")
    destination = normalized.city_canonical if normalized is not None else request.destination
    keywords = normalized.spot_keywords if normalized is not None else [request.destination]
    target_count = max(day_count * 3, 8)

    candidates = _search_amap_spots(destination, keywords, target_count)
    status = "success"
    note = f"candidates={len(candidates)}"
    if len(candidates) < target_count:
        fallback = _fallback_spots(destination, day_count)
        seen = {candidate.name for candidate in candidates}
        candidates.extend(candidate for candidate in fallback if candidate.name not in seen)
        candidates = candidates[:target_count]
        status = "degraded"
        note = f"amap_candidates={len(seen)}, fallback_total={len(candidates)}"

    return {
        "spot_candidates": candidates,
        "_node_status": status,
        "_note": note,
    }
