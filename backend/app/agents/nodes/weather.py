from __future__ import annotations

from datetime import date as DateType, timedelta

from app.agents.monitoring import monitored_node
from app.agents.state import WeatherContext, WeatherDay, TripState


RAIN_KEYWORDS = ("雨", "阵雨", "雷", "雪")


SEASONAL_CONDITIONS = {
    1: ("冬季偏凉，早晚温差明显", "6-16°C"),
    2: ("冬末春初，体感偏凉", "8-18°C"),
    3: ("春季舒适，偶有小雨", "10-22°C"),
    4: ("春季温和，适合户外慢游", "13-24°C"),
    5: ("初夏舒适，午后日晒增强", "16-27°C"),
    6: ("夏季偏热，注意防晒和阵雨", "20-30°C"),
    7: ("夏季炎热，午后可能有阵雨", "22-32°C"),
    8: ("夏季炎热，注意防晒补水", "22-32°C"),
    9: ("初秋舒适，适合户外活动", "18-28°C"),
    10: ("秋季清爽，早晚略凉", "14-25°C"),
    11: ("深秋偏凉，早晚温差明显", "10-21°C"),
    12: ("冬季偏凉，注意保暖", "7-17°C"),
}


def seasonal_weather_day(current_date: DateType) -> WeatherDay:
    condition, temp_range = SEASONAL_CONDITIONS[current_date.month]
    return WeatherDay(
        date=current_date,
        is_rainy="雨" in condition,
        condition=condition,
        temp_range=temp_range,
        source="seasonal",
    )


def _forecast_weather_days(destination: str, start_date: DateType, day_count: int) -> list[WeatherDay]:
    try:
        from app.services.weather_service import get_weather_forecast
    except Exception:
        return []

    try:
        forecast = get_weather_forecast(destination)
    except Exception:
        return []

    by_date = {
        item.get("date"): item
        for item in forecast.get("days", [])
        if isinstance(item, dict) and item.get("date")
    }
    days: list[WeatherDay] = []
    for index in range(day_count):
        current_date = start_date + timedelta(days=index)
        item = by_date.get(current_date.isoformat())
        if item is None:
            continue
        condition = item.get("day_weather") or item.get("night_weather") or "天气预报"
        temp_low = item.get("night_temp")
        temp_high = item.get("day_temp")
        temp_range = f"{temp_low}-{temp_high}°C" if temp_low and temp_high else None
        days.append(
            WeatherDay(
                date=current_date,
                is_rainy=any(keyword in condition for keyword in RAIN_KEYWORDS),
                condition=condition,
                temp_range=temp_range,
                source="forecast",
            )
        )
    return days


@monitored_node("weather")
def weather_node(state: TripState) -> dict:
    request = state["request"]
    day_count = state.get("day_count") or max((request.end_date - request.start_date).days + 1, 1)
    forecast_days = _forecast_weather_days(request.destination, request.start_date, day_count)
    forecast_by_date = {day.date: day for day in forecast_days}

    days: list[WeatherDay] = []
    for index in range(day_count):
        current_date = request.start_date + timedelta(days=index)
        days.append(forecast_by_date.get(current_date) or seasonal_weather_day(current_date))

    status = "success" if len(forecast_days) == day_count else "degraded"
    return {
        "weather": WeatherContext(days=days),
        "_node_status": status,
        "_note": f"forecast={len(forecast_days)}, seasonal={day_count - len(forecast_days)}",
    }
