# P2+P3 LLM Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 P0+P1 基础上落地 Coordinator、SpotCurator、MealCurator、Critic 和 Scheduler 回环，并收敛 graph / trip_service 重复实现。

**Architecture:** 保持 `TripRequest -> Itinerary` 对外契约不变，所有新能力都通过现有节点内部的 LLM 优先 + 规则降级实现。Coordinator 负责生成规划策略和扩展关键词，SpotCurator 和 MealCurator 只从真实候选池中做结构化选择，Critic 只给出 `accept/revise` 和可执行 hint，Scheduler 消费 hint 后重排。`graph.py` 统一本地执行和流式事件的阶段定义，`trip_service.py` 只保留入口和 legacy fallback。

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, httpx, pytest, existing `app/llm`, existing graph/nodes/services.

---

### Task 1: Extend shared models and config for P2/P3

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/models/schemas.py`
- Modify: `backend/app/agents/state.py`
- Test: `backend/tests/test_models_schemas.py`

- [ ] **Step 1: Write the failing test**

```python
from app.models.schemas import MealItem, TokenUsage
from app.agents.state import MealCandidate, TripState


def test_meal_item_keeps_existing_fields_only():
    meal = MealItem(name="示例餐厅", meal_type="午餐")
    assert meal.name == "示例餐厅"
    assert not hasattr(meal, "rating")


def test_state_can_store_planning_strategy_and_revise_hints():
    state: TripState = {
        "replan_count": 0,
        "max_replan": 2,
        "token_usage": TokenUsage(),
        "errors": [],
        "trace": [],
    }
    state["revise_hints"] = ["减少景点数量"]
    assert state["revise_hints"] == ["减少景点数量"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_models_schemas.py -v`
Expected: FAIL because `TripState` lacks the new optional fields and `MealItem`/candidate shape does not yet reflect the planned data flow.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/config.py
BOCHA_ENABLED = os.getenv("BOCHA_ENABLED", "false").lower() == "true"
BOCHA_API_KEY = os.getenv("BOCHA_API_KEY", "")
BOCHA_BASE_URL = os.getenv("BOCHA_BASE_URL", "https://api.bochaai.com/v1")
BOCHA_TIMEOUT_SECONDS = int(os.getenv("BOCHA_TIMEOUT_SECONDS", "15"))

# backend/app/models/schemas.py
class MealItem(BaseModel):
    name: str = Field(..., description="餐厅或餐饮建议名称")
    meal_type: str = Field(..., description="早餐、午餐、晚餐等")
    estimated_cost: float = Field(default=0.0, ge=0, description="预估花费")
    notes: str | None = Field(default=None, description="补充说明")

class PlanningStrategy(BaseModel):
    strategy: str = Field(default="")
    daily_themes: list[str] = Field(default_factory=list)
    pace_normalized: str | None = None
    spot_keywords: list[str] = Field(default_factory=list)
    meal_keywords: list[str] = Field(default_factory=list)
    budget_hint: dict[str, float] = Field(default_factory=dict)
    hard_constraints: list[str] = Field(default_factory=list)

class CoordinatorResponse(BaseModel):
    ...

class SpotCuratorResponse(BaseModel):
    ...

class MealCuratorResponse(BaseModel):
    ...

class CriticResponse(BaseModel):
    ...

# backend/app/agents/state.py
class MealCandidate(BaseModel):
    ...
    rating: float | None = None
    avg_cost: float | None = None
    notes: str | None = None

class TripState(TypedDict, total=False):
    ...
    planning_strategy: PlanningStrategy
    revise_hints: list[str]
    critic_report: CriticResponse
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_models_schemas.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/app/models/schemas.py backend/app/agents/state.py backend/tests/test_models_schemas.py
git commit -m "feat: extend shared models for p2 p3 agents"
```

### Task 2: Add Bocha web search and Amap POI calibration

**Files:**
- Create: `backend/app/services/web_search_service.py`
- Modify: `backend/app/services/map_service.py`
- Test: `backend/tests/test_services_map.py`
- Test: `backend/tests/test_services_web_search.py`

- [ ] **Step 1: Write the failing test**

```python
def test_search_places_includes_types_citylimit_and_rating_cost(monkeypatch):
    ...


def test_web_search_returns_empty_when_disabled(monkeypatch):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_services_map.py backend/tests/test_services_web_search.py -v`
Expected: FAIL because `search_places` still lacks `types/citylimit/biz_ext`, and web search service does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
def search_places(keyword: str, city: str | None = None, page_size: int = 5, types: str | None = None, citylimit: bool = True) -> list[dict[str, Any]]:
    ...
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
    ...
    biz_ext = poi.get("biz_ext") or {}
    "rating": _parse_float(biz_ext.get("rating")),
    "avg_cost": _parse_float(biz_ext.get("cost")),
```

```python
# backend/app/services/web_search_service.py
def search_web(query: str, count: int = 5) -> list[dict[str, str]]:
    if not BOCHA_ENABLED or not BOCHA_API_KEY:
        return []
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_services_map.py backend/tests/test_services_web_search.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/web_search_service.py backend/app/services/map_service.py backend/tests/test_services_map.py backend/tests/test_services_web_search.py
git commit -m "feat: add bocha search and amap poi calibration"
```

### Task 3: Implement Coordinator and thread its output into dispatch

**Files:**
- Modify: `backend/app/agents/nodes/dispatch.py`
- Modify: `backend/app/llm/registry.py`
- Test: `backend/tests/test_agents_nodes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_dispatch_node_uses_llm_strategy_when_enabled(monkeypatch):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_agents_nodes.py -k dispatch -v`
Expected: FAIL because `dispatch_node` still only builds `NormalizedDemand`.

- [ ] **Step 3: Write minimal implementation**

```python
@monitored_node("dispatch")
def dispatch_node(state: TripState) -> dict:
    ...
    normalized = NormalizedDemand(...)
    planning_strategy = None
    if USE_LLM_AGENTS:
        try:
            coordinator, tokens = call_structured_llm(build_coordinator_messages(...), CoordinatorResponse)
            planning_strategy = PlanningStrategy(...)
            normalized.spot_keywords = ...
            normalized.meal_keywords = ...
        except LLMUnavailable:
            pass
    return {"normalized": normalized, "planning_strategy": planning_strategy, ...}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_agents_nodes.py -k dispatch -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/nodes/dispatch.py backend/app/llm/registry.py backend/tests/test_agents_nodes.py
git commit -m "feat: add coordinator strategy to dispatch"
```

### Task 4: Implement SpotCurator and MealCurator

**Files:**
- Modify: `backend/app/agents/nodes/spot_search.py`
- Modify: `backend/app/agents/nodes/meal_search.py`
- Modify: `backend/app/llm/registry.py`
- Test: `backend/tests/test_agents_nodes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_spot_curator_rejects_names_outside_candidate_pool(monkeypatch):
    ...


def test_meal_curator_uses_bocha_notes_when_available(monkeypatch):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_agents_nodes.py -k "spot or meal" -v`
Expected: FAIL because nodes still rely on the old rule-only path.

- [ ] **Step 3: Write minimal implementation**

```python
def _search_amap_spots(...):
    places = search_places(query, city=destination, page_size=5, types="110000", citylimit=True)

def _search_amap_meals(...):
    places = search_places(query, city=destination, page_size=5, types="050000", citylimit=True)

@monitored_node("spots")
def spot_search_node(...):
    ...
    if USE_LLM_AGENTS and candidates:
        curator, tokens = call_structured_llm(..., SpotCuratorResponse)
        selected_names = {item.name for item in curator.selected if item.name in seen_names}
        ...

@monitored_node("meals")
def meal_search_node(...):
    ...
    web_hits = search_web(...)
    if USE_LLM_AGENTS and candidates:
        curator, tokens = call_structured_llm(..., MealCuratorResponse)
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_agents_nodes.py -k "spot or meal" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/nodes/spot_search.py backend/app/agents/nodes/meal_search.py backend/app/llm/registry.py backend/tests/test_agents_nodes.py
git commit -m "feat: add spot and meal curator agents"
```

### Task 5: Implement Critic and Scheduler hint consumption

**Files:**
- Modify: `backend/app/agents/nodes/schedule.py`
- Modify: `backend/app/agents/nodes/budget_check.py`
- Create: `backend/app/agents/nodes/critic.py`
- Modify: `backend/app/llm/registry.py`
- Test: `backend/tests/test_agents_nodes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_critic_sets_revise_hints_and_schedule_consumes_them(monkeypatch):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_agents_nodes.py -k critic -v`
Expected: FAIL because no critic node exists and schedule does not read hints.

- [ ] **Step 3: Write minimal implementation**

```python
@monitored_node("critic")
def critic_node(state: TripState) -> dict:
    ...
    return {"critic_report": report, "revise_hints": report.revise_hints, "_note": ...}

def schedule_node(state: TripState) -> dict:
    hints = state.get("revise_hints", [])
    if any("减少景点" in hint or "景点过多" in hint for hint in hints):
        max_count = 1
    if any("室内" in hint or "雨天" in hint for hint in hints):
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_agents_nodes.py -k critic -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/nodes/critic.py backend/app/agents/nodes/schedule.py backend/app/agents/nodes/budget_check.py backend/app/llm/registry.py backend/tests/test_agents_nodes.py
git commit -m "feat: add critic feedback loop"
```

### Task 6: Reconcile graph execution and streaming with one phase definition

**Files:**
- Modify: `backend/app/agents/graph.py`
- Test: `backend/tests/test_agents_graph.py`

- [ ] **Step 1: Write the failing test**

```python
def test_stream_and_run_share_same_node_order(monkeypatch):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_agents_graph.py -v`
Expected: FAIL because local runner and stream runner still duplicate topology logic.

- [ ] **Step 3: Write minimal implementation**

```python
PHASES = {
    "initial": (...),
    "replan": (...),
    "final": (...),
}

def _run_phases(...):
    ...

def stream_trip_graph_events(...):
    ...

def build_trip_graph():
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_agents_graph.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/graph.py backend/tests/test_agents_graph.py
git commit -m "refactor: unify trip graph phases"
```

### Task 7: Remove duplicated rule helpers from trip_service

**Files:**
- Modify: `backend/app/services/trip_service.py`
- Test: `backend/tests/test_services_trip.py`

- [ ] **Step 1: Write the failing test**

```python
def test_trip_service_uses_shared_rules_helpers(monkeypatch):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_services_trip.py -v`
Expected: FAIL because `trip_service.py` still defines its own helper copies.

- [ ] **Step 3: Write minimal implementation**

```python
from app.agents.nodes.rules import clean_user_tips, estimate_ticket_cost, prorate_amounts, stable_bucket
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_services_trip.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/trip_service.py backend/tests/test_services_trip.py
git commit -m "refactor: reuse shared trip rules in service"
```

### Task 8: End-to-end verification

**Files:**
- All modified backend files
- Test: `backend/tests/test_api_trip.py`
- Test: `backend/tests/test_no_rag_runtime.py`

- [ ] **Step 1: Run backend test subset**

Run: `pytest backend/tests/test_models_schemas.py backend/tests/test_services_map.py backend/tests/test_services_web_search.py backend/tests/test_agents_nodes.py backend/tests/test_agents_graph.py backend/tests/test_services_trip.py backend/tests/test_api_trip.py -v`
Expected: PASS.

- [ ] **Step 2: Run lightweight compilation check**

Run: `python -m compileall -q backend/app backend/tests`
Expected: exit code 0.

- [ ] **Step 3: Smoke generate trip**

Run: `python backend/scripts/test_trip_graph_real.py`
Expected: graph still generates itineraries and returns non-empty output.

- [ ] **Step 4: Commit**

```bash
git add backend/app backend/tests docs/superpowers/specs/2026-06-17-llm-agents-p2-p3-design.md docs/superpowers/plans/2026-06-17-llm-agents-p2-p3.md
git commit -m "feat: ship p2 p3 llm agents"
```
