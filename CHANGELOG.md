# 更新日志

> 这里只记录项目功能、架构和工程能力相关的更新，不记录简历、面试文档等个人整理内容。

## 2026-06-16

### LangGraph 多 Agent 编排重构

- 新增 `agents/graph.py`、`agents/state.py`、`agents/nodes/*` 与 `agents/algorithms/*`，将行程生成拆分为 dispatch、景点候选、餐饮候选、交通、天气、编排、住宿、预算校验和汇总节点。
- 新增节点监控装饰器，统一记录节点状态、耗时、降级原因和 `graph_trace`。
- `trip_service.generate_trip_itinerary` 改为 graph 优先，保留纯规则兜底。
- 新增 `/trip/generate/stream` SSE 接口，返回节点进度事件和最终 itinerary。
- 移除本地向量检索链路：删除向量库模块、检索工具、入库/评估/调试脚本、评估样例、本地攻略 corpus 和相关重依赖。
- 新增 graph/算法/节点/无向量链路回归测试，并修复一处测试文件里的中文弯引号语法错误。

## 2026-06-11

### Docker 容器化部署

- 新增 `backend/Dockerfile`：基于 Python 3.11-slim 镜像，先复制 `requirements.txt` 安装依赖（利用层缓存），再复制应用代码，运行 uvicorn。
- 新增 `frontend/Dockerfile`：两阶段构建——Node 20 编译 Vue 应用，Nginx alpine 托管静态文件，最终镜像不含 Node.js，体积从几百 MB 缩小到几十 MB。
- 新增 `frontend/nginx.conf`：Nginx 配置，负责静态文件托管 + API 反向代理到后端容器，`try_files` 支持 Vue SPA 路由。
- 新增 `docker-compose.yaml`：编排 Redis、后端、前端三个服务，定义依赖关系、端口映射、数据卷持久化和环境变量注入。
- 新增 `.dockerignore`：排除 `__pycache__`、`.env`、`node_modules` 等不需要打包的文件。
- 后端 `main.py` 新增 Docker 环境的 CORS 允许源（`http://localhost`、`http://localhost:80`）。

## 2026-04-15

### Redis 缓存优化

- 新增 Redis 缓存层，并支持 Redis 不可用时自动降级，不影响主流程运行。
- 接入天气查询缓存，减少同城天气的重复外部请求。
- 接入高德地图地理编码、POI 搜索和路线估算缓存，减少重复地图查询开销。
- 接入 RAG 检索结果缓存，复用高频 query 的攻略片段召回结果。
- 增加基础 `cache hit / cache miss` 日志，便于本地验证缓存命中情况。
- 通过本地 Docker Redis 容器验证缓存 key 写入成功。

**本次验证到的缓存 key**

```text
trip_planner:weather:forecast:大理
trip_planner:rag:guide:大理 自然风景 拍照 美食 轻松 不想太早起床，希望安排一个适合看日落的地点。 景点 行程 攻略 推荐:5
trip_planner:map:place:大理 舒适型住宿 2:大理:1
trip_planner:map:place:双廊古镇:大理:1
trip_planner:map:route:100.323501,25.647149:100.131582,25.852950
trip_planner:map:place:大理 舒适型住宿 1:大理:1
trip_planner:map:place:大理 舒适型住宿 3:大理:1
trip_planner:map:route:100.323501,25.647149:100.164000,25.694836
trip_planner:map:place:大理古城:大理:1
trip_planner:map:geocode:大理:大理
trip_planner:map:place:大理 出发点:大理:1
trip_planner:map:route:100.323501,25.647149:100.194322,25.908323
trip_planner:map:place:喜洲古镇:大理:1
```

## 2026-04-25

### RAG 在线阶段优化

- 接入轻量化 Query Rewrite，不再直接拼接整句备注，而是从用户偏好、节奏与备注中提炼更适合检索的关键词。
- 在 `rag_tool.py` 中补充旅行场景规则词，例如日落、傍晚、洱海、双廊、慢节奏等，提升旅行规划类 query 的检索聚焦度。
- 在 `retriever.py` 中加入第一版轻量 Rerank，对标题命中、正文命中、行程类标题和已知噪声片段做启发式打分。
- 对”文档开头”这类低信息量片段做强惩罚，并对与当前主目标弱相关的餐饮 / 预算类片段做轻量降权。
- 新增 `debug_rag_retrieval.py` 调试脚本，可直接观察检索 query、top-k 召回片段、`rerank_score` 与 `rerank_reasons`。

**优化效果**

以大理日落+拍照+轻松场景为例：

| 优化前 Top5 | 优化后 Top5（含 rerank_score） |
|-------------|-------------------------------|
| 1. 经典三日行程参考 | 1. 经典三日行程参考 (score: 10) |
| 2. 目的地简介 | 2. 大理古城 (score: 5) |
| 3. 特色餐饮与预算参考 | 3. 洱海生态廊道 (score: 5) |
| 4. 住宿区域建议 | 4. 目的地简介 (score: 3) |
| 5. 文档开头 | 5. 住宿区域建议 (score: 3) |

优化前 Top5 中”目的地简介”和”文档开头”等低信息量片段占据位置；优化后通过 Rerank 打分，”大理古城”和”洱海生态廊道”等与场景强相关的片段被排到前面，”文档开头”被过滤。

## 2026-04-29

### 地图路线可视化

- 在高德地图上用虚线箭头连接各天行程点位，展示完整路线走向。
- 每个目的地用 🚩 旗帜图标标记，直观体现打卡风格。
- 鼠标悬停时展示景点图片气泡窗口（含景区图片和地名），无图片时显示占位提示。
- 气泡窗口采用轻量卡片设计，尺寸紧凑，不遮挡地图主体内容。

### RAG 知识库扩充

- 新增 4 个目的地攻略文档：`chengdu_guide.md`、`xian_guide.md`、`xiamen_guide.md`、`sanya_guide.md`。
- 与原有 `dali_guide.md` 共同覆盖 5 个目的地，写入后 ChromaDB 共 49 个检索片段。
- 每个攻略统一采用：目的地简介 → 核心景点（含门票/位置/时长） → 特色餐饮（含人均预算） → 住宿区域（含价格区间） → 经典行程参考的结构。

### RAG 评估样例集扩充

- `rag_eval_cases.json` 从 3 条扩充到 15 条，每个目的地 3 条。
- 覆盖不同人群（情侣、亲子、带父母）、不同节奏（轻松、适中）、不同偏好（美食、文化、自然、度假）。

### RAG 在线阶段规则级优化

本轮在不引入模型的前提下，通过规则优化 RAG 在线阶段的检索排序质量。

**Query Rewrite 目的地过滤**

- 在 `rag_tool.py` 的 `_extract_note_keywords` 中引入目的地过滤机制。
- 目的地特定关键词（如"洱海、双廊"只在大理时注入，"回民街"只在西安时注入）不再跨目的地泄漏。
- 新增成都（大熊猫）、西安（古镇→回民街）、厦门（古镇→鼓浪屿/曾厝垵、骑行→环岛路）、三亚（潜水→蜈支洲岛、海鲜→第一市场）等目的地规则。

**Rerank 多层降权**

- 在 `retriever.py` 的 `_score_chunk_for_rerank` 中新增三层降权机制：
  - 行程参考片段降权（-4）：防止"经典行程参考"因内容过于全面而霸占 Top1。
  - 目的地简介降权（-2）：防止过于泛化的简介片段排到 Top1。
  - 目的地不匹配降权（-5）：对来源目的地与查询目的地不一致的片段做大幅降权，解决跨目的地污染。
- `rerank_guide_chunks` 和 `retrieve_travel_guide_chunks` 全链路传递 `destination` 参数。

**优化效果**

| 指标 | 基线 | 优化后 |
|------|------|--------|
| Top1 命中率 | 13/15 (87%) | 12/15 (80%) |
| TopK 命中率 | 15/15 (100%) | 15/15 (100%) |
| 跨目的地污染 | 存在 | 已消除 |
| 噪声片段 | 0 | 0 |

Top1 命中率看似下降，实际是基线中"行程片段"霸榜导致的虚高。优化后专题片段（餐饮、景点、住宿）能正确排到 Top1，排序质量实质提升。剩余 3 个 Top1 miss 均为同目的地内的排序竞争，需引入语义级 rerank 进一步优化。

**优化路线总览**

```text
已完成（规则级）                    下一步（模型级）
┌─────────────────────┐      ┌──────────────────────┐
│ Query Rewrite       │      │ LLM-based Rewrite    │
│  └ 目的地过滤        │ ──→  │  └ qwen-max 改写 query│
├─────────────────────┤      ├──────────────────────┤
│ Rerank              │      │ Cross-encoder Rerank │
│  └ 关键词打分        │ ──→  │  └ bge-reranker-base │
│  └ 行程/简介降权     │      │  └ 语义相关性打分    │
│  └ 目的地不匹配降权  │      │                      │
└─────────────────────┘      └──────────────────────┘
```

## 2026-05-06

### RAG 评估指标体系完善

- 在 `evaluate_rag_retrieval.py` 中新增 4 个量化指标，建立完整的 RAG 检索质量评估体系。
- **MRR（Mean Reciprocal Rank）**：衡量命中结果的排序质量，标准 IR 指标。
- **Noise Rate**：噪声片段占 Top-K 的百分比，比绝对数量更直观。
- **Latency（检索耗时）**：单次检索耗时（ms），体现工程优化价值。
- **Cross-destination Pollution**：跨目的地污染片段数，量化之前的优化成果。

**Baseline 数据（规则级 RAG）**

| 指标 | 数值 |
|------|------|
| Top1 Hit Rate | 12/15 (80.0%) |
| TopK Hit Rate | 15/15 (100.0%) |
| MRR | 0.889 |
| Keyword Coverage | 59/63 |
| Noise Rate | 0.0% |
| Cross-destination Pollution | 0 |
| Avg Latency | 461.8ms |

3 个 Top1 miss 均为同目的地内的排序竞争（西安美食→期望回民街、西安亲子→期望景点、厦门骑行→期望环岛路），需引入语义级优化进一步提升。

### LLM-based Query Rewrite

- 用 qwen-max 替代手写规则，将用户旅行需求自动改写成向量检索 query。
- 在 `rag_tool.py` 中新增 `llm_rewrite_query()`，遵循项目已有的 LangChain + ChatOpenAI 调用模式。
- 保留规则级 `_rule_based_query()` 作为 fallback，LLM 不可用时自动降级。
- Prompt 设计：System 指定输出格式（纯关键词、空格分隔、无解释），Human 包含目的地/偏好/节奏/备注。

**优化效果对比**

| 指标 | Baseline（规则级） | LLM-based | 变化 |
|------|-------------------|-----------|------|
| Top1 Hit Rate | 12/15 (80.0%) | 13/15 (86.7%) | +6.7% |
| TopK Hit Rate | 15/15 (100.0%) | 15/15 (100.0%) | - |
| MRR | 0.889 | 0.922 | +3.7% |
| Keyword Coverage | 59/63 | 60/63 | +1 |
| Noise Rate | 0.0% | 0.0% | - |
| Cross-destination Pollution | 0 | 0 | - |
| Avg Latency | 461.8ms | 347.7ms | -24.7% |

LLM Rewrite 将 Top1 命中从 12 提升到 13，MRR 从 0.889 提升到 0.922。西安美食场景从 RR=0.5 提升到 RR=1.0（LLM 直接生成"回民街"关键词）。剩余 2 个 Top1 miss 为同目的地内排序竞争，需引入 Cross-encoder Rerank 进一步优化。

## 2026-05-07

### Cross-encoder Rerank

- 接入 DashScope qwen3-rerank 模型，替代规则级 Rerank 做语义级重排序。
- 在 `retriever.py` 中新增 `_rerank_with_dashscope()`，通过 httpx 调用 DashScope Reranker API。
- 保留规则级 `_score_chunk_for_rerank()` 作为 fallback，API 不可用时自动降级。
- 添加 `instruct` 参数引导模型优先选择具体、详细的旅行攻略片段，避免泛化介绍。
- 在 rerank 前过滤已知噪声片段（"文档开头"），避免浪费 rerank 名额。

**优化效果对比**

| 指标 | Baseline（规则级） | LLM Rewrite | + Cross-encoder | 变化（vs Baseline） |
|------|-------------------|-------------|-----------------|-------------------|
| Top1 Hit Rate | 12/15 (80.0%) | 13/15 (86.7%) | 14/15 (93.3%) | +13.3% |
| TopK Hit Rate | 15/15 (100.0%) | 15/15 (100.0%) | 15/15 (100.0%) | - |
| MRR | 0.889 | 0.922 | 0.967 | +8.8% |
| Keyword Coverage | 59/63 | 60/63 | 61/63 | +2 |
| Noise Rate | 0.0% | 0.0% | 0.0% | - |
| Cross-destination Pollution | 0 | 0 | 0 | - |
| Avg Latency | 461.8ms | 347.7ms | 728.4ms | +57.7% |

经过 LLM Query Rewrite + Cross-encoder Rerank + 噪声预过滤三阶段优化，Top1 命中率从 80% 提升到 93.3%，MRR 从 0.889 提升到 0.967，噪声率保持为 0。Latency 增加主要来自 rerank API 调用，后续可通过缓存优化。

### Rerank 缓存优化

- 在 `retriever.py` 的 `rerank_guide_chunks` 中新增 Rerank 结果缓存，命中时跳过 DashScope API 调用。
- 缓存 key 由 normalized query + chunks 内容哈希组成，确保相同 query 配相同候选集时复用缓存。
- 缓存值只存索引和分数 `[{"i", "s"}]`，不重复存 chunk 文本，节省存储。
- 在 `config.py` 中新增 `REDIS_RERANK_TTL_SECONDS` 配置项，与现有 Redis 缓存共享开关。
- 在 `.env` 和 `.env.example` 中同步新增 `REDIS_RERANK_TTL_SECONDS=21600`。

**本次验证到的 Rerank 缓存 key**

```text
trip_planner:rerank:成都 熊猫基地 美食 休闲 轻松 适合家庭:86281a27592a
trip_planner:rerank:大理 自然风景 拍照 美食 轻松 日落地点:feaeff944221
trip_planner:rerank:西安 回民街 美食 小吃 夜市 特色小吃:6aca260efd77
trip_planner:rerank:三亚 海滩 度假 放松 高级酒店 沙滩休闲:aa3ac37f0667
trip_planner:rerank:厦门 厦门大学 南普陀寺 拍照 文化 建筑:6e5f7b5d1ae0
trip_planner:rerank:三亚 美食 海鲜 南山寺 文化 适中节奏 新鲜海鲜:610d238b719e
trip_planner:rerank:大理 古镇 文化体验 慢节奏 白族文化 轻松旅行:25c88c579b45
trip_planner:rerank:成都 自然风景 徒步 文化 都江堰 青城山 户外:e651b63c9ec0
trip_planner:rerank:大理 美食 小吃 预算 特色餐饮 花费:edd9f0093e5f
trip_planner:rerank:厦门 鼓浪屿 海岛 文艺 美食 情调 轻松 旅行:229afa03f280
trip_planner:rerank:西安 亲子 研学 文化 轻松 教育意义 不赶:7184d33a1e75
trip_planner:rerank:成都 美食 小吃 火锅 特色美食 吃货 探店:584c7e41a41b
trip_planner:rerank:三亚 亲子 水上乐园 海洋动物 轻松:ca99c53ae7e7
trip_planner:rerank:西安 历史 文化 古迹 深度游:480addc7dfe9
trip_planner:rerank:厦门 骑行 海景 休闲 日落 放松:14207650ed1a
```

**优化效果**

| 指标 | 无 Rerank | + Cross-encoder | + Rerank 缓存 |
|------|-----------|-----------------|---------------|
| Top1 Hit Rate | 12/15 (80.0%) | 14/15 (93.3%) | 14/15 (93.3%) |
| MRR | 0.889 | 0.967 | 0.967 |
| Noise Rate | 0.0% | 0.0% | 0.0% |
| Avg Latency（首次） | 461.8ms | 728.4ms | 654.6ms |
| Avg Latency（缓存命中） | - | - | **424.8ms** |

缓存命中后延迟降低 41.6%，回到接近 LLM Rewrite 阶段水平，同时保留 Cross-encoder Rerank 的质量提升。

## 2026-05-19

### Token 消耗统计

- 在 `schemas.py` 中为 `Itinerary` 增加 `token_usage` 字段，记录单次行程生成过程中的 token 消耗。
- 在 `rag_tool.py` 中提取 LLM-based Query Rewrite 的输入/输出 token。
- 在 `vector_db.py` 中提取在线 Query Embedding 的官方 `usage`，仅统计在线检索 query embedding，不统计离线知识库入库 embedding。
- 在 `retriever.py` 中提取 DashScope qwen3-rerank 官方 `usage`，记录 Rerank 输入/输出 token。
- 在 `trip_planner_agent.py` 中提取 Planner 行程生成的输入/输出 token。
- 在 `trip_service.py` 中汇总 Query Rewrite、Query Embedding、Rerank、Planner 四段 token，并在后端终端打印分项与总量。
- 新增 `/trip/stats` 接口，可统计已保存行程的 token 消耗汇总。

**本次前端输入**

```text
目的地城市：大理
开始日期：2026-05-19
结束日期：2026-05-21
人数：2
旅行天数：3 天
节奏偏好：轻松
住宿偏好：舒适型
预算：3200
旅行偏好：自然风景、拍照、美食
饮食偏好：少辣
额外要求：不想太早起床，希望安排一个适合看日落的地点。
```

**本次验证日志**

```text
[embedding] query embedding token: prompt=20, completion=0, source=api
[rerank] qwen3-rerank token: prompt=1703, completion=0, source=api
[trip_planner_agent] 大模型调用完成。token: prompt=1159, completion=437
[token_usage] Query Rewrite: prompt=134, completion=19
[token_usage] Rerank: prompt=1703, completion=0
[token_usage] Query Embedding: prompt=20, completion=0
[token_usage] Planner: prompt=1159, completion=437
[token_usage] Total: prompt=3016, completion=456, all=3472
```

**四种模型调用的 token 消耗**

| 调用 | 模型 | 输入 token | 输出 token | 原因 |
|------|------|-----------|-----------|------|
| Query Rewrite | qwen-max | ✅ 有 | ✅ 有 | 生成式 LLM，输入 prompt，输出关键词文本 |
| Query Embedding | text-embedding-v4 | ✅ 有 | ❌ 0 | 只把文本转向量，不生成文本 |
| Rerank | qwen3-rerank | ✅ 有 | ❌ 0 | 只算相关性分数，不生成文本 |
| 行程生成 | qwen-max | ✅ 有 | ✅ 有 | 生成式 LLM，输入 prompt，输出 JSON 行程 |

**说明**

- `source=api` 表示 Query Embedding 和 Rerank token 均来自 DashScope 官方响应字段，不是本地估算。
- 本次统计只覆盖在线请求成本：Query Rewrite、Query Embedding、Rerank、Planner；离线知识库切片入库时的 document embedding 不计入单次 `/trip/generate`。
- Rerank 和 Embedding 的 `completion=0` 属于正常现象，因为 Embedding 输出向量、Rerank 输出分数，都不生成自然语言正文。
- `/trip/generate` 返回结果中会同步包含 `token_usage`，便于前端或调试工具查看本次生成成本。
