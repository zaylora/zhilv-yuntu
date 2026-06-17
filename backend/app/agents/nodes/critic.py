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

    # ── 累加 token 到 state.token_usage ─────────────────────
    usage = state.get("token_usage") or TokenUsage()
    # call_structured_llm 返回的 tokens 键名可能是 input_tokens/output_tokens
    # 也可能是 prompt_tokens/completion_tokens，兼容两种格式
    usage.planner_prompt_tokens += tokens.get("prompt_tokens", 0) or tokens.get("input_tokens", 0)
    usage.planner_completion_tokens += tokens.get("completion_tokens", 0) or tokens.get("output_tokens", 0)

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
            "token_usage": usage,
            "_tokens": tokens,
            "_note": "critic=accept(forced,max_replan_reached)",
        }

    # ── revise 未达上限 ───────────────────────────────────────
    if report.verdict == "revise":
        return {
            "critic_report": report,
            "revise_hints": report.revise_hints,
            "replan_count": replan_count + 1,
            "token_usage": usage,
            "_tokens": tokens,
            "_note": f"critic=revise:{replan_count + 1}",
        }

    # ── accept ────────────────────────────────────────────────
    return {
        "critic_report": report,
        "revise_hints": [],
        "token_usage": usage,
        "_tokens": tokens,
        "_note": "critic=accept",
    }
