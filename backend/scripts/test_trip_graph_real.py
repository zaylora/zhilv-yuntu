from __future__ import annotations

import argparse
from pathlib import Path
import sys


CURRENT_FILE = Path(__file__).resolve()
BACKEND_DIR = CURRENT_FILE.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.graph import run_trip_graph  # noqa: E402
from app.models.schemas import TripRequest  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the LangGraph trip planner once.")
    parser.add_argument("--destination", default="大理")
    parser.add_argument("--start-date", default="2026-04-10")
    parser.add_argument("--end-date", default="2026-04-12")
    parser.add_argument("--travelers", type=int, default=2)
    parser.add_argument("--budget", type=float, default=3200)
    parser.add_argument("--pace", default="轻松")
    parser.add_argument("--hotel-level", default="舒适型")
    parser.add_argument("--preference", action="append", default=["自然风景", "拍照", "美食"])
    parser.add_argument("--dietary", action="append", default=["少辣"])
    parser.add_argument("--notes", default="不想太早起床，希望安排一个适合看日落的地点")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    request = TripRequest(
        destination=args.destination,
        start_date=args.start_date,
        end_date=args.end_date,
        travelers=args.travelers,
        budget=args.budget,
        preferences=args.preference,
        pace=args.pace,
        dietary_preferences=args.dietary,
        hotel_level=args.hotel_level,
        special_notes=args.notes,
    )

    # This script intentionally exercises the graph, not the API wrapper, so
    # node traces and budget behavior are easy to inspect during real debugging.
    itinerary = run_trip_graph(request)

    print("=== Trip Graph Result ===")
    print(f"trip_id: {itinerary.trip_id}")
    print(f"destination: {itinerary.destination}")
    print(f"days: {len(itinerary.days)}")
    print(f"budget_total: {itinerary.budget_breakdown.total}")
    print(f"summary: {itinerary.summary}")
    print("\n=== Days ===")
    for day in itinerary.days:
        spot_names = "、".join(spot.name for spot in day.spots)
        meal_names = "、".join(meal.name for meal in day.meals)
        hotel_name = day.hotel.name if day.hotel else "<none>"
        print(f"D{day.day_index} {day.date}: {day.theme}")
        print(f"  spots: {spot_names}")
        print(f"  meals: {meal_names}")
        print(f"  hotel: {hotel_name}")
    print("\n=== Trace ===")
    for note in itinerary.source_notes:
        if note.startswith("graph_trace:") or note.startswith("graph_error:"):
            print(note)


if __name__ == "__main__":
    main()
