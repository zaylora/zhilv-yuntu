from __future__ import annotations

from app.agents.monitoring import monitored_node
from app.agents.state import TransportPlan, TripState


@monitored_node("transport")
def transport_search_node(state: TripState) -> dict:
    request = state["request"]
    pace = request.pace or "适中"
    mode = "打车" if pace in ("轻松", "适中") else "打车+步行"
    destination = request.destination

    plan = TransportPlan(
        intercity_advice=f"建议提前确认往返{destination}的大交通班次，并给首尾日预留缓冲。",
        intracity_default_mode=mode,
        hub=f"{destination} 市区",
    )
    return {
        "transport_options": plan,
        "_note": mode,
    }
