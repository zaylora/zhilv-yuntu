from __future__ import annotations

from datetime import timedelta

from app.agents.algorithms.cluster import cluster_spots_by_day
from app.agents.algorithms.routing import haversine_km, nearest_neighbor_order
from app.agents.monitoring import monitored_node
from app.agents.nodes.rules import meal_weights, prorate_amounts, transport_weights
from app.agents.state import GeoPoint, MealCandidate, SpotCandidate, TripState
from app.models.schemas import DayPlan, MealItem, SpotItem, TransportItem


def _nearest_meals(
    meals: list[MealCandidate],
    centroid: GeoPoint,
    count: int,
    fallback_destination: str,
) -> list[MealCandidate]:
    center = SpotCandidate(name="center", latitude=centroid.latitude, longitude=centroid.longitude)
    ranked = sorted(
        meals,
        key=lambda meal: haversine_km(center, meal),
    )
    selected = ranked[:count]
    while len(selected) < count:
        selected.append(
            MealCandidate(
                name=f"{fallback_destination} 特色餐饮 {len(selected) + 1}",
                cuisine="本地风味",
                avg_price=45,
                notes="补充餐饮候选。",
            )
        )
    return selected


def _pick_rain_adjusted_spots(
    candidates: list[SpotCandidate],
    is_rainy: bool,
    max_count: int,
) -> list[SpotCandidate]:
    ordered = nearest_neighbor_order(candidates)
    if is_rainy:
        indoor = [candidate for candidate in ordered if candidate.is_indoor]
        outdoor = [candidate for candidate in ordered if not candidate.is_indoor]
        ordered = indoor + outdoor
    return ordered[:max_count]


@monitored_node("schedule")
def schedule_node(state: TripState) -> dict:
    request = state["request"]
    day_count = state.get("day_count") or max((request.end_date - request.start_date).days + 1, 1)
    spot_candidates = state.get("spot_candidates", [])
    meal_candidates = state.get("meal_candidates", [])
    weather_days = state.get("weather").days if state.get("weather") is not None else []
    transport_plan = state.get("transport_options")

    clusters, centroids = cluster_spots_by_day(spot_candidates, day_count)
    meal_costs = prorate_amounts(request.budget * (0.28 if "美食" in request.preferences else 0.22), meal_weights(day_count, request.preferences))
    transport_costs = prorate_amounts(request.budget * 0.14, transport_weights(day_count, request.pace))
    replan_count = state.get("replan_count", 0)
    if replan_count:
        meal_costs = [round(value * 0.88, 2) for value in meal_costs]
        transport_costs = [round(value * 0.90, 2) for value in transport_costs]

    days: list[DayPlan] = []
    for index in range(day_count):
        current_date = request.start_date + timedelta(days=index)
        weather_day = weather_days[index] if index < len(weather_days) else None
        cluster = clusters[index] if index < len(clusters) else []
        selected_spots = _pick_rain_adjusted_spots(cluster, bool(weather_day and weather_day.is_rainy), max_count=2)
        if not selected_spots:
            selected_spots = [
                SpotCandidate(
                    name=f"{request.destination} 推荐景点 {index + 1}",
                    category="景点",
                    is_indoor=False,
                    ticket_estimate=35,
                    description="根据目的地和偏好补充的候选景点。",
                )
            ]

        centroid = centroids[index] if index < len(centroids) else GeoPoint(latitude=0, longitude=0)
        selected_meals = _nearest_meals(meal_candidates, centroid, 1, request.destination)
        meal_cost = meal_costs[index] if index < len(meal_costs) else 0.0
        transport_cost = transport_costs[index] if index < len(transport_costs) else 0.0

        spot_items = [
            SpotItem(
                name=spot.name,
                start_time="10:00" if spot_index == 0 else "14:30",
                end_time="12:00" if spot_index == 0 else "17:00",
                description=spot.description or "根据候选池和地理顺路关系安排。",
                estimated_cost=spot.ticket_estimate,
                location=spot.address or request.destination,
                image_url=spot.image_url,
                address=spot.address,
                latitude=spot.latitude,
                longitude=spot.longitude,
                poi_id=spot.poi_id,
                is_indoor=spot.is_indoor,
            )
            for spot_index, spot in enumerate(selected_spots)
        ]
        meal = selected_meals[0]

        weather_note = ""
        if weather_day is not None:
            weather_note = f"天气参考：{weather_day.condition}（{weather_day.source}）"
            if weather_day.is_rainy:
                weather_note += "，优先安排室内或低暴露活动。"

        days.append(
            DayPlan(
                day_index=index + 1,
                date=current_date,
                theme=f"{request.destination} 第 {index + 1} 天顺路游",
                spots=spot_items,
                meals=[
                    MealItem(
                        name=meal.name,
                        meal_type="午餐",
                        estimated_cost=meal_cost,
                        notes=meal.notes or "按当天活动中心点就近安排。",
                    )
                ],
                transport=[
                    TransportItem(
                        mode=transport_plan.intracity_default_mode if transport_plan is not None else "打车",
                        from_place=transport_plan.hub if transport_plan is not None else f"{request.destination} 市区",
                        to_place=spot_items[0].name,
                        estimated_cost=transport_cost,
                        duration="30 分钟",
                    )
                ],
                notes=[
                    f"当前旅行节奏：{request.pace or '适中'}",
                    weather_note or "建议根据当天体力和实时天气微调停留时间。",
                ],
            )
        )

    return {
        "day_plans": days,
        "day_centroids": centroids,
        "_note": f"days={len(days)}, replan={replan_count}",
    }
