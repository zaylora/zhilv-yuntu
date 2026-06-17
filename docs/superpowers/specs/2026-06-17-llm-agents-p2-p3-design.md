# P2+P3 Design: LLM 策略/策展 Agent 与 Critic 回环

日期：2026-06-17  
范围：`backend/app`  
来源：`docs/PRD-多Agent-LLM行程规划.md`、`多Agent-LLM行程规划策划书.md`、`docs/superpowers/specs/2026-06-16-llm-agents-p0-p1-design.md`

## 目标

本阶段沿用 P0+P1 已落地的 `app/llm` 接入层，在行程生成主链路继续落地 P2+P3：

- P2：接入 Coordinator、SpotCurator、MealCurator，并补强高德 `search_places(types, citylimit, biz_ext)`。
- P2：新增可选博查 Web Search 餐饮增强，默认关闭，失败时不影响行程生成。
- P3：接入 Critic 评审回环，并让 Scheduler 消费 `revise_hints`。
- P3：收敛 `graph.py` 的本地 collect/stream 拓扑重复，避免后续节点顺序漂移。
- P3：消除 `trip_service.py` 中与 `agents.nodes.rules` 已重复的规则算法实现。

本阶段不做 P4 的 `generate_day_edit_draft` LLM 单日改写，不改 `TripRequest`、`Itinerary`、SSE `done` payload、前端字段，也不为餐饮评分/招牌菜新增独立 schema 字段。

## 现状约束

P0+P1 已完成：

- `app/llm/client.py` 和 `app/llm/structured.py` 可调用 OpenAI-compatible JSON 输出。
- `USE_LLM_AGENTS` 默认关闭。
- Narrator 已在 `summarize_node` 内部降级接入，token 可累加进 `TokenUsage.planner_*`。

当前生成链路仍是：

```text
dispatch -> spots -> meals -> transport -> weather -> schedule -> hotel -> budget_check -> summarize
```

`graph.py` 同时维护 `_run_local_graph`、`stream_trip_graph_events`、`build_trip_graph` 三份拓扑。`trip_service.py` 的 legacy fallback 仍有 `_stable_bucket`、`_prorate_amounts`、`_estimate_ticket_cost`、`_clean_user_tips` 等重复规则实现。

## 架构

继续使用现有节点入口，内部增量接入 LLM 优先路径：

```text
dispatch(Coordinator fallback) -> spots(SpotCurator fallback) -> meals(MealCurator fallback)
  -> transport -> weather -> schedule -> hotel -> budget_check -> critic -> summarize
```

### Coordinator

`dispatch_node` 先构造现有 `NormalizedDemand` 作为稳定兜底。若 `USE_LLM_AGENTS=true`，尝试调用 Coordinator：

- 输出 `strategy`、`daily_themes`、`pace_normalized`、`spot_keywords`、`meal_keywords`、`budget_hint`、`hard_constraints`。
- 成功后把结构化策略写入 `state.planning_strategy`，并用 LLM 关键词补强 `normalized.spot_keywords` 和新增的 `normalized.meal_keywords`。
- 失败时返回当前规则 `NormalizedDemand`。

### SpotCurator

`spot_search_node` 先用高德按 `types=110000`、`citylimit=true` 检索候选，再保留现有黑白名单过滤。若 LLM 可用，SpotCurator 只允许从真实候选池中挑选名称：

- `selected[].name` 必须原样存在于候选池。
- 坐标、地址、`poi_id`、图片仍来自高德候选。
- `suggested_hours` 仅作为描述素材，不参与精确排程。
- 校验失败或选择为空时回退现有规则候选和 `_fallback_spots`。

### MealCurator

`meal_search_node` 用高德按 `types=050000`、`citylimit=true` 检索餐饮候选，并解析可空 `rating`、`avg_cost`。若 LLM 可用，MealCurator 只允许从真实候选池中挑选名称：

- `rating` 只来自高德，缺失时为 `null`。
- `signature_dishes` 和 `review_digest` 只来自博查检索片段；博查关闭或失败时留空。
- 因 `MealItem` schema 本阶段不扩展，增强信息合并写入 `MealCandidate.notes`。
- 校验失败或选择为空时回退高德规则候选；高德也不足时回退 `_fallback_meals`。

### Web Search

新增 `app/services/web_search_service.py`：

- 配置：`BOCHA_ENABLED=false`、`BOCHA_API_KEY`、`BOCHA_BASE_URL`、`BOCHA_TIMEOUT_SECONDS`。
- 对外函数返回标准化片段列表：`title`、`url`、`snippet`。
- 未启用、无 key、超时、非 2xx、接口错误均返回空列表。
- 使用 `cache_service` 缓存，缓存 key 至少包含查询文本。

### Critic

新增 `critic_node`，运行在 `budget_check` 之后、`summarize` 之前：

- 输入完整 `day_plans` 摘要、`budget_report`、用户约束和 `replan_count`。
- 输出 `verdict=accept|revise`、`score`、`issues`、`revise_hints`。
- LLM 不可用时只做只读降级，等价于接受当前行程，不触发额外回环。
- 达到 `TRIP_MAX_REPLAN` 时即使 LLM 返回 `revise` 也强制接受，避免死循环。

### Scheduler 消费 revise_hints

`schedule_node` 消费 `state.revise_hints`，只支持少量明确、可测试的 hint：

- 出现“减少景点”“景点过多”“减到 1/2 个”等语义时，降低每日景点上限。
- 出现“室内”“雨天”时，优先选择 `is_indoor=true` 的候选。
- 出现“轻松”“节奏”时，压低餐饮和交通预算系数，保持预算回环可收敛。

不让 Scheduler 解析复杂自然语言，也不让 Critic 直接修改 `DayPlan`。

### Graph 收敛

在 `graph.py` 定义一份本地执行阶段：

```text
INITIAL_NODES = dispatch, spots, meals, transport, weather
REPLAN_NODES = schedule, hotel, budget, critic
FINAL_NODES = summarize
```

`_run_local_graph` 和 `stream_trip_graph_events` 共享这些阶段定义。`build_trip_graph()` 保留 LangGraph 编译能力，但节点顺序与本地阶段保持一致。回环路由统一检查预算超支和 Critic revise，且都受 `TRIP_MAX_REPLAN` 限制。

## 数据模型

新增内部 Pydantic 模型，放在 `app.models.schemas` 或靠近节点的模块中：

- `CoordinatorResponse`
- `SpotCuratorResponse`
- `MealCuratorResponse`
- `CriticResponse`

新增状态字段：

- `NormalizedDemand.meal_keywords`
- `PlanningStrategy`
- `TripState.planning_strategy`
- `TripState.revise_hints`
- `TripState.critic_report`

`MealCandidate` 增加可空 `rating` 和 `avg_cost`，不影响现有消费者。

## 降级策略

所有 LLM 节点统一满足：

- `USE_LLM_AGENTS=false` 时走纯规则。
- LLM 无 key、超时、HTTP 错误、坏 JSON、Pydantic 校验失败、候选名称不在候选池时降级。
- 降级不抛给 API 用户，不改变对外 schema。
- LLM 成功时把本次 usage 写入 `_tokens`，并累加到 `state.token_usage.planner_prompt_tokens` 和 `planner_completion_tokens`。

## 测试与验收

自动测试使用 monkeypatch 模拟 LLM 结构化结果，不调用真实模型。

必须覆盖：

- `search_places` 请求参数包含 `types`、`citylimit`，缓存 key 区分新增参数，并解析 `biz_ext.rating/cost`。
- Coordinator 成功时扩展 `spot_keywords`/`meal_keywords` 并累加 token；无 key 时降级为当前规则。
- SpotCurator 只接受候选池内名称；若 LLM 返回外部名称则降级。
- MealCurator 只接受候选池内名称，并把评分/博查摘要合并进 notes；博查失败时不阻断。
- Critic 返回 `revise` 时写入 `revise_hints` 并让 graph 回到 Scheduler；到达上限后强制接受。
- Scheduler 在明确 hint 下减少每日景点数或优先室内。
- collect 与 stream 共享同一节点阶段，新增 Critic 事件不破坏最终 done payload。
- `trip_service.py` legacy fallback 仍能生成行程，预算和 tips 行为不退化。

可选真实验证：

- 配置 `USE_LLM_AGENTS=true` 和真实 `LLM_API_KEY` 后，非大理/厦门城市能生成非模板化主题/景点/餐饮文案。
- `planner_prompt_tokens` 或 `planner_completion_tokens` 大于 0。
