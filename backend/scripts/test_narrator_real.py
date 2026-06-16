from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


CURRENT_FILE = Path(__file__).resolve()
BACKEND_DIR = CURRENT_FILE.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import LLM_API_KEY, USE_LLM_AGENTS  # noqa: E402
from app.models.schemas import TripRequest  # noqa: E402
from app.services.trip_service import generate_trip_itinerary  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="使用真实 LLM 验证 Narrator 文案 Agent。")
    parser.add_argument("--destination", default="大理", help="目的地")
    parser.add_argument("--start-date", default="2026-04-10", help="开始日期，格式 YYYY-MM-DD")
    parser.add_argument("--end-date", default="2026-04-12", help="结束日期，格式 YYYY-MM-DD")
    parser.add_argument("--travelers", type=int, default=2, help="出行人数")
    parser.add_argument("--budget", type=float, default=3200, help="总预算")
    parser.add_argument("--pace", default="轻松", help="旅行节奏")
    parser.add_argument("--hotel-level", default="舒适型", help="酒店档次")
    parser.add_argument("--preference", action="append", default=["自然风景", "拍照", "美食"])
    parser.add_argument("--dietary", action="append", default=["少辣"])
    parser.add_argument("--notes", default="不想太早起床，希望安排一个适合看日落的地点")
    return parser


def build_request(args: argparse.Namespace) -> TripRequest:
    return TripRequest(
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


def main() -> int:
    args = build_parser().parse_args()
    if not USE_LLM_AGENTS or not LLM_API_KEY:
        raise SystemExit("需要真实 LLM 配置：请设置 USE_LLM_AGENTS=true 且提供 LLM_API_KEY。")

    itinerary = generate_trip_itinerary(build_request(args))
    print(json.dumps(itinerary.model_dump(mode="json"), ensure_ascii=False, indent=2))

    usage = itinerary.token_usage
    planner_tokens = 0
    if usage is not None:
        planner_tokens = usage.planner_prompt_tokens + usage.planner_completion_tokens
    if planner_tokens <= 0:
        raise SystemExit("Narrator 未记录 planner token，请检查 LLM 调用是否成功。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
