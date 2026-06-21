"""Dedicated browser worker thread for Playwright operations.

Solves the "cannot switch to a different thread" error by ensuring all
Playwright operations run on a single, long-lived worker thread.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_worker_lock = threading.Lock()
_worker_instance: Optional["BrowserWorker"] = None


class BrowserWorker:
    """Dedicated thread for all Playwright browser operations."""

    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[Callable, tuple, dict, queue.Queue]] = queue.Queue()
        self._shutdown = threading.Event()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="playwright-worker",
            daemon=False,  # Graceful shutdown
        )
        self._thread.start()
        logger.info("Browser worker thread started: thread_id=%s", self._thread.ident)

    def execute(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute a function on the browser worker thread and return result."""
        if threading.current_thread() == self._thread:
            # Already on worker thread, execute directly
            return func(*args, **kwargs)

        result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue()
        self._queue.put((func, args, kwargs, result_queue))

        success, result = result_queue.get()
        if not success:
            raise result  # Re-raise exception from worker thread
        return result

    def shutdown(self, timeout: float = 10.0) -> None:
        """Shutdown the worker thread gracefully."""
        self._shutdown.set()
        self._queue.put((lambda: None, (), {}, queue.Queue()))  # Wake up worker
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            logger.warning("Browser worker thread did not stop within %ss", timeout)
        else:
            logger.info("Browser worker thread stopped cleanly")

    def _worker_loop(self) -> None:
        """Main loop running on the dedicated browser thread."""
        logger.info("Browser worker loop started")
        while not self._shutdown.is_set():
            try:
                func, args, kwargs, result_queue = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if self._shutdown.is_set():
                break

            try:
                result = func(*args, **kwargs)
                result_queue.put((True, result))
            except Exception as exc:
                logger.exception("Browser worker caught exception: %s", exc)
                result_queue.put((False, exc))

        logger.info("Browser worker loop exited")


def get_browser_worker() -> BrowserWorker:
    """Get or create the global browser worker instance."""
    global _worker_instance
    with _worker_lock:
        if _worker_instance is None:
            _worker_instance = BrowserWorker()
        return _worker_instance


def shutdown_browser_worker() -> None:
    """Shutdown the global browser worker."""
    global _worker_instance
    with _worker_lock:
        if _worker_instance is not None:
            _worker_instance.shutdown()
            _worker_instance = None
