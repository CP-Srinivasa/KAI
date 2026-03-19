"""Digest collector.

Accumulates AlertMessages and flushes them as a batch for digest delivery.
Simple, stateful, not thread-safe (single-threaded async context assumed).
"""

from __future__ import annotations

from collections import deque

from app.alerts.base.interfaces import AlertMessage


class DigestCollector:
    """Accumulator for digest-mode alerts.

    max_size: maximum number of messages to hold before the oldest is dropped
              (deque with maxlen — never grows unbounded).
    """

    def __init__(self, max_size: int = 100) -> None:
        if max_size < 1:
            raise ValueError(f"max_size must be >= 1, got {max_size}")
        self._max_size = max_size
        self._queue: deque[AlertMessage] = deque(maxlen=max_size)

    def add(self, message: AlertMessage) -> None:
        """Add a message to the digest queue."""
        self._queue.append(message)

    def flush(self) -> list[AlertMessage]:
        """Return all collected messages and clear the queue."""
        messages = list(self._queue)
        self._queue.clear()
        return messages

    def peek(self) -> list[AlertMessage]:
        """Return all messages without clearing the queue."""
        return list(self._queue)

    def is_empty(self) -> bool:
        return len(self._queue) == 0

    def __len__(self) -> int:
        return len(self._queue)
