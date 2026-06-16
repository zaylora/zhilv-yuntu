from pathlib import Path
import sys

import pytest


CURRENT_FILE = Path(__file__).resolve()
BACKEND_DIR = CURRENT_FILE.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.llm.structured import LLMUnavailable, call_structured_llm  # noqa: E402
from app.models.schemas import NarratorResponse  # noqa: E402


def test_structured_llm_raises_unavailable_without_key(monkeypatch) -> None:
    monkeypatch.setattr("app.llm.client.LLM_API_KEY", "", raising=False)

    with pytest.raises(LLMUnavailable, match="LLM is unavailable"):
        call_structured_llm(
            [{"role": "user", "content": "{}"}],
            NarratorResponse,
        )
