# P0+P1 Design: LLM 接入层与 Narrator 文案 Agent

日期：2026-06-16  
范围：`backend/app`  
来源：`docs/PRD-多Agent-LLM行程规划.md` 与 `多Agent-LLM行程规划策划书.md`

## 目标

第一阶段只落地 PRD 的 P0+P1 安全底座：

- 新增统一 LLM 接入层，复用现有 `LLM_*` 配置。
- 新增 `USE_LLM_AGENTS=false` 总开关，默认保持当前行为。
- 在最终文案环节接入 Narrator Agent，生成 `summary`、`tips`、每日 `theme` 和每日提示。
- LLM 不可用、无 key、超时、坏 JSON、校验失败时自动回退当前模板文案。
- 将真实 LLM usage 累加到 `TokenUsage.planner_prompt_tokens` 和 `planner_completion_tokens`。

本阶段不改 `TripRequest`、`Itinerary`、SSE 接口签名，不引入 Coordinator、SpotCurator、MealCurator、Critic，也不重构 graph 拓扑。

## 现状约束

当前后端行程生成路径是固定规则图：

```text
dispatch -> spots -> meals -> transport -> weather -> schedule -> hotel -> budget_check -> summarize
```

`summarize_node` 负责组装最终 `Itinerary`，并用模板生成 summary 和 tips。`TokenUsage` 已有 planner 字段，`monitored_node` 已有 `_tokens` 钩子，适合直接承接 LLM usage。`config.py` 已存在 `LLM_PROVIDER`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_BASE_URL`、`LLM_TIMEOUT_SECONDS`、`LLM_MAX_RETRIES`，但目前未被生成链路使用。

## 架构

新增 `backend/app/llm/`：

```text
app/llm/
  __init__.py
  client.py
  structured.py
  registry.py
```

职责：

- `client.py`：OpenAI-compatible chat completions 客户端。无 key 或开关关闭时返回不可用信号，不向节点抛业务外异常。
- `structured.py`：统一执行 JSON 调用、解析、Pydantic 校验。解析或校验失败抛 `LLMUnavailable`，由节点降级。
- `registry.py`：集中保存 Narrator system prompt 与用户消息构造逻辑。

`summarize_node` 保持对外入口不变，内部拆成两段：

1. 使用当前模板逻辑先构造一个完整、可用的 `Itinerary`。
2. 如果 `USE_LLM_AGENTS=true`，尝试调用 Narrator；成功则覆盖文案字段并写入 token，失败则返回第 1 步结果。

这样降级路径和当前行为共享同一份规则输出，不会因为 LLM 失败影响行程生成。

## Narrator 输出模型

新增内部 Pydantic 模型：

```json
{
  "summary": "60-120 字行程概述",
  "tips": ["3-5 条游客可用建议"],
  "day_titles": {"1": "第 1 天标题"},
  "day_notes": {"1": ["当天提示"]}
}
```

应用规则：

- `summary` 覆盖 `Itinerary.summary`。
- `tips` 经过 `clean_user_tips` 过滤后覆盖 `Itinerary.tips`。
- `day_titles` 写入对应 `DayPlan.theme`。
- `day_notes` 追加到对应 `DayPlan.notes`，不删除 schedule/weather 已生成的节奏和天气提示。
- 缺失或越界的天数忽略，保留原规则文案。
- 文案不得包含 “LLM”“模型”“规则”“源码”“trip_service” 等技术词；过滤后如果 tips 为空，使用当前兜底 tips。

## 降级策略

降级条件：

- `USE_LLM_AGENTS=false`。
- `LLM_API_KEY` 为空。
- HTTP 超时、网络错误、非 2xx、OpenAI-compatible 响应结构缺失。
- LLM 返回非 JSON、JSON 缺字段或字段类型不符合 Pydantic 模型。
- Narrator 输出通过技术词过滤后不可用。

降级行为：

- 继续返回当前模板 `Itinerary`。
- `summarize` 节点 `_node_status=degraded`，`_note` 说明原因。
- token usage 不增加。

成功行为：

- `summarize` 节点 `_node_status=success`。
- `_tokens` 写入本次 prompt 和 completion token。
- `state.token_usage.planner_prompt_tokens` 与 `planner_completion_tokens` 累加真实 usage。

## 配置

新增：

```bash
USE_LLM_AGENTS=false
```

保留并复用：

```bash
LLM_PROVIDER=openai_compatible
LLM_API_KEY=
LLM_MODEL=gpt-4o-mini
LLM_BASE_URL=
LLM_TIMEOUT_SECONDS=60
LLM_MAX_RETRIES=1
```

本阶段不新增博查配置，不启用餐饮评论增强。

## 测试与验收

自动测试不使用假大模型，不 monkeypatch 伪造成功结果。

必须覆盖：

- `USE_LLM_AGENTS=false` 时生成结果保持可用，token 为 0。
- `USE_LLM_AGENTS=true` 但无 `LLM_API_KEY` 时自动降级，仍返回完整 itinerary。
- 坏配置或调用失败不会向 API 用户暴露异常。
- `clean_user_tips` 对 Narrator 路径仍生效。
- 保存后的 itinerary 可通过 `/trip/stats` 统计 token；无真实 LLM 调用时为 0。

真实 LLM 成功路径用可选集成脚本验证：

- 只有在本地配置 `LLM_API_KEY`，并显式设置 `USE_LLM_AGENTS=true` 时运行。
- 真实调用一次 Narrator。
- 验证 `summary`、`tips`、至少一个 day theme/day note 被 LLM 改写。
- 验证 `planner_prompt_tokens` 或 `planner_completion_tokens` 大于 0。

## 后续阶段预留

P2 会在同一套 `app/llm` 上继续接入 Coordinator、SpotCurator、MealCurator，并补强高德 `search_places(types, citylimit, biz_ext)`。P3 再接入 Critic 回环、Scheduler 消费 `revise_hints` 和 graph 拓扑收敛。P0+P1 不提前实现这些节点，只保证接入层接口可复用。
