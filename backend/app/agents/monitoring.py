from __future__ import annotations

import functools
import logging
from time import perf_counter
from typing import Callable

from app.agents.state import NodeTrace, TripState


logger = logging.getLogger(__name__)


try:
    from langsmith import traceable
except ImportError:  # pragma: no cover - optional observability dependency

    def traceable(*_args, **_kwargs):
        def decorator(func):
            return func

        return decorator


try:
    from langsmith.run_helpers import get_current_run_tree
except ImportError:  # pragma: no cover

    def get_current_run_tree():
        return None


def tag_run(**fields) -> None:
    """Attach metadata to the current LangSmith run when tracing is available."""
    try:
        run = get_current_run_tree()
        if run is None:
            return
        if hasattr(run, "add_metadata"):
            run.add_metadata(fields)
        else:
            run.extra.setdefault("metadata", {}).update(fields)
    except Exception:
        logger.debug("failed to tag LangSmith run", exc_info=True)


NodeFunction = Callable[[TripState], dict]


def monitored_node(node_name: str) -> Callable[[NodeFunction], NodeFunction]:
    """Wrap a graph node with timing, trace, error capture, and LangSmith tags."""

    def decorator(fn: NodeFunction) -> NodeFunction:
        @traceable(run_type="chain", name=f"trip.{node_name}")
        @functools.wraps(fn)
        def wrapper(state: TripState) -> dict:
            started_at = perf_counter()
            try:
                patch = fn(state)
                status = str(patch.pop("_node_status", "success"))
            except Exception as exc:
                logger.exception("trip graph node failed: %s", node_name)
                status = "failed"
                patch = {
                    "errors": [f"{node_name}: {type(exc).__name__}: {exc}"],
                    "_note": str(exc),
                }

            elapsed_ms = round((perf_counter() - started_at) * 1000)
            tokens = patch.pop("_tokens", None)
            note = patch.pop("_note", None)
            trace = NodeTrace(
                node=node_name,
                status=status,
                elapsed_ms=elapsed_ms,
                tokens=tokens,
                note=note,
            )
            tag_run(outcome=status, node=node_name, elapsed_ms=elapsed_ms, **(tokens or {}))
            return {**patch, "trace": [trace]}

        return wrapper

    return decorator
