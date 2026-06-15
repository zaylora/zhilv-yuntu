from __future__ import annotations

from app.agents.monitoring import monitored_node
from app.agents.state import BudgetReport, TripState


def _sum_day_costs(days) -> dict[str, float]:
    transport = round(sum(item.estimated_cost for day in days for item in day.transport), 2)
    hotel = round(sum(day.hotel.estimated_cost for day in days if day.hotel is not None), 2)
    meals = round(sum(item.estimated_cost for day in days for item in day.meals), 2)
    tickets = round(sum(item.estimated_cost for day in days for item in day.spots), 2)
    subtotal = transport + hotel + meals + tickets
    other = round(max(subtotal * 0.06, 0.0), 2)
    total = round(subtotal + other, 2)
    return {
        "transport": transport,
        "hotel": hotel,
        "meals": meals,
        "tickets": tickets,
        "other": other,
        "total": total,
    }


def _missing_items(days) -> list[str]:
    missing: list[str] = []
    for day in days:
        prefix = f"day_{day.day_index}"
        if not day.spots:
            missing.append(f"{prefix}.spots")
        if not day.meals:
            missing.append(f"{prefix}.meals")
        if day.hotel is None:
            missing.append(f"{prefix}.hotel")
        if not day.transport:
            missing.append(f"{prefix}.transport")
    return missing


@monitored_node("budget")
def budget_check_node(state: TripState) -> dict:
    request = state["request"]
    days = state.get("day_plans", [])
    breakdown = _sum_day_costs(days)
    missing = _missing_items(days)
    over_budget = bool(request.budget and breakdown["total"] > request.budget)
    passed = not missing and not over_budget

    report = BudgetReport(
        total=breakdown["total"],
        breakdown=breakdown,
        over_budget=over_budget,
        missing_items=missing,
        passed=passed,
    )
    patch = {
        "budget_report": report,
        "_node_status": "success" if passed else "degraded",
        "_note": f"total={breakdown['total']}, over={over_budget}, missing={len(missing)}",
    }
    if over_budget and state.get("replan_count", 0) < state.get("max_replan", 2):
        patch["replan_count"] = state.get("replan_count", 0) + 1
    return patch
