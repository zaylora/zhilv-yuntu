# LLM 接入层与 Narrator 文案 Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为行程生成链路接入安全的 LLM 文案 Agent，并在不可用时完整回退到当前规则输出，同时记录真实 token。

**Architecture:** 保持现有 LangGraph/规则图不变，只在最终 `summarize` 路径增加 Narrator 包裹层。新增 `app/llm` 统一处理 OpenAI-compatible 调用、JSON 解析和 Pydantic 校验，`summarize_node` 先产出规则版 itinerary，再在开关打开且 LLM 可用时覆盖文案字段。失败路径全部回退，不影响 `/trip/generate`、`/trip/generate/stream`、保存与统计接口。

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, httpx, pytest, existing LangGraph/rule nodes.

---

### Task 1: Add the LLM access layer

**Files:**
- Create: `backend/app/llm/__init__.py`
- Create: `backend/app/llm/client.py`
- Create: `backend/app/llm/structured.py`
- Create: `backend/app/llm/registry.py`
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_llm_structured.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
import sys


CURRENT_FILE = Path(__file__).resolve()
BACKEND_DIR = CURRENT_FILE.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest  # noqa: E402

from app.llm.structured import LLMUnavailable, call_structured_llm  # noqa: E402
from app.models.schemas import NarratorResponse  # noqa: E402


def test_structured_llm_raises_unavailable_without_key(monkeypatch) -> None:
    monkeypatch.setattr("app.llm.client.LLM_API_KEY", "", raising=False)

    with pytest.raises(LLMUnavailable, match="LLM is unavailable"):
        call_structured_llm(
            [{"role": "user", "content": "{}"}],
            NarratorResponse,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_llm_structured.py -v`
Expected: fail with `ModuleNotFoundError: No module named 'app.llm'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/llm/client.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT_SECONDS


@dataclass(slots=True)
class LLMResult:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


def llm_is_available() -> bool:
    return bool(LLM_API_KEY)


def chat_completion(messages: list[dict[str, str]], *, response_format: dict[str, str] | None = None) -> LLMResult | None:
    if not llm_is_available():
        return None

    payload: dict[str, Any] = {
        "model": LLM_MODEL,
        "messages": messages,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions" if LLM_BASE_URL else "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"}
    with httpx.Client(timeout=LLM_TIMEOUT_SECONDS) as client:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    choice = data["choices"][0]["message"]["content"]
    usage = data.get("usage") or {}
    return LLMResult(
        text=choice,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
    )
```

```python
# backend/app/llm/__init__.py
"""Shared LLM helpers for trip planning agents."""
```

```python
# backend/app/llm/structured.py
from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.client import chat_completion


class LLMUnavailable(RuntimeError):
    pass


T = TypeVar("T", bound=BaseModel)


def call_structured_llm(messages: list[dict[str, str]], model: type[T]) -> tuple[T, dict[str, int]]:
    result = chat_completion(messages, response_format={"type": "json_object"})
    if result is None:
        raise LLMUnavailable("LLM is unavailable")

    try:
        data = json.loads(result.text)
        parsed = model.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise LLMUnavailable("LLM output failed validation") from exc

    return parsed, {
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
    }
```

```python
# backend/app/models/schemas.py
class NarratorResponse(BaseModel):
    summary: str = Field(...)
    tips: list[str] = Field(default_factory=list)
    day_titles: dict[str, str] = Field(default_factory=dict)
    day_notes: dict[str, list[str]] = Field(default_factory=dict)
```

```python
# backend/app/llm/registry.py
from __future__ import annotations


NARRATOR_SYSTEM_PROMPT = """你是旅行行程文案撰写者。系统会给你一份排好的逐日行程。请为它撰写面向游客的友好文案。

【你要写】
1. summary: 60-120 字，概括这趟旅行的亮点与节奏。
2. tips: 3-5 条实用、具体的出行建议。
3. 每天的标题(day_titles)与 1-2 条当天提示(day_notes)。

【硬性规则】
- 只基于给定行程内容写，不要虚构未出现的景点或餐厅。
- 不要提及“LLM、模型、规则、源码”等技术字眼。
- 语气亲切、简洁，面向普通游客。
- 只输出 JSON。
"""
```

```python
# backend/app/config.py
USE_LLM_AGENTS = os.getenv("USE_LLM_AGENTS", "false").lower() == "true"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_llm_structured.py -v`
Expected: PASS. The test must not perform any HTTP request because `chat_completion` returns `None` before constructing a client when the key is empty.

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/app/llm backend/app/models/schemas.py backend/tests/test_llm_structured.py
git commit -m "feat: add llm access layer"
```

### Task 2: Make summarize_node use Narrator when enabled

**Files:**
- Modify: `backend/app/agents/nodes/summarize.py`
- Modify: `backend/app/models/schemas.py`
- Modify: `backend/app/agents/monitoring.py` if token note formatting needs a small helper
- Test: `backend/tests/test_agents_nodes.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import date
from pathlib import Path
import sys


CURRENT_FILE = Path(__file__).resolve()
BACKEND_DIR = CURRENT_FILE.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.nodes.summarize import summarize_node
from app.models.schemas import DayPlan, NarratorResponse, TokenUsage, TripRequest


def build_trip_request() -> TripRequest:
    return TripRequest(
        destination="大理",
        start_date="2026-04-10",
        end_date="2026-04-12",
        travelers=2,
        budget=3200,
        preferences=["自然风景", "拍照"],
        pace="轻松",
    )


def test_summarize_node_keeps_template_output_when_llm_disabled(monkeypatch) -> None:
    monkeypatch.setattr("app.agents.nodes.summarize.USE_LLM_AGENTS", False)
    state = {
        "request": build_trip_request(),
        "day_plans": [
            DayPlan(day_index=1, date=date(2026, 4, 10), theme="规则主题", notes=["规则提示"])
        ],
        "budget_report": None,
        "token_usage": TokenUsage(),
        "trace": [],
        "errors": [],
    }
    patch = summarize_node(state)
    assert patch["itinerary"].summary
    assert patch["itinerary"].days[0].theme == "规则主题"
    assert patch["itinerary"].token_usage.total_tokens == 0


def test_apply_narrator_result_filters_tips_and_appends_day_notes() -> None:
    itinerary = summarize_node(
        {
            "request": build_trip_request(),
            "day_plans": [
                DayPlan(day_index=1, date=date(2026, 4, 10), theme="规则主题", notes=["规则提示"])
            ],
            "budget_report": None,
            "token_usage": TokenUsage(),
            "trace": [],
            "errors": [],
        }
    )["itinerary"]

    _apply_narrator_result(
        itinerary,
        NarratorResponse(
            summary="这是一段游客可读的行程总结。",
            tips=["建议穿舒适鞋", "不要暴露 LLM 实现"],
            day_titles={"1": "慢游古城"},
            day_notes={"1": ["下午适合放慢节奏"]},
        ),
    )

    assert itinerary.summary == "这是一段游客可读的行程总结。"
    assert itinerary.tips == ["建议穿舒适鞋"]
    assert itinerary.days[0].theme == "慢游古城"
    assert itinerary.days[0].notes == ["规则提示", "下午适合放慢节奏"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_agents_nodes.py -k summarize -v`
Expected: fail with `ImportError` for `_apply_narrator_result`, because the helper does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/agents/nodes/summarize.py
import json

from app.config import USE_LLM_AGENTS
from app.llm.registry import NARRATOR_SYSTEM_PROMPT
from app.llm.structured import LLMUnavailable, call_structured_llm
from app.models.schemas import Itinerary, NarratorResponse


def _build_narrator_messages(state: TripState) -> list[dict[str, str]]:
    request = state["request"]
    days = state.get("day_plans", [])
    payload = {
        "destination": request.destination,
        "pace": request.pace,
        "preferences": request.preferences,
        "special_notes": request.special_notes,
        "days": [
            {
                "day_index": day.day_index,
                "theme": day.theme,
                "spots": [spot.name for spot in day.spots],
                "meals": [meal.name for meal in day.meals],
                "notes": day.notes,
            }
            for day in days
        ],
    }
    return [
        {"role": "system", "content": NARRATOR_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _apply_narrator_result(itinerary: Itinerary, result: NarratorResponse) -> None:
    itinerary.summary = result.summary
    itinerary.tips = clean_user_tips(result.tips, itinerary.destination)
    for day in itinerary.days:
        key = str(day.day_index)
        if key in result.day_titles:
            day.theme = result.day_titles[key]
        if key in result.day_notes:
            day.notes.extend(result.day_notes[key])


@monitored_node("summarize")
def summarize_node(state: TripState) -> dict:
    itinerary = _build_template_itinerary(state)
    if not USE_LLM_AGENTS:
        return {"itinerary": itinerary, "_note": f"trace={len(state.get('trace', []))}"}

    try:
        narrator, tokens = call_structured_llm(_build_narrator_messages(state), NarratorResponse)
        _apply_narrator_result(itinerary, narrator)
        itinerary.token_usage.planner_prompt_tokens += tokens["prompt_tokens"]
        itinerary.token_usage.planner_completion_tokens += tokens["completion_tokens"]
        return {"itinerary": itinerary, "_tokens": tokens, "_note": "narrator=success"}
    except LLMUnavailable as exc:
        return {"itinerary": itinerary, "_node_status": "degraded", "_note": str(exc)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_agents_nodes.py -k summarize -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/nodes/summarize.py backend/tests/test_agents_nodes.py
git commit -m "feat: wire narrator into summarize node"
```

### Task 3: Verify storage and stats remain accurate

**Files:**
- Modify: `backend/tests/test_storage_service.py`
- Modify: `backend/tests/test_api_trip.py`
- Test: `backend/tests/test_storage_service.py`, `backend/tests/test_api_trip.py`

- [ ] **Step 1: Write the regression tests**

```python
from app.services.storage_service import get_token_stats


def test_token_stats_include_planner_usage_from_saved_itinerary() -> None:
    itinerary = generate_trip_itinerary(build_trip_request())
    itinerary.token_usage.planner_prompt_tokens = 11
    itinerary.token_usage.planner_completion_tokens = 7
    itinerary.trip_id = f"{itinerary.trip_id}_{uuid.uuid4().hex[:8]}"
    save_itinerary(itinerary)
    stats = get_token_stats()
    assert stats.total_prompt_tokens >= 11
    assert stats.total_completion_tokens >= 7
```

- [ ] **Step 2: Run the regression tests**

Run: `pytest backend/tests/test_storage_service.py -k token_stats -v`
Expected: PASS if existing save/load already preserves planner token fields. If it fails, fix only the serialization path that drops `token_usage`.

- [ ] **Step 3: Add API stats smoke coverage**

```python
def test_trip_stats_returns_saved_planner_usage() -> None:
    generated_response = client.post("/trip/generate", json=build_generate_payload())
    generated_itinerary = generated_response.json()
    generated_itinerary["trip_id"] = f"{generated_itinerary['trip_id']}_stats"
    generated_itinerary["token_usage"]["planner_prompt_tokens"] = 13
    generated_itinerary["token_usage"]["planner_completion_tokens"] = 5

    client.post(
        "/trip/save",
        json={
            "trip_id": generated_itinerary["trip_id"],
            "itinerary": generated_itinerary,
            "user_id": "user_001",
        },
    )

    response = client.get("/trip/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_prompt_tokens"] >= 13
    assert data["total_completion_tokens"] >= 5
```

- [ ] **Step 4: Run stats tests**

Run: `pytest backend/tests/test_storage_service.py -k token_stats -v && pytest backend/tests/test_api_trip.py -k stats -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_storage_service.py backend/tests/test_api_trip.py
git commit -m "test: preserve planner token stats"
```

### Task 4: Add real-LLM integration script and API smoke coverage

**Files:**
- Create: `backend/scripts/test_narrator_real.py`
- Test: `backend/scripts/test_narrator_real.py`

- [ ] **Step 1: Run missing script to verify it fails**

Run: `python backend/scripts/test_narrator_real.py --destination 大理`
Expected: fail because the file does not exist.

- [ ] **Step 2: Write minimal implementation**

```python
# backend/scripts/test_narrator_real.py
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

CURRENT_FILE = Path(__file__).resolve()
BACKEND_DIR = CURRENT_FILE.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import USE_LLM_AGENTS, LLM_API_KEY
from app.models.schemas import TripRequest
from app.services.trip_service import generate_trip_itinerary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the trip narrator with a real LLM key.")
    parser.add_argument("--destination", default="大理")
    args = parser.parse_args()

    if not USE_LLM_AGENTS or not LLM_API_KEY:
        raise SystemExit("Real LLM config is required: set USE_LLM_AGENTS=true and LLM_API_KEY.")

    request = TripRequest(
        destination=args.destination,
        start_date="2026-04-10",
        end_date="2026-04-12",
        travelers=2,
        budget=3200,
        preferences=["自然风景", "拍照", "美食"],
        pace="轻松",
        dietary_preferences=["少辣"],
        hotel_level="舒适型",
        special_notes="不想太早起床，希望安排一个适合看日落的地点",
    )
    itinerary = generate_trip_itinerary(request)
    print(json.dumps(itinerary.model_dump(mode="json"), ensure_ascii=False, indent=2))
    usage = itinerary.token_usage
    if usage is None or usage.planner_prompt_tokens + usage.planner_completion_tokens <= 0:
        raise SystemExit("Narrator did not record planner tokens.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Run script without config**

Run: `python backend/scripts/test_narrator_real.py --destination 大理`
Expected: exits non-zero with `Real LLM config is required...`. This confirms the script will not pretend success without a real key.

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/test_narrator_real.py
git commit -m "feat: add narrator real integration script"
```

## Verification

After the tasks above, run:

```bash
pytest backend/tests/test_llm_structured.py backend/tests/test_agents_nodes.py backend/tests/test_storage_service.py backend/tests/test_api_trip.py -v
```

Expected:

- Default path still returns complete itinerary.
- No-key path degrades cleanly.
- Planner tokens are persisted and aggregated.
- No test depends on a fake model response.

Run the real script only when real LLM credentials are set:

```bash
USE_LLM_AGENTS=true python backend/scripts/test_narrator_real.py --destination 大理
```

Expected:

- summary/tips/day fields are updated by the narrator.
- planner token counts are greater than zero.
