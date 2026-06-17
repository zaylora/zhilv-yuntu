from __future__ import annotations

import json

from app.agents.monitoring import monitored_node
from app.agents.nodes.rules import stable_bucket
from app.agents.state import MealCandidate, TripState
from app.config import USE_LLM_AGENTS
from app.llm.registry import MEAL_CURATOR_SYSTEM_PROMPT
from app.llm.structured import LLMUnavailable, call_structured_llm
from app.models.schemas import MealCuratorResponse, TokenUsage
from app.services.map_service import search_places
from app.services.web_search_service import search_web


KNOWN_MEALS: dict[str, list[str]] = {
    "大理": ["喜洲粑粑", "砂锅鱼", "白族三道茶", "乳扇小吃", "洱海边家常菜", "海景下午茶"],
    "厦门": ["沙茶面", "姜母鸭", "海蛎煎", "土笋冻", "闽南海蛎", "花生汤"],
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
    queries = [f"{destination} 美食", f"{destination} 餐厅"]
    if dietary_tags:
        queries.extend(f"{destination} {tag} 餐厅" for tag in dietary_tags)

    candidates: list[MealCandidate] = []
    seen_names: set[str] = set()
    for query in queries:
        try:
            places = search_places(query, city=destination, page_size=5, types="050000", citylimit=True)
        except Exception:
            continue
        for place in places:
            name = place.get("name")
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            # 高德 rating/avg_cost 写入候选
            raw_rating = place.get("rating")
            raw_avg_cost = place.get("avg_cost")
            try:
                rating = float(raw_rating) if raw_rating not in (None, "", "暂无") else None
            except (ValueError, TypeError):
                rating = None
            try:
                avg_cost = float(raw_avg_cost) if raw_avg_cost not in (None, "", "暂无") else None
            except (ValueError, TypeError):
                avg_cost = None

            candidates.append(
                MealCandidate(
                    name=name,
                    latitude=place.get("latitude"),
                    longitude=place.get("longitude"),
                    cuisine=place.get("type") or "餐饮",
                    avg_price=round(45 + stable_bucket(name, 5) * 10, 2),
                    dietary_tags=dietary_tags,
                    notes=place.get("address") or "按饮食偏好检索到的餐饮候选。",
                    rating=rating,
                    avg_cost=avg_cost,
                )
            )
            if len(candidates) >= limit:
                return candidates
    return candidates


def _build_meal_curator_messages(
    candidates: list[MealCandidate],
    state: TripState,
) -> list[dict[str, str]]:
    """构造 MealCurator LLM 的消息列表。"""
    request = state["request"]
    normalized = state.get("normalized")
    planning_strategy = state.get("planning_strategy")

    candidate_list = [
        {
            "name": c.name,
            "cuisine": c.cuisine,
            "rating": c.rating,
            "avg_cost": c.avg_cost,
            "address": c.notes,
        }
        for c in candidates
    ]

    payload: dict = {
        "destination": request.destination,
        "candidates": candidate_list,
        "travelers": request.travelers,
        "dietary_preferences": request.dietary_preferences or [],
        "meal_keywords": normalized.meal_keywords if normalized else [],
    }
    if planning_strategy:
        payload["strategy"] = planning_strategy.strategy
        payload["hard_constraints"] = planning_strategy.hard_constraints

    return [
        {"role": "system", "content": MEAL_CURATOR_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _build_bocha_notes(
    candidate: MealCandidate,
    web_hits: list[dict],
) -> str | None:
    """把高德评分和博查片段合并写入 notes（字段来源铁律：rating 仍来自 candidate.rating）。"""
    parts: list[str] = []

    # 高德评分
    if candidate.rating is not None:
        parts.append(f"评分 {candidate.rating}")

    # 博查招牌菜/点评（只用 snippet 文本，不用 LLM 编的）
    if web_hits:
        snippets = [hit.get("snippet", "") for hit in web_hits if hit.get("snippet")]
        if snippets:
            combined = "；".join(snippets[:2])  # 最多取 2 条
            parts.append(combined)

    if not parts:
        return candidate.notes

    bocha_info = "；".join(parts)
    if candidate.notes:
        return f"{candidate.notes}；{bocha_info}"
    return bocha_info


@monitored_node("meals")
def meal_search_node(state: TripState) -> dict:
    request = state["request"]
    day_count = state.get("day_count") or max((request.end_date - request.start_date).days + 1, 1)
    normalized = state.get("normalized")
    destination = normalized.city_canonical if normalized is not None else request.destination
    dietary_tags = normalized.dietary_norm if normalized is not None else request.dietary_preferences
    target_count = max(day_count * 2, 6)

    # ── 规则路径（兜底）：高德候选 ────────────────────────
    amap_meals = _search_amap_meals(destination, dietary_tags, target_count)

    # ── MealCurator：仅在 USE_LLM_AGENTS=True 且有候选时介入 ──
    token_usage_patch: dict = {}
    if USE_LLM_AGENTS and amap_meals:
        try:
            seen_names = {c.name for c in amap_meals}
            curator, tokens = call_structured_llm(
                _build_meal_curator_messages(amap_meals, state),
                MealCuratorResponse,
            )
            # 累加 token：只返回本次调用的增量，由 state reducer 负责累加
            usage = TokenUsage(
                planner_prompt_tokens=tokens.get("prompt_tokens", 0),
                planner_completion_tokens=tokens.get("completion_tokens", 0),
            )
            token_usage_patch = {"token_usage": usage, "_tokens": tokens}

            # 只接受候选池内名称（去重保序）
            picked_ordered: list[str] = []
            seen_picked: set[str] = set()
            for sel in curator.selected:
                if sel.name in seen_names and sel.name not in seen_picked:
                    picked_ordered.append(sel.name)
                    seen_picked.add(sel.name)

            if picked_ordered:
                name_to_candidate = {c.name: c for c in amap_meals}
                reordered = [name_to_candidate[n] for n in picked_ordered]
                rest = [c for c in amap_meals if c.name not in seen_picked]
                amap_meals = reordered + rest

                # 博查增强 + notes 合并（对被选中的候选）
                for candidate in reordered:
                    try:
                        web_hits = search_web(f"{destination} {candidate.name}")
                    except Exception:
                        web_hits = []
                    # rating 只来自高德（candidate.rating），绝不用 LLM 的
                    # signature_dishes/review_digest 只来自博查片段，不用 LLM 编的
                    candidate.notes = _build_bocha_notes(candidate, web_hits)
        except LLMUnavailable:
            # 降级：保持规则 amap_meals，不抛
            pass

    candidates = amap_meals
    status = "success"
    note = f"candidates={len(candidates)}"
    if len(candidates) < target_count:
        fallback = _fallback_meals(destination, dietary_tags, target_count)
        seen = {candidate.name for candidate in candidates}
        candidates.extend(candidate for candidate in fallback if candidate.name not in seen)
        candidates = candidates[:target_count]
        status = "degraded"
        note = f"amap_candidates={len(seen)}, fallback_total={len(candidates)}"

    result: dict = {
        "meal_candidates": candidates,
        "_node_status": status,
        "_note": note,
    }
    result.update(token_usage_patch)
    return result
