"""In-memory SSE broadcaster."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any


class InMemoryBroadcaster:
    """In-memory event broadcaster for SSE."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[Any]] = []

    def subscribe(self) -> asyncio.Queue[Any]:
        """Subscribe to events."""
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=100)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Any]) -> None:
        """Unsubscribe from events."""
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def publish(self, event: Any) -> None:
        """Publish event to all subscribers."""
        for queue in self._subscribers:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)
