from __future__ import annotations

import json

from app.agents.monitoring import monitored_node
from app.agents.state import TripState
from app.config import TRIP_MAX_REPLAN, USE_LLM_AGENTS
from app.llm.registry import CRITIC_SYSTEM_PROMPT
from app.llm.structured import LLMUnavailable, call_structured_llm
from app.models.schemas import CriticResponse, TokenUsage


def _build_critic_messages(state: TripState) -> list[dict[str, str]]:
    """构造 Critic LLM 的消息列表。"""
    request = state["request"]
    day_plans = state.get("day_plans") or []
    budget_report = state.get("budget_report")
    replan_count = state.get("replan_count", 0)

    # 行程摘要：每天 theme/景点名/餐饮名/notes
    days_summary = []
    for day in day_plans:
        spot_names = [s.name for s in (day.spots or [])]
        meal_names = [m.name for m in (day.meals or [])]
        days_summary.append({
            "day_index": day.day_index,
            "theme": day.theme,
            "spots": spot_names,
            "meals": meal_names,
            "notes": day.notes or [],
        })

    # 预算报告摘要
    budget_summary = None
    if budget_report is not None:
        budget_summary = {
            "total": budget_report.total,
            "over_budget": budget_report.over_budget,
            "breakdown": budget_report.breakdown,
            "missing_items": budget_report.missing_items,
        }

    # 用户约束
    user_constraints = {
        "preferences": request.preferences or [],
        "dietary": request.dietary_preferences or [],
        "pace": request.pace,
        "budget": request.budget,
        "special_notes": request.special_notes,
    }

    payload = {
        "day_count": len(day_plans),
        "days_summary": days_summary,
        "budget_report": budget_summary,
        "user_constraints": user_constraints,
        "replan_count": replan_count,
    }

    return [
        {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _accept_patch(note: str, state: TripState) -> dict:
    """构造等价接受的 patch（不触发回环）。"""
    return {
        "critic_report": CriticResponse(verdict="accept"),
        "revise_hints": [],
        "_note": note,
    }


@monitored_node("critic")
def critic_node(state: TripState) -> dict:
    """评审行程质量，输出 verdict/score/issues/revise_hints。

    三种结局：
    1. 降级（USE_LLM_AGENTS=False 或 LLMUnavailable）→ 等价接受，不触发回环。
    2. revise 且 replan_count >= max_replan → 强制接受，不自增 replan_count。
    3. revise 且未达上限 → 保留 hints，自增 replan_count。
    """
    replan_count = state.get("replan_count", 0)
    max_replan = state.get("max_replan", TRIP_MAX_REPLAN)

    # ── 降级：LLM 不可用 ─────────────────────────────────────
    if not USE_LLM_AGENTS:
        return _accept_patch("critic=degraded", state)

    # ── 调用 Critic LLM ──────────────────────────────────────
    try:
        report, tokens = call_structured_llm(
            _build_critic_messages(state),
            CriticResponse,
        )
    except LLMUnavailable:
        return _accept_patch("critic=degraded", state)

    # ── 累加 token：只返回本次调用的增量，由 state reducer 负责累加 ────────────
    delta = TokenUsage(
        # call_structured_llm 返回的 tokens 键名可能是 input_tokens/output_tokens
        # 也可能是 prompt_tokens/completion_tokens，兼容两种格式
        planner_prompt_tokens=tokens.get("prompt_tokens", 0) or tokens.get("input_tokens", 0),
        planner_completion_tokens=tokens.get("completion_tokens", 0) or tokens.get("output_tokens", 0),
    )

    # ── 达上限强制接受 ────────────────────────────────────────
    if report.verdict == "revise" and replan_count >= max_replan:
        return {
            "critic_report": CriticResponse(
                verdict="accept",
                score=report.score,
                issues=report.issues,
                revise_hints=[],
            ),
            "revise_hints": [],
            "token_usage": delta,
            "_tokens": tokens,
            "_note": "critic=accept(forced,max_replan_reached)",
        }

    # ── revise 未达上限 ───────────────────────────────────────
    # Important #2 修复：budget 本轮已因超支自增 replan_count 时（over_budget=True 且
    # budget_check 触发自增的条件为 over_budget and replan_count_before < max_replan），
    # critic 就不再额外自增，避免单轮双重消耗回环额度。
    # 注意：此时读到的 state.replan_count 是 budget_check 已自增后的值；
    # budget 触发自增的前提是 over_budget=True（若已达上限 budget_check 也不增，
    # 但此时 replan_count >= max_replan 在上面已强制接受，不会到这里）。
    if report.verdict == "revise":
        budget_report = state.get("budget_report")
        budget_triggered_increment = (
            budget_report is not None and getattr(budget_report, "over_budget", False)
        )
        if budget_triggered_increment:
            # budget 本轮已自增，critic 只保留 hints 不再自增
            return {
                "critic_report": report,
                "revise_hints": report.revise_hints,
                "token_usage": delta,
                "_tokens": tokens,
                "_note": f"critic=revise(hints_only,budget_incremented):{replan_count}",
            }
        else:
            return {
                "critic_report": report,
                "revise_hints": report.revise_hints,
                "replan_count": replan_count + 1,
                "token_usage": delta,
                "_tokens": tokens,
                "_note": f"critic=revise:{replan_count + 1}",
            }

    # ── accept ────────────────────────────────────────────────
    return {
        "critic_report": report,
        "revise_hints": [],
        "token_usage": delta,
        "_tokens": tokens,
        "_note": "critic=accept",
    }
