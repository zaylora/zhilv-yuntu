# 旅游攻略 Agent 编排重构计划（LangGraph 版）

> 依据：`旅游攻略Agent编排-改进版.excalidraw`
> 范围：后端 Agent 编排从「单次 LLM 调用 + 规则拼装」彻底重构为「LangGraph 多 Agent 编排」，从架构到实现全部重做。
> 日期：2026-06-15

---

## 一、现状分析与差距

### 1.1 当前实现（实际代码）

| 模块 | 现状 | 文件 |
| --- | --- | --- |
| "Agent" | 本质是**单次 LLM 调用**，让模型一次性吐出每天 1 景点/1 餐/1 备注的 JSON 草稿 | `app/agents/trip_planner_agent.py` |
| 行程拼装 | **纯规则**：预算按比例分摊、门票按关键词估算、酒店/交通/餐饮按权重 prorate | `app/services/trip_service.py` |
| 地图 | 生成完后**可选**地一次性 enrich（geocode + POI + 路线） | `app/services/map_service.py` |
| 天气 | 有独立 service 和接口，但**完全不参与行程生成**（前端单独调用） | `app/services/weather_service.py` |
| RAG | query 改写 → 向量检索 → rerank，能力完整 | `app/rag/` `app/agents/tools/rag_tool.py` |
| 编辑 | 单日编辑同样是一次 LLM 调用 | `trip_service.edit_trip_itinerary` |

### 1.2 设计图要求的目标流程

```
① 用户输入(城市/起止日期/人数/偏好/预算)
        │
② 任务派发 Agent（需求标准化改写 + 并行派发）
        │  ── 并行 fan-out ──
   ┌────────────┬────────────┬────────────┬────────────┐
   ③a 游玩地检索   ③b 餐饮检索   ③c 交通检索   ③d 天气查询
   候选景点+坐标    候选餐厅池     大/市内交通    预报；远期降级
   不够则补搜        按饮食偏好                  季节/历史气候
   (有退出条件)                              [工具:高德天气API]
   [高德POI]      [点评/搜索]   [高德/搜索]
        └────────────┴─────┬──────┴────────────┘
                           │ (天气影响排程 / 每天活动中心点)
④ 行程编排 Agent ★新增
   坐标聚类 → 分天 → 顺路排序；雨天换室内；候选餐厅按天就近分配
   [工具:高德地图API POI/周边搜索]
        │  (每天活动中心点)
⑤ 住宿 Agent —— 按每天活动中心点选酒店
        │
⑥ 预算核算+校验 Agent ★新增
   门票+住宿+餐饮+交通是否超支；schema校验/缺失补偿
   ── 超支则回退重排 ──┐(回到 ④)
        │ (校验通过)    │
⑦ 汇总 Agent ──────────┘
   按已排好的结构写每天攻略 → 输出最终攻略
```

### 1.3 核心差距（必须重做的点）

1. **没有编排框架**：当前是串行函数调用，缺少状态机、条件边、并行节点、回退环。
2. **没有真正的并行检索**：景点/餐饮/交通/天气应并行 fan-out，现状只有一次 LLM。
3. **天气不参与排程**：设计要求"雨天换室内""远期降级历史气候"，现状天气是孤立接口。
4. **没有行程编排算法**：设计要求"坐标聚类→分天→顺路排序"，现状是按天硬塞 1 景点。
5. **没有住宿就近选择**：设计要求按"每天活动中心点"选酒店，现状是 `f"{destination} 住宿 N"` 占位。
6. **没有预算校验回退环**：设计要求超支回退重排，现状只做事后比例分摊。
7. **检索无"补搜/退出条件"**：设计要求候选不足时补搜并有终止条件。

---

## 二、目标架构

### 2.1 技术选型

| 关注点 | 选型 | 说明 |
| --- | --- | --- |
| 编排框架 | **LangGraph**（`langgraph`） | `StateGraph` + 条件边 + 并行节点 + 检查点 |
| 状态 | `TypedDict` + Pydantic 子模型 | 全局 `TripState` 贯穿所有节点 |
| LLM | 复用现有 `ChatOpenAI`（OpenAI 兼容） | 复用 `config.py` 的 LLM_* |
| 结构化输出 | `with_structured_output` / Pydantic | 替换现有手写 `_extract_json_object` |
| 工具 | 高德 POI/周边/路线/天气、RAG、（可选）联网搜索 | 大量复用现有 `map_service`/`weather_service`/`rag` |
| 流式进度 | LangGraph `astream` (`stream_mode="updates"`) → SSE | 前端展示"哪个 Agent 在跑" |
| 持久化 | 现有 SQLite + Redis 缓存 | 不变 |

新增依赖（`requirements.txt`）：
```
langgraph>=0.2,<0.4
langgraph-checkpoint>=2.0,<3.0   # 可选：断点续跑
```

### 2.2 全局状态 `TripState`

```python
# app/agents/state.py
class TripState(TypedDict, total=False):
    # —— 输入 ——
    request: TripRequest
    day_count: int

    # —— ② 派发：标准化后的检索意图 ——
    normalized: NormalizedDemand        # 改写后的检索关键词/约束/每类 query

    # —— ③ 并行检索产物（候选池，未排程）——
    spot_candidates: list[SpotCandidate]      # 带坐标，可补搜
    meal_candidates: list[MealCandidate]      # 餐厅候选池
    transport_options: TransportPlan          # 大交通+市内交通
    weather: WeatherContext                   # 逐日预报 or 季节降级

    # —— ④ 编排产物 ——
    day_plans: list[DayPlan]            # 已聚类/分天/排序，含每天中心点
    day_centroids: list[GeoPoint]       # 每天活动中心点（喂给住宿 Agent）

    # —— ⑤ 住宿 ——
    # 直接写回 day_plans[i].hotel

    # —— ⑥ 预算校验 ——
    budget_report: BudgetReport         # 是否超支/缺失项/校验结果
    replan_count: int                   # 回退重排次数（防死循环）

    # —— ⑦ 汇总 ——
    itinerary: Itinerary                # 最终产物

    # —— 横切 ——
    token_usage: TokenUsage             # 累加各节点 token
    errors: list[str]                   # 节点降级/失败记录
    trace: list[str]                    # 进度事件（供 SSE）
```

> 设计原则：每个检索 Agent 只产出**候选池**，"分天/排序/分配"全部交给 ④ 行程编排 Agent，符合设计图职责划分。

### 2.3 LangGraph 图结构

```python
graph = StateGraph(TripState)
graph.add_node("dispatch", dispatch_node)            # ②
graph.add_node("spots", spot_search_node)            # ③a
graph.add_node("meals", meal_search_node)            # ③b
graph.add_node("transport", transport_search_node)   # ③c
graph.add_node("weather", weather_node)              # ③d
graph.add_node("schedule", schedule_node)            # ④
graph.add_node("hotel", hotel_node)                  # ⑤
graph.add_node("budget", budget_check_node)          # ⑥
graph.add_node("summarize", summarize_node)          # ⑦

graph.set_entry_point("dispatch")
# ② → 并行 fan-out 到四个检索节点
for n in ("spots", "meals", "transport", "weather"):
    graph.add_edge("dispatch", n)
    graph.add_edge(n, "schedule")   # 四路汇聚（join）到 ④
graph.add_edge("schedule", "hotel")
graph.add_edge("hotel", "budget")
# ⑥ 条件边：超支且未超重排上限 → 回 ④；否则 → ⑦
graph.add_conditional_edges("budget", budget_router,
                            {"replan": "schedule", "ok": "summarize"})
graph.add_edge("summarize", END)
```

> LangGraph 的"多入边节点"会自动等待所有上游完成（join），天然实现"四路并行检索 → 汇聚到编排"。并行节点对 `TripState` 的写入需用 `Annotated[..., operator.add]` 或写入互不冲突的独立键（本设计中每个检索节点写各自的键，无冲突）。

---

## 三、节点（Agent）详细设计

### ② dispatch_node —— 任务派发 Agent
- **职责**：把用户原始输入标准化改写，生成各检索通道的 query/约束，并设置 `day_count`。
- **输入**：`request`
- **输出**：`normalized`（含：景点检索关键词、饮食偏好归一、交通诉求、城市规范名）
- **实现**：1 次 LLM（结构化输出）。复用并扩展现有 `rag_tool.llm_rewrite_query` 的改写思路。
- **降级**：LLM 不可用 → 规则改写（复用 `_rule_based_query`）。

### ③a spot_search_node —— 游玩地检索 Agent
- **职责**：产出**带坐标**的候选景点池，候选不足时**补搜**，有**退出条件**。
- **工具**：`map_service.search_places`（高德 POI）+ `rag` 检索（本地攻略补充语义）。
- **退出条件**：`len(candidates) >= max(day_count*3, 8)` 或 补搜轮数 ≥ 2 或 无新增。
- **输出**：`spot_candidates`（name/坐标/poi_id/类型/室内外标记/门票估算）。
- **关键新增**：给每个候选打 `is_indoor` 标记（博物馆/寺/室内馆=室内），供 ④ 雨天替换使用。

### ③b meal_search_node —— 餐饮检索 Agent
- **职责**：按饮食偏好搜集**候选餐厅池**（不分天，纯候选）。
- **工具**：高德 POI（餐饮类目）+（可选）联网/点评搜索 + RAG 本地特色餐饮。
- **输出**：`meal_candidates`（name/坐标/人均/菜系/适配的 dietary 标签）。

### ③c transport_search_node —— 交通检索 Agent
- **职责**：大交通（城际到达方式建议）+ 市内交通方案。
- **工具**：高德路线（`estimate_route`）/（可选）联网搜索。
- **输出**：`transport_options`（市内默认模式、起点枢纽、单价模型）。

### ③d weather_node —— 天气查询 Agent
- **职责**：获取出行期逐日预报；**远期自动降级**为季节/历史气候描述。
- **工具**：`weather_service.get_weather_forecast`（高德天气，仅未来 ~4 天）。
- **降级逻辑**：日期超出预报窗口 → 用月份映射季节气候（规则表，不调 API）。
- **输出**：`weather`（逐日 `{date, is_rainy, condition, temp_range, source: forecast|seasonal}`）。

### ④ schedule_node —— 行程编排 Agent ★新增（核心）
- **职责**：把候选池**编排成逐日行程**。
- **算法步骤**：
  1. **坐标聚类**：对 `spot_candidates` 按经纬度做地理聚类（KMeans/网格聚类，`k=day_count`）。
  2. **分天**：每个聚类 = 一天，计算**每天活动中心点** `day_centroids`。
  3. **顺路排序**：天内景点按最近邻/简化 TSP 排序，生成时间段。
  4. **天气调整**：该天 `is_rainy` → 优先把室内候选（`is_indoor`）排到前面，替换户外主景点。
  5. **餐饮就近分配**：`meal_candidates` 按到当天中心点的距离分配到每天（午/晚餐）。
  6. **交通段生成**：相邻景点用 `estimate_route` 估距离/耗时/费用。
- **工具**：高德地图 API（POI/周边搜索/路线）。
- **重排入口**：被 ⑥ 回退时，依据 `budget_report` 收紧候选（降档/减项）后重排。
- **输出**：`day_plans` + `day_centroids`。

### ⑤ hotel_node —— 住宿 Agent
- **职责**：按**每天活动中心点**选酒店（多天则就近/居中选 1-2 个落脚点）。
- **工具**：高德周边搜索（中心点坐标 + 酒店类目 + 档次过滤）。
- **输出**：写回 `day_plans[i].hotel`（含坐标/档次/估价）。

### ⑥ budget_check_node —— 预算核算 + 校验 Agent ★新增
- **职责**：
  - 核算 门票+住宿+餐饮+交通 总额，判断是否超 `request.budget`。
  - **schema 校验**：每天必须有景点/餐饮/住宿/交通，缺失则**补偿**（补占位或触发补搜）。
- **路由**：
  - 超支 且 `replan_count < MAX_REPLAN(=2)` → 返回 `"replan"`（`replan_count += 1`，回 ④）。
  - 校验通过 或 已达重排上限 → 返回 `"ok"`（达上限时记录 `errors`，按比例兜底压缩预算）。
- **输出**：`budget_report` + 更新后的 `day_plans`。

### ⑦ summarize_node —— 汇总 Agent
- **职责**：按已排好的结构，为每天写攻略文案，生成 `summary`/`tips`，组装最终 `Itinerary`。
- **实现**：1 次 LLM（输入已结构化的 day_plans，只写文案不改结构）。
- **输出**：`itinerary`（含累加 `token_usage`）。

---

## 四、目录结构重组

```
backend/app/
├── agents/
│   ├── state.py              # 新增：TripState、各候选/上下文子模型
│   ├── graph.py              # 新增：StateGraph 装配 + 编译 + 运行入口
│   ├── nodes/                # 新增：每个节点一个文件
│   │   ├── dispatch.py       # ②
│   │   ├── spot_search.py    # ③a
│   │   ├── meal_search.py    # ③b
│   │   ├── transport_search.py # ③c
│   │   ├── weather.py        # ③d
│   │   ├── schedule.py       # ④（含聚类/排序算法）
│   │   ├── hotel.py          # ⑤
│   │   ├── budget_check.py   # ⑥
│   │   └── summarize.py      # ⑦
│   ├── tools/
│   │   ├── rag_tool.py       # 复用（小改：返回结构）
│   │   ├── amap_tool.py      # 新增：薄封装 map_service 给节点用
│   │   └── search_tool.py    # 新增(可选)：联网/点评搜索
│   └── algorithms/           # 新增：纯算法，便于单测
│       ├── cluster.py        # 坐标聚类
│       └── routing.py        # 顺路排序/最近邻
├── services/                 # 大部分复用
│   ├── map_service.py        # 复用（已具备 search_places/estimate_route/geocode）
│   ├── weather_service.py    # 复用 + 新增季节降级函数
│   ├── cache_service.py      # 复用
│   ├── storage_service.py    # 复用
│   ├── export_service.py     # 复用
│   └── trip_service.py       # 重构为「调用 graph 的瘦封装」
├── models/schemas.py         # 演进（见第五节）
└── api/                      # 接口基本不变，新增 SSE 进度（见第六节）
```

> 旧 `trip_planner_agent.py` 的逻辑拆分迁移到 `nodes/` 后删除（其 token 提取、JSON 提取工具函数可抽到 `agents/llm_utils.py` 复用）。

---

## 五、数据模型（schemas）演进

**对外契约（`Itinerary`/`DayPlan`/`SpotItem`...）保持不变**，确保前端与存储零改动。新增**内部中间模型**（放 `agents/state.py`，不入库）：

| 新增模型 | 字段要点 |
| --- | --- |
| `NormalizedDemand` | spot_keywords, dietary_norm, transport_intent, city_canonical |
| `SpotCandidate` | name, lat, lon, poi_id, category, is_indoor, ticket_est |
| `MealCandidate` | name, lat, lon, cuisine, avg_price, dietary_tags |
| `TransportPlan` | intercity_advice, intracity_default_mode, hub |
| `WeatherContext` | days: list[{date, is_rainy, condition, temp_range, source}] |
| `GeoPoint` | lat, lon |
| `BudgetReport` | total, breakdown, over_budget, missing_items, passed |

**`SpotItem` 小幅扩展**（可选、向后兼容）：增加 `is_indoor: bool | None`，用于前端标识雨天替代项。

**`TokenUsage` 扩展**：现有字段偏 RAG 流水线（rewrite/embedding/planner/rerank）。新增按节点维度累加，建议加：
```python
dispatch_*  spot_*  meal_*  transport_*  schedule_*  hotel_*  budget_*  summarize_*
```
或更简洁地保留 totals + 新增 `by_node: dict[str, NodeTokens]`。**保持 `total_*` 属性接口不变**，前端 token 统计不受影响。

---

## 六、API 与流式输出改造

- `POST /trip/generate`：内部改为 `run_trip_graph(request)`，**返回结构不变**（`Itinerary`）。
- 新增 `POST /trip/generate/stream`（SSE）：用 `graph.astream(state, stream_mode="updates")` 把每个节点完成事件推给前端，展示"任务派发中→并行检索中→编排中→…"。事件用 `trace` 字段。
- `POST /trip/edit`：重构为**子图**或独立小图（dispatch→schedule→budget→summarize 的局部重跑），替换现有单日 LLM 编辑。
- `weather` / `export` / 存储接口：**不变**。

---

## 七、流程监控与可观测性（Observability）

> 多 Agent 编排最大的痛点是「黑盒」：并行 fan-out 卡在哪个 Agent、为什么触发回退重排、token/费用花在哪、哪个工具在降级——必须做到**全程可观测**。本节是本次重构的一等公民，贯穿所有节点，不是事后补丁。

### 7.1 监控三个层次

| 层次 | 监控什么 | 落点 |
| --- | --- | --- |
| **节点级（流程）** | 每个 Agent 节点的 开始/结束/耗时/状态(成功·降级·失败)/输入输出摘要 | `NodeTrace` + LangSmith run |
| **工具级** | 高德 POI/天气/路线、RAG、LLM 的调用次数、缓存命中、失败率、耗时 | 工具封装层计数器 + 日志 |
| **业务级** | 候选数量、补搜轮数、重排次数、是否超支、token 成本、降级率 | `TripState` 指标字段 + LangSmith metadata |

### 7.2 统一节点监控装饰器（核心机制）

所有节点用同一个包装器接入监控，保证「每个 Agent 都被一致地计时、计 token、记 trace、上报 LangSmith、异常降级」，避免每个节点各写一套：

```python
# app/agents/monitoring.py
def monitored_node(node_name: str):
    """统一节点包装：计时 + token 累加 + trace 事件 + LangSmith 标记 + 异常降级。"""
    def decorator(fn):
        @traceable(run_type="chain", name=f"trip.{node_name}")  # 复用现有 langsmith
        @functools.wraps(fn)
        def wrapper(state: TripState) -> dict:
            t0 = perf_counter()
            emit_event(state, node_name, status="running")        # → SSE
            try:
                patch = fn(state)                                  # 节点真正逻辑
                status = patch.get("_node_status", "success")     # success|degraded
            except Exception as exc:                               # 节点失败不崩整图
                logger.exception("node %s failed", node_name)
                _tag_run(outcome="node_error", node=node_name, error_type=type(exc).__name__)
                patch = {"errors": [f"{node_name}: {exc}"], "_node_status": "failed"}
                status = "failed"
            elapsed = round((perf_counter() - t0) * 1000)
            trace = NodeTrace(node=node_name, status=status, elapsed_ms=elapsed,
                              tokens=patch.get("_tokens"), note=patch.get("_note"))
            _tag_run(outcome=status, node=node_name, elapsed_ms=elapsed, **(patch.get("_tokens") or {}))
            emit_event(state, node_name, status=status, elapsed_ms=elapsed)  # → SSE
            return {**patch, "trace": [trace]}   # trace 用 operator.add 在 state 里累积
        return wrapper
    return decorator
```

- `trace`、`errors`、`token_usage` 在 `TripState` 中声明为 `Annotated[list, operator.add]` / 可累加，**并行节点各自追加、自动合并**，无写冲突。
- 复用现有 `app/agents/.../trip_planner_agent.py` 已有的 `@traceable` 与 `_tag_run`（带 import 守卫，未装 langsmith 时静默降级），抽到 `agents/llm_utils.py`/`monitoring.py` 全节点共用。

### 7.3 监控数据模型

```python
class NodeTrace(BaseModel):
    node: str                  # 节点名（spots/meals/schedule/...）
    status: str                # running|success|degraded|failed
    elapsed_ms: int
    tokens: dict | None        # 该节点 token
    note: str | None           # 降级原因/关键指标，如 "weather=seasonal_fallback"

class RunMetrics(BaseModel):       # 整次编排汇总（随 Itinerary 一起可返回/入库）
    total_elapsed_ms: int
    replan_count: int              # 预算回退重排次数
    spot_search_rounds: int        # 景点补搜轮数
    candidate_counts: dict         # {spots, meals, ...}
    degraded_nodes: list[str]      # 走了降级路径的节点
    tool_calls: dict               # {amap_poi: n, amap_route: n, weather: n, rag: n, llm: n}
    cache_hits: dict               # 各工具缓存命中数
    token_usage: TokenUsage
```

### 7.4 实时进度推送（SSE）

`POST /trip/generate/stream` 用 `graph.astream(state, stream_mode="updates")`，把 `monitored_node` 发出的事件实时推给前端：

```jsonc
// 每个 SSE 事件
{"type":"node","node":"spots","status":"running","message":"正在检索候选景点…","ts":...}
{"type":"node","node":"spots","status":"success","elapsed_ms":820,"detail":{"candidates":11}}
{"type":"node","node":"budget","status":"replan","detail":{"over_budget":true,"replan_count":1}}
{"type":"done","metrics":{...RunMetrics...}}
```

前端据此展示编排流水线状态（②派发中→③并行检索中→④编排中→⑥校验/重排中→⑦汇总中），让"流程"可见。

### 7.5 工具调用监控

- 在 `agents/tools/amap_tool.py`、`weather`、`rag` 封装层统一打点：调用计数、耗时、成功/失败、**缓存命中**（复用 `cache_service` 已有的 `cache hit/miss` 日志）。
- 累加进 `RunMetrics.tool_calls` / `cache_hits`，既能算成本，也能定位"是不是某个外部 API 在拖慢/失败"。

### 7.6 结构化日志

- 统一 `logging` 配置：每条日志带 `trip_id` / `run_id` 作为关联键，贯穿一次编排的所有节点与工具调用，便于按一次请求串起全链路。
- 关键事件统一前缀（如 `[node:schedule]`、`[tool:amap_poi]`、`[replan]`），生产环境可切 JSON 日志接入 ELK/Loki。

### 7.7 LangSmith 看板维度（复用现有接入）

继续用现有 LangSmith 追踪，整次编排是一个 root trace，每个节点是子 run，靠 `_tag_run` 写 metadata，可在 LangSmith 直接筛选/统计：

- **降级率**：`outcome in (llm_not_configured, *_fallback, node_error)` 的占比（沿用现有 fallback 率统计思路）。
- **重排率**：`replan_count > 0` 的请求占比。
- **节点耗时分布 / token 成本分布**：按 `node` 维度聚合。
- **失败热点**：哪个 `node` / `error_type` 最常失败。

### 7.8 指标落库（可选）

`RunMetrics` 随行程一并存 SQLite（扩展 `db_models`/`storage_service`），新增 `GET /trip/{id}/metrics` 与现有 `GET /trip/stats`（token 统计）并列，形成最小自带看板，不强依赖 LangSmith。

---

## 八、前端配合改动（最小化）

| 改动 | 说明 | 必需性 |
| --- | --- | --- |
| 进度展示 | 接 SSE，展示各 Agent 阶段 | 可选（推荐） |
| 雨天标记 | `is_indoor`/天气替换的 UI 提示 | 可选 |
| 其余 | `Itinerary` 契约不变，结果页/地图/历史/导出**无需改动** | — |

> 前端 `frontend/src/types/index.ts` 若新增 `is_indoor` 需同步类型，但非破坏性。

---

## 九、复用 / 重写 / 新增清单

| 处理 | 模块 |
| --- | --- |
| ✅ 直接复用 | `map_service`(geocode/search_places/estimate_route)、`cache_service`、`storage_service`、`export_service`、`rag/`(retriever/vector_db)、`config.py` |
| 🔧 小改复用 | `weather_service`(+季节降级)、`rag_tool`(返回结构适配)、`schemas`(加内部模型/扩展字段) |
| ♻️ 重写 | `trip_service.py`(变瘦封装)、`trip_planner_agent.py`(拆解后删除) |
| 🆕 新增 | `agents/state.py`、`agents/graph.py`、`agents/nodes/*`、`agents/algorithms/*`、`amap_tool.py`、SSE 接口 |

---

## 十、分阶段实施里程碑

> 每个阶段结束都应可运行 + 通过测试，避免大爆炸式重构。

### M0 — 脚手架 + 监控底座（0.5d）
- 加依赖 `langgraph`；建 `state.py`、`graph.py` 空骨架；`trip_service.generate_trip_itinerary` 切到调用 graph（节点先全用现有规则逻辑填充，**行为对齐旧版**）。
- **先落监控底座**：`agents/monitoring.py` 的 `monitored_node` 装饰器 + `NodeTrace`/`RunMetrics` + `trace`/`errors` 可累加 state 键（抽取现有 `@traceable`/`_tag_run`）。后续节点一上来就被监控覆盖。
- ✅ 验收：现有 6 个 `tests/` 全绿，生成结果与旧版基本一致；每个节点产出 `trace` 事件、LangSmith 能看到子 run。

### M1 — 并行检索骨架（1d）
- 实现 ② dispatch + ③a/b/c/d 四个检索节点（真实工具调用 + 降级）。
- ④ schedule 先用"简单分天"占位（不聚类）。
- ✅ 验收：候选池非空；天气节点能预报+降级；并行 join 正常。

### M2 — 行程编排算法（1.5d）★重点
- 实现坐标聚类 + 顺路排序 + 雨天替换 + 餐饮就近分配（`algorithms/` 纯函数 + 单测）。
- ✅ 验收：多天行程地理上成团、天内顺路、雨天有室内替换。

### M3 — 住宿 + 预算校验回退环（1d）★重点
- ⑤ hotel 按中心点选店；⑥ budget 校验 + 条件回退（`MAX_REPLAN` 防死循环）。
- ✅ 验收：构造超支用例触发 1 次重排后收敛；缺失项被补偿。

### M4 — 汇总 + SSE 进度 + 编辑子图（1d）
- ⑦ summarize 写文案；`/generate/stream` SSE（消费 `monitored_node` 事件流，见第七节）；`/edit` 改子图；汇总 `RunMetrics` 随结果返回。
- ✅ 验收：端到端产出完整 `Itinerary`；前端能实时看到各 Agent 阶段状态与重排事件。

### M5 — 清理与文档（0.5d）
- 删除 `trip_planner_agent.py`；更新 README/CHANGELOG；补 `.env.example`。

**总计约 5.5 人日。**

---

## 十一、测试策略

- **算法单测**（最高价值，无需 LLM/网络）：聚类、顺路排序、餐饮分配、预算回退判定、天气季节降级。
- **节点单测**：mock 工具（map/weather/rag/LLM），验证每个节点输入→输出契约与降级路径。
- **图集成测试**：mock 全部外部调用，跑完整 graph，断言 `Itinerary` schema 合法、预算不超、每天结构完整。
- **回归**：保留并适配现有 `tests/test_services_trip.py`、`test_api_trip.py`。
- **监控测试**：断言每个节点产出 `NodeTrace`、状态正确（含降级/失败路径）、`trace` 在并行节点下正确合并、`RunMetrics` 字段（重排次数/工具调用数/token）准确。
- **真实联调脚本**：复用 `scripts/test_trip_planner_agent_real.py` 思路新增 graph 真跑脚本，打印 `RunMetrics` 全链路指标。

---

## 十二、配置与环境变量（新增）

```env
# 编排控制
TRIP_MAX_REPLAN=2                 # 预算超支最大回退重排次数
TRIP_SPOT_MIN_CANDIDATES=8        # 景点候选退出阈值
TRIP_SPOT_MAX_SEARCH_ROUNDS=2     # 景点补搜最大轮数
TRIP_ENABLE_WEB_SEARCH=false      # 是否启用联网/点评搜索通道
WEATHER_FORECAST_MAX_DAYS=4       # 超出则走季节降级

# 监控 / 可观测性
LANGCHAIN_TRACING_V2=true         # 开启 LangSmith 追踪（未配则静默降级）
LANGCHAIN_API_KEY=...             # LangSmith Key
LANGCHAIN_PROJECT=zhilv-yuntu     # LangSmith 项目名
TRIP_LOG_LEVEL=INFO               # 结构化日志级别
TRIP_METRICS_PERSIST=true         # 是否把 RunMetrics 落库
```
（高德 `AMAP_*`、LLM_*、Redis_* 复用现有配置。`ENABLE_AMAP_ENRICHMENT` 将被编排内置调用取代，可保留为总开关。）

---

## 十三、风险与回退

| 风险 | 缓解 |
| --- | --- |
| 高德 Key 未配 / 限流 | 每个检索节点都有规则降级（无坐标时退化为旧版行为） |
| 并行节点状态写冲突 | 各节点写**独立 state 键**，join 在 ④ 统一消费 |
| 预算回退死循环 | `MAX_REPLAN` 硬上限，达上限走比例兜底压缩 |
| LLM 结构化输出不稳定 | `with_structured_output` + 失败降级到规则节点 |
| 重构破坏前端契约 | `Itinerary` 对外 schema 冻结，新增字段全部可选 |
| token 统计回归 | `TokenUsage.total_*` 属性接口保持不变 |

**总回退开关**：`trip_service` 保留 `USE_LANGGRAPH`（默认 true）环境开关，异常时可一键切回旧版生成函数（M5 前不删旧逻辑）。

---

## 十四、与设计图的对应核对表

| 设计图节点 | 本计划落点 | 工具 |
| --- | --- | --- |
| ① 用户输入 | `TripRequest` | — |
| ② 任务派发 | `dispatch_node` | LLM 改写 |
| ③a 游玩地检索（补搜/退出） | `spot_search_node` | 高德 POI + RAG |
| ③b 餐饮检索 | `meal_search_node` | 高德 POI + 搜索/点评 |
| ③c 交通检索 | `transport_search_node` | 高德路线 + 搜索 |
| ③d 天气查询（远期降级） | `weather_node` | 高德天气 API |
| ④ 行程编排（聚类/排序/雨天/餐饮分配）★ | `schedule_node` + `algorithms/` | 高德 POI/周边/路线 |
| ⑤ 住宿（按中心点） | `hotel_node` | 高德周边搜索 |
| ⑥ 预算核算+校验（超支回退）★ | `budget_check_node` + 条件边 | — |
| ⑦ 汇总 | `summarize_node` | LLM 文案 |

> 设计图全部要素均已覆盖，含两个 ★新增 Agent 与"天气影响排程""每天活动中心点""超支则回退重排"三条关键数据流/控制流。
