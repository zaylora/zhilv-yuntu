from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT_SECONDS


@dataclass(slots=True)
class LLMResult:
    """Text and usage returned by a chat completion call."""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


def llm_is_available() -> bool:
    """Return whether the current process has enough config to call an LLM."""
    return bool(LLM_API_KEY)


def chat_completion(
    messages: list[dict[str, str]],
    *,
    response_format: dict[str, str] | None = None,
) -> LLMResult | None:
    """Call an OpenAI-compatible chat completion endpoint."""
    if not llm_is_available():
        return None

    payload: dict[str, Any] = {
        "model": LLM_MODEL,
        "messages": messages,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    url = (
        f"{LLM_BASE_URL.rstrip('/')}/chat/completions"
        if LLM_BASE_URL
        else "https://api.openai.com/v1/chat/completions"
    )
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"}
    with httpx.Client(timeout=LLM_TIMEOUT_SECONDS) as client:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    text = str(data["choices"][0]["message"]["content"])
    usage = data.get("usage") or {}
    return LLMResult(
        text=text,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
    )
