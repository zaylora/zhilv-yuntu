from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.client import chat_completion


class LLMUnavailable(RuntimeError):
    """Raised when an LLM call cannot provide trusted structured output."""


T = TypeVar("T", bound=BaseModel)


def call_structured_llm(
    messages: list[dict[str, str]],
    model: type[T],
) -> tuple[T, dict[str, int]]:
    """Call the LLM in JSON mode and validate the response with Pydantic."""
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
