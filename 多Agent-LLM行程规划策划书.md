# 智旅云途 · 多 Agent + LLM 行程规划 策划书

> 版本：v1.1　|　日期：2026-06-16　|　范围：后端 `backend/app`
>
> 本策划书基于对现有源码的逐文件核实编写，所有"现状"描述均来自真实代码。

---

## 一、背景与目标

### 1.1 现状（基于源码核实）

当前 `app/agents` 是一条 **9 节点 LangGraph 固定流水线**：

```
dispatch → (spots / meals / transport / weather 并行) → schedule → hotel → budget_check → summarize
```

其中 `budget_check → schedule` 是超预算重排回环（最多 `TRIP_MAX_REPLAN=2` 次）。

关键事实：

- **没有任何一个节点调用 LLM**。`config.py` 里的 `LLM_PROVIDER / LLM_API_KEY / LLM_MODEL / LLM_BASE_URL` 全部闲置。
- 所有"智能"都是规则：景点靠关键词黑白名单过滤 + 内置 `KNOWN_DESTINATION_SPOTS`（只有大理 / 厦门），餐饮靠 `KNOWN_MEALS`，文案靠模板字符串。
- 已有工具能力（可被 LLM function calling 直接复用）：`search_places`、`geocode_address`、`estimate_route`、`get_weather_forecast`。
- `TokenUsage` schema 里已预留 `planner_prompt_tokens / planner_completion_tokens` 字段，目前恒为 0；`monitored_node` 装饰器已经留了 `_tokens` 钩子——**接 LLM 的管道是现成的**。
- 技术债：`graph.py` 有三套近乎重复的拓扑（`_run_local_graph` / `stream_trip_graph_events` / `build_trip_graph`）；`trip_service.py` 重复实现了一遍 `rules.py` 的算法。

### 1.2 目标

1. 把"伪 Agent"升级成**真正带 LLM 推理的多 Agent 协作**，显著提升非大理/厦门城市、复杂偏好、特殊要求（`special_notes`）的行程质量。
2. **不破坏对外契约**：`TripRequest` 进、`Itinerary` 出，前端 SSE 流式接口 `/trip/generate/stream` 行为不变。
3. **LLM 不可用时自动降级**到现有规则，保证零 key、断网、超时都能出结果。
4. 顺手收敛三套拓扑为一套、消除 `trip_service` 的重复算法。

---

## 二、设计原则：LLM 与规则的分工

| 环节 | 谁来做 | 理由 |
|------|--------|------|
| 需求理解、隐含偏好挖掘、整体策略 | **LLM** | 自然语言 `special_notes` 解析是 LLM 强项 |
| 候选景点/餐饮的语义筛选与排序 | **LLM** | "适合带老人""避免爬山"这类语义判断规则做不好 |
| 行程文案：summary / tips / 每日 theme & notes | **LLM** | 纯文本生成，LLM 碾压模板 |
| 整体合理性评审（节奏/顺路/忌口冲突） | **LLM** | 需要综合推理 |
| 地理聚类、最近邻路径排序 | **规则（算法）** | 数学优化问题，LLM 不可靠 |
| 预算分摊 `prorate_amounts`、汇总核账 | **规则** | 必须精确到分，LLM 会算错 |
| POI 检索、地理编码、路线、天气、评分/人均 | **高德 API** | 事实数据，不能让 LLM 编；评分/人均是可空字段 |
| 招牌菜 / 口碑评论 | **博查 Web Search + LLM 提炼** | 网络检索取事实，LLM 只做摘要 |

> 一句话：**LLM 负责"判断和表达"，规则负责"计算和检索"。** 绝不让 LLM 算钱、编坐标。

---

## 三、目标架构

### 3.1 Agent 角色（其中 5 个用 LLM）

| Agent | 类型 | 职责 | 工具/依赖 |
|-------|------|------|-----------|
| **Coordinator 总控规划师** | LLM | 解析需求→输出规划策略（每日主题骨架、节奏、预算占比建议、扩展搜索关键词） | 无 |
| **SpotCurator 景点策展** | LLM+工具 | 调高德取候选→语义筛选/排序/判断室内外/估游玩时长 | `search_places(types=景点类, citylimit=true)` |
| **MealCurator 餐饮策展** | LLM+工具 | 高德评分挑选 + 博查检索招牌菜/口碑 + 结合忌口生成城市级餐饮候选池 | `search_places(types=餐饮类, citylimit=true)` + 博查 |
| **TransportPlanner 交通** | 规则 | 按节奏出交通方案（保留现状） | — |
| **WeatherAgent 天气** | API | 高德预报 + 季节兜底（保留现状） | `get_weather_forecast` |
| **Scheduler 排期师** | 规则 | 聚类 + 路径 + 预算分摊（保留现状，核心数值引擎） | cluster/routing/rules |
| **HotelPlanner 住宿** | 规则 | 档次占比 + 权重分摊（保留现状） | rules |
| **BudgetChecker 预算核账** | 规则 | 精确汇总 + 超支判定（保留现状） | — |
| **Critic 评审官** | LLM | 审查整趟行程→`accept` / `revise`（带可执行修改理由） | `revise_hints` 状态 |
| **Narrator 文案** | LLM | 生成 summary / tips / 每日 theme & notes | 无 |

### 3.2 拓扑

```
              ┌─→ SpotCurator(LLM) ─┐
Coordinator ──┼─→ MealCurator(LLM) ─┤
   (LLM)      ├─→ Transport(规则)  ─┼─→ Scheduler(规则) ─→ Hotel(规则) ─→ BudgetCheck(规则)
              └─→ Weather(API)    ─┘                                          │
                                                                              ▼
                                                            ┌──── Critic(LLM) 评审
                                              revise ◄──────┤
                                          (回 Scheduler)    └── accept ──→ Narrator(LLM) ─→ END
```

回环条件统一：`Critic 判 revise` **或** `BudgetCheck 判超支`，且 `replan_count < TRIP_MAX_REPLAN`。

> 关键约束：`MealCurator` 在 `Scheduler` 前运行时还不知道每天的活动中心点，因此只负责产出**城市级餐饮候选池**；真正的“按当天区域就近选择餐厅”仍由 `Scheduler` 基于 `day_centroids` 完成。若后续需要更精细的晚餐/午餐区域匹配，再新增一个 Scheduler 后置的餐饮精排节点。

---

## 四、各 Agent 详细设计与 Prompt

所有 LLM agent 统一约定：

- **强制 JSON 输出**（用 OpenAI 兼容的 `response_format={"type":"json_object"}` 或 function calling），返回后用 Pydantic 校验，**校验失败即降级**到规则。
- 输入里**禁止让 LLM 产出坐标/价格**（坐标来自高德，价格来自 `rules.py`）。
- 每次调用回传 usage，累加进 `state.token_usage`。

### 4.1 Coordinator 总控规划师

**输入**：`TripRequest` 全字段 + 计算出的 `day_count`。

**输出 JSON**：

```json
{
  "strategy": "整体策略一句话",
  "daily_themes": ["第1天主题", "第2天主题"],
  "pace_normalized": "轻松|适中|紧凑",
  "spot_keywords": ["扩展后的景点搜索关键词"],
  "meal_keywords": ["扩展后的餐饮搜索关键词"],
  "budget_hint": {"hotel": 0.5, "meals": 0.22, "transport": 0.14},
  "hard_constraints": ["从 special_notes 提炼的硬性约束"]
}
```

> `budget_hint` 只是给 Scheduler 的**建议比例**，实际分摊仍由 `prorate_amounts` 精确执行。

**System Prompt**：

```
你是资深旅行规划师，负责为一次旅行制定"总体策略"。你不安排具体景点和价格，只输出高层规划，供下游的检索和排期模块使用。

【你会收到】目的地、出行日期范围、天数、人数、总预算、旅行偏好标签、旅行节奏、饮食偏好/忌口、酒店档次、额外要求(special_notes)。

【你要做】
1. 用一句话概括整趟旅行的策略基调。
2. 为每一天拟定一个主题(daily_themes，数量必须等于天数)，主题要呼应偏好，并让相邻天的区域/强度有节奏地变化(避免连续两天都是高强度爬山)。
3. 把模糊的节奏归一化为 轻松/适中/紧凑 之一。
4. 围绕目的地和偏好，扩展出 6-10 个景点搜索关键词、4-6 个餐饮搜索关键词，关键词要具体可检索(如"大理 洱海 骑行""大理 白族 菜")。
5. 给出 hotel/meals/transport 三类预算占比建议(小数，相加约等于 0.86，其余留给门票和其他)。预算紧张时压低 hotel。
6. 从 special_notes 和忌口中提炼硬性约束(如"不吃辣""带老人少爬楼梯""第二天要赶高铁")。

【硬性规则】
- 绝不编造景点的真实名称、坐标、门票价格——那是下游模块的事。
- daily_themes 数量必须严格等于天数。
- 只输出 JSON，不要解释。
```

### 4.2 SpotCurator 景点策展

**流程**：先调 `search_places`（用 Coordinator 给的关键词，每个词取 5 条，限定景点相关 `types` 且 `citylimit=true`）得到候选池 → 把候选（名称/类型/地址/评分可空，**不含价格**）喂给 LLM 做筛选排序。

**输出 JSON**：

```json
{
  "selected": [
    {"name": "候选池里的原名", "reason": "选它的理由",
     "is_indoor": true, "suggested_hours": 2.5, "category": "景点"}
  ],
  "rejected_names": ["被排除的候选名+原因"]
}
```

> LLM 只能从**真实候选池**里挑（`name` 必须来自高德 POI 返回），坐标优先沿用 POI 坐标，价格由 `estimate_ticket_cost` 算；不要让 LLM 或地理编码结果覆盖已有 POI 坐标。

**System Prompt**：

```
你是目的地景点策展专家。系统会给你一批由地图服务真实检索到的候选地点，以及用户的偏好和硬性约束。你的任务是从候选中挑选并排序出最值得安排的景点。

【判断标准】
1. 契合用户偏好与每日主题。
2. 满足硬性约束(如"少爬山""适合带小孩")。
3. 多样性:避免选出多个高度同质的地点。
4. 为每个入选景点判断是否更适合室内(is_indoor，供雨天调整用)，并估计合理游玩时长(suggested_hours)。

【硬性规则】
- selected 里的 name 必须原样来自候选池，禁止改名或新增候选池里没有的地点。
- 不要输出价格、坐标、地址，这些由系统补全。
- 候选数量充足时，入选数量控制在 天数×3 左右；若候选明显不相关，可少选并在 rejected_names 说明。
- 只输出 JSON。
```

### 4.3 MealCurator 餐饮策展（高德评分 + 博查检索）

> **说明**：原计划使用大众点评 API，因接口不可得已放弃。改为「高德评分系统」做事实筛选 + 「博查 Web Search」做评论/菜品补充。

#### 数据来源

| 数据 | 来源 |
|------|------|
| 餐厅名称/地址/坐标/类型 | 高德 `search_places` |
| 评分 `rating`、人均 `avg_cost` | 高德 POI 的 `biz_ext` 字段（需在 map_service 补解析，且都可能为空） |
| 招牌菜、口碑摘要 | 博查 Web Search（可选增强，失败则跳过） |

#### 流程

```
1. 检索候选  → search_places(关键词, types=餐饮类, citylimit=true) → 得到城市级餐饮候选池
2. LLM 初筛  → 按忌口+评分/人均(可空)+本地特色挑出 N 家             → selected[]
3. 评论增强  → 对 selected 每家调博查检索招牌菜/口碑              → LLM 提炼进 notes(可选)
4. 按日就近  → Scheduler 基于 day_centroids 从 selected 中挑当天餐饮
```

#### 工具层改动（`map_service.py`）

把 `search_places` 从宽关键词搜索改成可限定类型和城市的工具：

```python
def search_places(
    keyword: str,
    city: str | None = None,
    page_size: int = 5,
    types: str | None = None,
    citylimit: bool = True,
) -> list[dict[str, Any]]:
    ...
```

请求高德 `/place/text` 时固定传：

```python
params = {
    "keywords": keyword,
    "city": city or AMAP_DEFAULT_CITY,
    "offset": page_size,
    "page": 1,
    "extensions": "all",
    "citylimit": "true" if citylimit else "false",
}
if types:
    params["types"] = types
```

- 餐饮检索传餐饮类 `types`（优先用高德 POI 类型码，如餐饮服务 `050000`）。
- 景点检索传景点/休闲/文化相关 `types`（如风景名胜 `110000`，必要时扩展体育休闲、科教文化等类型码）。
- 缓存 key 必须包含 `keyword / city / page_size / types / citylimit`，否则景点和餐饮搜索会互相污染缓存。

在 `search_places` 的结果 dict 里**新增两个可空字段**（高德 `extensions=all` 可能返回 `biz_ext`，当前未解析）：

```python
biz_ext = poi.get("biz_ext") or {}
"rating": _parse_float(biz_ext.get("rating")),   # 高德评分，如 4.5；可能为空
"avg_cost": _parse_float(biz_ext.get("cost")),   # 高德人均；可能为空
```

> 纯增量改动，景点检索也能顺带拿到评分，不影响现有调用方；但排序逻辑必须允许 `rating=None`、`avg_cost=None`。

#### 新增工具：博查检索（`app/services/web_search_service.py`）

```
POST https://api.bochaai.com/v1/web-search
Header: Authorization: Bearer {BOCHA_API_KEY}
        Content-Type: application/json
Body:   {"query": "大理 喜洲 砂锅鱼 招牌菜 评价", "summary": true, "count": 5}
取:     data.webPages.value[].{name, url, snippet}
```

- 新增配置：`BOCHA_API_KEY`、`BOCHA_ENABLED`（默认 `false`）、`BOCHA_TIMEOUT_SECONDS`、`BOCHA_BASE_URL`。
- 走现有 `cache_service` 缓存（key 如 `web:meal:{餐厅名}`），省钱省延迟。
- 无 key / 超时 / 失败 → 返回空，MealCurator 跳过增强步直接用高德数据。

#### 输出 JSON

```json
{
  "selected": [
    {
      "name": "候选原名(必须来自高德候选池)",
      "cuisine": "菜系",
      "rating": 4.5,
      "signature_dishes": ["招牌菜1", "招牌菜2"],
      "review_digest": "一句口碑摘要(来自博查检索，无则留空)",
      "dietary_ok": true,
      "reason": "选它的理由"
    }
  ]
}
```

> `rating` 来自高德（事实，可为空），`signature_dishes`/`review_digest` 来自博查检索后由 LLM 提炼——**LLM 只做摘要，不许凭空编**。如果保持现有 `MealItem` schema 不变，这些增强信息最终只能合并写入 `notes`。

**System Prompt**：

```
你是本地餐饮推荐专家。系统会给你：
- 一批由地图服务真实检索到的餐饮候选(含名称、类型、地址、评分、人均)；
- 针对部分候选的网络检索片段(网页标题与摘要，可能为空)；
- 用户的饮食偏好与忌口。

请从候选中挑选契合的餐饮，并在有网络片段时提炼招牌菜和口碑摘要。

【判断标准】
1. 严格规避忌口(如忌"辣"，排除以辣著称者)。
2. 优先评分高、契合目的地特色与用户口味者。
3. 先做城市级初筛；当日活动区域的就近性由 Scheduler 基于当天中心点做最终选择。

【提炼规则】
- signature_dishes 与 review_digest 只能来自给定的网络检索片段；没有片段就分别留空数组与空字符串，禁止凭空编造菜品或评价。
- rating 直接用候选给定的数值；如果候选没有评分，输出 null，不要自己补。

【硬性规则】
- name 必须原样来自候选池，不得新增或改名。
- 不输出坐标，不输出预估花费(由系统按规则计算)。
- 只输出 JSON。
```

#### 降级链（从强到弱）

1. 高德有评分 + 博查有评论 → 最佳：带评分、招牌菜、口碑。
2. 高德有评分 + 博查不可用 → 用评分辅助挑选，招牌菜/口碑留空。
3. 高德无评分但有餐饮 POI → 按类型、地址、忌口和本地特色挑选。
4. 高德也搜不到（非热门城市）→ 回退现有 `_fallback_meals`（内置 `KNOWN_MEALS` + 通用候选）。

### 4.4 Critic 评审官

**输入**：Scheduler+Hotel+BudgetCheck 产出的完整 `day_plans` 摘要 + `budget_report` + 原始约束。

**输出 JSON**：

```json
{
  "verdict": "accept | revise",
  "score": 0.0,
  "issues": ["发现的问题"],
  "revise_hints": ["给排期的具体调整建议"]
}
```

> Critic **只给意见不改数据**；`revise` 时把 `revise_hints` 写入 `state`，再回到 Scheduler 重排。Scheduler 必须显式消费这些 hint（例如“第2天景点减到2个”“雨天优先室内”“餐饮避开辣味”），否则 Critic 回环只会空转。受 `TRIP_MAX_REPLAN` 限制，达上限强制 accept，避免死循环。

**System Prompt**：

```
你是行程质检官。系统会给你一份已经排好的逐日行程摘要(含景点、餐饮、交通、住宿、预算明细)和用户的原始约束。请审查它是否合理，并裁定通过或需要返工。

【审查维度】
1. 节奏:每天强度是否匹配用户节奏，是否过满或过空。
2. 顺路:同一天景点是否扎堆合理，有没有明显折返。
3. 约束:是否违反硬性约束(忌口、带老人、赶车等)。
4. 预算:是否超支或某类占比异常。
5. 多样性:几天之间是否过度重复。

【裁定规则】
- 没有明显问题就给 accept，score 给 0.8 以上。
- 有可改进项给 revise，并在 revise_hints 给出"具体、可执行"的调整建议(如"第2天景点过多，减到2个")，不要泛泛而谈。
- 你只提建议，不直接改数据，也不要输出价格和坐标。
- 只输出 JSON。
```

### 4.5 Narrator 文案

**输入**：最终 `day_plans` + 约束。

**输出 JSON**：

```json
{
  "summary": "整趟概述",
  "tips": ["实用建议1", "建议2"],
  "day_titles": {"1": "第1天标题", "2": "..."},
  "day_notes": {"1": ["该天提示"], "2": ["..."]}
}
```

> 生成的 tips 仍要过现有 `clean_user_tips` 过滤掉"LLM/规则/源码"等技术词，保证不泄露实现细节。

**System Prompt**：

```
你是旅行行程文案撰写者。系统会给你一份排好的逐日行程。请为它撰写面向游客的友好文案。

【你要写】
1. summary:60-120字，概括这趟旅行的亮点与节奏。
2. tips:3-5条实用、具体的出行建议(天气、穿着、错峰、当地注意事项)，避免空话。
3. 每天的标题(day_titles)与1-2条当天提示(day_notes)。

【硬性规则】
- 只基于给定行程内容写，不要虚构未出现的景点或餐厅。
- 不要提及"LLM、模型、规则、系统、源码"等技术字眼。
- 语气亲切、简洁，面向普通游客。
- 只输出 JSON。
```

---

## 五、LLM 接入层设计（新增 `app/llm/`）

```
app/llm/
  __init__.py
  client.py       # 统一客户端：openai 兼容，封装 timeout/retry，返回 (text, usage)
  structured.py   # JSON 模式调用 + Pydantic 校验，失败抛专用异常
  registry.py     # 各 agent 的 system prompt 常量 / 模板集中管理
```

- 复用 `config.py` 现有 `LLM_*` 变量，新增一个总开关 `USE_LLM_AGENTS`（默认 `false`，渐进灰度）。
- `client.py` 单例 + 懒加载，**无 key 时不抛错，直接返回"不可用"信号**，让节点走降级。
- `structured.py` 统一负责"调用→解析 JSON→Pydantic 校验"，任一步失败抛 `LLMUnavailable`，节点捕获后降级。

### 5.1 高德 API 使用校准

高德接口可行，但需要比当前代码更收窄参数，避免宽关键词带来噪声结果。

| 能力 | 接口 | 当前可用性 | 需要补强 |
|------|------|------------|----------|
| POI 关键字搜索 | `/place/text` | 当前已用 `keywords/city/offset/page/extensions=all` | 增加 `types`、`citylimit`；解析 `biz_ext.rating/cost`；缓存 key 纳入新增参数 |
| 地理编码 | `/geocode/geo` | 当前用于地址/城市转坐标和 adcode | 城市天气取 adcode 可继续用；具体 POI 坐标优先用 POI 搜索结果 |
| 驾车路线 | `/direction/driving` | 当前可取 `paths[0].distance/duration` | 若依赖 `taxi_cost`，请求显式加 `extensions=all`；后续如要步行/公交需另接对应接口 |
| 天气预报 | `/weather/weatherInfo` | 当前 `extensions=all` 可取 `casts` | 仅适合近期预报；远期行程继续保留季节兜底 |

`estimate_route` 建议小改：

```python
payload = _request_amap(
    "/direction/driving",
    {
        "origin": f"{origin_longitude},{origin_latitude}",
        "destination": f"{destination_longitude},{destination_latitude}",
        "strategy": 0,
        "extensions": "all",
    },
)
```

> 不建议把高德返回的评分、人均、出租车费当作强一致数据。它们适合作为推荐排序和提示信息，预算核算仍以本地规则为准。

---

## 六、降级与容错（核心，不可省）

每个 LLM agent 都是**"LLM 优先 + 规则兜底"**的双路结构：

| Agent | LLM 失败时的兜底 |
|-------|-----------------|
| Coordinator | 用现有 `dispatch_node` 逻辑（关键词=目的地+偏好） |
| SpotCurator | 用现有 `is_relevant_spot_place` 黑白名单过滤 + `_fallback_spots` |
| MealCurator | 高德 POI 有结果则按类型/忌口/评分可空挑选 → 再不行回退 `_fallback_meals` |
| Critic | 退化为现有 `budget_router`（只看是否超支；若 Scheduler 未消费 hint，则 Critic 只做只读评审） |
| Narrator | 用现有 `summarize_node` 的模板文案 |

> 节点状态用现有 `_node_status` 标记：LLM 成功 `success`，降级 `degraded`，这套机制已存在，直接复用。**即使一行 LLM 都没成功，系统行为和今天完全一致。**

---

## 七、Token 统计与可观测

- `client.py` 返回的 usage 累加到 `state.token_usage.planner_prompt_tokens / planner_completion_tokens`（字段已存在）。
- 节点把本次消耗塞进 `_tokens` 返回，`monitored_node` 已有钩子会写进 `NodeTrace.tokens`，并通过 `tag_run` 上报 LangSmith。
- `/trip/stats` 接口自动就能统计到真实 token（目前恒 0）。

---

## 八、编排层重构（顺带还技术债）

把 `graph.py` 三套拓扑收敛成**一套节点序列定义 + 一个执行器**：

- 节点顺序、并行组、回环条件抽成单一数据结构（一份 `PHASES` 定义）。
- 执行器支持两种模式：`collect`（返回最终 Itinerary）和 `stream`（每节点 `yield` 事件）——消除 `_run_local_graph` 与 `stream_trip_graph_events` 的重复。
- `build_trip_graph()`（真 LangGraph）与本地执行器共享同一份节点函数，只是连边方式不同。
- 同步删除 `trip_service.py` 里重复的 `_prorate_amounts / _estimate_ticket_cost / _stable_bucket / _clean_user_tips`，统一 import `rules.py`。

---

## 九、对外契约影响

- `TripRequest` / `Itinerary` schema：**不变**。
- `/trip/generate`、`/trip/generate/stream`、`/trip/edit`：**接口签名不变**。
- SSE 事件：可**新增**几种 LLM 节点事件（coordinator/critic/narrator），前端不处理也不影响——向后兼容。
- 餐饮评分、招牌菜、口碑摘要若不改 schema，只能合并写入 `MealItem.notes`；如果前端要独立展示这些字段，应另开一期显式扩展 `MealItem`。
- `edit_trip_itinerary`：目前 `generate_day_edit_draft` 是空占位，本次可顺势接入 LLM（单日改写），建议作为**第二期**，先聚焦生成主链路。

---

## 十、分阶段实施计划

| 阶段 | 内容 | 产出 | 风险 |
|------|------|------|------|
| **P0** | 搭 `app/llm` 接入层 + 总开关 + 降级骨架 | 能调通 LLM、无 key 自动降级 | 低 |
| **P1** | Narrator（纯文案，最安全，不影响结构） | summary/tips 质量肉眼可见提升 | 低 |
| **P2** | Coordinator + SpotCurator + MealCurator（先补高德 `types/citylimit/biz_ext`，再接博查检索） | 非大理/厦门城市行程质量提升 | 中（需调 prompt 和 POI 类型） |
| **P3** | Critic 评审回环 + Scheduler 消费 `revise_hints` + 编排层收敛三套拓扑 | 行程合理性自检 + 还技术债 | 中 |
| **P4** | LLM 接入 `edit` 单日改写 | 编辑功能真正智能 | 中 |

每阶段都可独立上线、独立回滚（靠 `USE_LLM_AGENTS` 和各节点降级）。

---

## 十一、主要风险与对策

1. **LLM 编造景点/价格** → 强约束 prompt + 输出后校验 `name` 必须来自候选池 + 价格坐标一律走规则/高德。
2. **LLM 编造菜品/评论** → `signature_dishes`/`review_digest` 只能来自博查检索片段，无片段则留空。
3. **JSON 解析失败** → 结构化输出 + Pydantic 校验 + 失败即降级，不影响出图。
4. **高德宽关键词噪声大** → `search_places` 必传 `citylimit=true`，景点/餐饮分别传 `types`，并保留现有黑白名单过滤。
5. **高德评分/人均缺失** → `rating/avg_cost` 统一按可空处理；无评分时不降级，只降低排序权重。
6. **缓存污染** → 地图缓存 key 纳入 `types/citylimit/extensions/page_size`，避免同一关键词不同场景复用错误结果。
7. **MealCurator 不知道当天位置** → MealCurator 只产城市级候选池；按日就近选择由 Scheduler 或后置精排节点完成。
8. **Critic 回环空转** → `revise_hints` 必须写入 `state` 并被 Scheduler 消费；否则只允许 Critic 做只读评审，不进入 revise 回环。
9. **延迟/成本上升** → 并行调用 + 缓存（候选池、博查检索均走缓存）+ 总开关灰度 + 可只开 Narrator。
10. **回环死循环** → 沿用 `TRIP_MAX_REPLAN` 上限，达上限强制 accept。
11. **prompt 漂移导致不稳定** → prompt 集中在 `registry.py`，可版本化、可做 A/B。

---

## 十二、新增配置项汇总

```bash
# LLM 多 Agent 总开关
USE_LLM_AGENTS=false

# 博查 Web Search（餐饮评论/菜品增强）
BOCHA_ENABLED=false
BOCHA_API_KEY=
BOCHA_BASE_URL=https://api.bochaai.com/v1
BOCHA_TIMEOUT_SECONDS=15
```

> 现有 `LLM_*`、`AMAP_*`、`REDIS_*` 配置全部复用，无需改动。

---

## 附录：数据来源说明

- 博查 Web Search API：`POST https://api.bochaai.com/v1/web-search`，详见 [博查 AI 开放平台](https://open.bochaai.com/)。
- 高德地图 POI / 天气 / 路线：复用现有 `map_service.py` / `weather_service.py`。
