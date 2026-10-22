"""Per-user FIFO dispatch within one process, with no durability guarantee."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import logging
from threading import Lock
from typing import Any, Callable


logger = logging.getLogger(__name__)


class PerUserDispatcher:
    """Run each user's synchronous handlers serially on a finite worker pool.

    Queues are in-memory and unbounded, and only coordinate within one process.
    There is no durability guarantee. A busy user retains one worker until its
    queue empties; other users may wait when the pool is full. Output must finish
    inside the handler to participate in the per-user FIFO ordering.
    """

    def __init__(self, handler: Callable[[Any], None], max_workers: int = 16):
        self._handler = handler
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = Lock()
        self._queues: dict[int, deque[Any]] = {}
        self._closed = False

    def submit(self, user_id: int, event: Any) -> None:
        """Accept an event in FIFO order, or raise RuntimeError after close."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Dispatcher is closed")
            queue = self._queues.get(user_id)
            if queue is not None:
                queue.append(event)
                return

            self._queues[user_id] = deque([event])
            try:
                self._executor.submit(self._drain, user_id)
            except BaseException:
                del self._queues[user_id]
                raise

    def _drain(self, user_id: int) -> None:
        while True:
            with self._lock:
                queue = self._queues[user_id]
                if not queue:
                    del self._queues[user_id]
                    return
                event = queue.popleft()

            try:
                self._handler(event)
            except Exception as exc:
                # Exception messages and tracebacks may contain event payloads.
                logger.error("Event handler failed (%s)", type(exc).__name__)

    def close(self) -> None:
        """Stop accepting events and wait for all accepted handlers to finish.

        Call from outside the worker pool; a handler cannot join its own worker.
        Pending events are drained rather than cancelled.
        """
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True)
