"""Real-time execution event bus."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from database.connection import get_db_connection
from observability.metrics import increment_counter, set_gauge

logger = logging.getLogger(__name__)


class ExecutionEventBus:
    """Thread-safe process-wide event bus for execution telemetry."""

    def __init__(self, history_limit: int = 200):
        self._history: Deque[Dict[str, Any]] = deque(maxlen=history_limit)
        self._subscribers: Dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self._lock = threading.Lock()

    def emit(
        self,
        *,
        component: str,
        action_name: str,
        status: str,
        duration_ms: Optional[float] = None,
        screenshot_path: Optional[str] = None,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        message_id: Optional[str] = None,
        approval_id: Optional[str] = None,
        data_source: str = "unknown",
        detail: Optional[str] = None,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        event = {
            "event_id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(),
            "component": component,
            "action_name": action_name,
            "status": status,
            "duration_ms": round(float(duration_ms), 2) if duration_ms is not None else None,
            "session_id": session_id,
            "trace_id": trace_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "approval_id": approval_id,
            "screenshot_path": _normalize_path(screenshot_path),
            "data_source": data_source,
            "detail": detail,
            "error": error,
            "metadata": metadata or {},
        }
        event["screenshot_data"] = _encode_screenshot(event["screenshot_path"])

        self._persist_event(event)

        with self._lock:
            self._history.append(event)
            subscribers = list(self._subscribers.items())

        increment_counter("execution_events_total")
        if event["status"] == "failure":
            increment_counter("execution_events_failure_total")
        set_gauge("execution_subscribers", float(len(subscribers)))

        stale_queues: List[asyncio.Queue] = []
        for queue, loop in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                stale_queues.append(queue)

        if stale_queues:
            with self._lock:
                for queue in stale_queues:
                    self._subscribers.pop(queue, None)

        return event

    async def subscribe(self) -> Tuple[asyncio.Queue, List[Dict[str, Any]]]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers[queue] = loop
            history = list(self._history)
        set_gauge("execution_subscribers", float(len(self._subscribers)))
        return queue, history

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.pop(queue, None)
            subscriber_count = len(self._subscribers)
        set_gauge("execution_subscribers", float(subscriber_count))

    def recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._history)[-limit:] if limit > 0 else []

    def latest_screenshot_event(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            for event in reversed(self._history):
                if event.get("screenshot_data"):
                    return event
        return None

    def _persist_event(self, event: Dict[str, Any]) -> None:
        conn = get_db_connection()
        try:
            conn.execute(
                """
                INSERT INTO execution_logs (
                    event_id, event_time, component, action_name, status, duration_ms,
                    session_id, trace_id, conversation_id, message_id, approval_id,
                    screenshot_path, data_source, detail, error, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event["timestamp"],
                    event["component"],
                    event["action_name"],
                    event["status"],
                    event["duration_ms"],
                    event["session_id"],
                    event["trace_id"],
                    event["conversation_id"],
                    event["message_id"],
                    event["approval_id"],
                    event["screenshot_path"],
                    event["data_source"],
                    event["detail"],
                    event["error"],
                    json.dumps(event["metadata"], ensure_ascii=False),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("failed to persist execution event")
        finally:
            conn.close()


def _normalize_path(path: Optional[str]) -> Optional[str]:
    return str(Path(path).resolve()) if path else None


def _encode_screenshot(path: Optional[str]) -> Optional[str]:
    if not path:
        return None

    target = Path(path)
    if not target.exists() or not target.is_file():
        return None

    try:
        encoded = base64.b64encode(target.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception:
        logger.exception("failed to encode screenshot: %s", path)
        return None


_execution_event_bus = ExecutionEventBus()


def get_execution_event_bus() -> ExecutionEventBus:
    return _execution_event_bus
