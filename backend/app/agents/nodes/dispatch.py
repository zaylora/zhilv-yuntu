from __future__ import annotations

from app.agents.monitoring import monitored_node
from app.agents.state import NormalizedDemand, TripState


@monitored_node("dispatch")
def dispatch_node(state: TripState) -> dict:
    """Normalize request fields into search intents."""
    request = state["request"]
    preferences = request.preferences or []
    spot_keywords = [request.destination]
    spot_keywords.extend(preferences)
    if request.special_notes:
        spot_keywords.append(request.special_notes)

    normalized = NormalizedDemand(
        city_canonical=request.destination.strip(),
        spot_keywords=[keyword for keyword in spot_keywords if keyword],
        dietary_norm=request.dietary_preferences or [],
        transport_intent=f"{request.pace or '适中'}节奏，市内以打车和步行为主",
        hotel_level=request.hotel_level or "舒适型",
    )
    day_count = max((request.end_date - request.start_date).days + 1, 1)

    return {
        "normalized": normalized,
        "day_count": day_count,
        "_note": f"days={day_count}",
    }
