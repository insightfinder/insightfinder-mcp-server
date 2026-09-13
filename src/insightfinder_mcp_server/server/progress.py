"""Progress reporting for long-running tools.

A tool calls `report_progress(...)` at meaningful points (a page fetched, a stage finished).
When the tool runs under `/tools/{name}/stream`, the endpoint has installed an asyncio.Queue in
the `_PROGRESS_SINK` context variable before starting the tool task, so reports are forwarded to
the client as `progress` SSE events while the tool is still running. Without a sink (stdio
transport, tests) reports are dropped silently.
"""
from __future__ import annotations

import asyncio
import contextvars
import time
from typing import Any, Dict, Optional

_PROGRESS_SINK: contextvars.ContextVar[Optional[asyncio.Queue]] = contextvars.ContextVar(
    "if_tool_progress_sink", default=None)


def set_progress_sink(queue: Optional[asyncio.Queue]) -> contextvars.Token:
    return _PROGRESS_SINK.set(queue)


def reset_progress_sink(token: contextvars.Token) -> None:
    _PROGRESS_SINK.reset(token)


def report_progress(message: str, current: Optional[int] = None, total: Optional[int] = None,
                    stage: Optional[str] = None, **extra: Any) -> None:
    """Non-blocking. `current`/`total` are counts of the unit named in `message` (e.g. log
    anomalies fetched so far vs. expected total); `stage` is a short machine-friendly label."""
    queue = _PROGRESS_SINK.get()
    if queue is None:
        return
    item: Dict[str, Any] = {"message": message, "timestamp": time.time()}
    if current is not None:
        item["current"] = int(current)
    if total is not None:
        item["total"] = int(total)
    if stage:
        item["stage"] = stage
    item.update(extra)
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        pass
