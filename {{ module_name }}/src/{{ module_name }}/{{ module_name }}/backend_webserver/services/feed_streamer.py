"""
FeedStreamer — Unidirectional In-Process WebSocket Feed Bus

Decouples the application core (vyra_base) from the FastAPI WebSocket
layer via a lightweight asyncio-based pub/sub bus.  Extends BaseEventBridge
to gain throttle, interceptor, and health-check capabilities.

Flow (unidirectional — Logic → UI only)
---------------------------------------
Application core         WebSocket handler
  (sync or async)             (async)
       │                         │
       │ publish_feed()          │ subscribe() → Queue
       ▼                         ▼
  ┌───────────────────────────────────┐
  │  FeedStreamer (singleton)          │
  │  • history deque (ring buffer)    │
  │  • list[asyncio.Queue]            │
  │  • throttle / interceptors (base) │
  └───────────────────────────────────┘

Usage (application layer — sync)
---------------------------------
    from ..backend_webserver.services.feed_streamer import FeedStreamer, FeedMessage

    FeedStreamer.get_instance().publish_feed(FeedMessage(
        module_name="{{ module_name }}",
        module_id="...",
        feed_type="news",
        message="heartbeat",
        timestamp=datetime.now().isoformat(),
        data={}
    ))

Usage (WebSocket handler)
-------------------------
    fm = FeedStreamer.get_instance()
    queue = fm.subscribe()
    try:
        for msg in fm.get_history():
            await ws.send_json({"type": "feed", "data": msg})
        while True:
            data = await queue.get()
            await ws.send_json({"type": "feed", "data": data})
    finally:
        fm.unsubscribe("", queue)
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, List, Optional

from .base_event_bridge import BaseEventBridge

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FeedMessage:
    """Canonical feed message passed through the in-process bus.

    ``feed_type`` values: ``"news"`` | ``"state"`` | ``"error"`` | ``"system"`` | ``"custom"``

    All fields are plain JSON-serialisable types so that WebSocket handlers
    can forward them without any further transformation.
    """

    module_name: str
    module_id: str
    feed_type: str  # "news" | "state" | "error" | "system" | "custom"
    message: str
    timestamp: str  # ISO-8601
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return as plain dict (JSON-ready)."""
        return asdict(self)


# ---------------------------------------------------------------------------
# FeedStreamer
# ---------------------------------------------------------------------------


class FeedStreamer(BaseEventBridge):
    """
    Singleton in-process pub/sub bus for module feed messages.
    Unidirectional: application logic → WebSocket clients only.

    Extends :class:`BaseEventBridge` to gain per-topic throttle,
    data-transform interceptors, and :meth:`checkHealth` support.

    Sync publish (application layer)
    ----------------------------------
    Use :meth:`publish_feed` from sync code (non-async callers, ROS2 cb, …).
    It uses ``Queue.put_nowait()`` so it never blocks the caller.

    For callers on a *different thread* use
    :meth:`publish_threadsafe(msg, loop)` instead.

    Async publish (BaseEventBridge interface)
    ------------------------------------------
    ``await publish(topic, payload)`` is the generic async interface; it is
    used by :meth:`checkHealth` and can be called from async contexts.
    ``topic`` maps to ``FeedMessage.feed_type``.

    Parameters
    ----------
    history_size : int
        Maximum number of messages kept in the ring-buffer sent to new
        WebSocket clients on connect.  Defaults to 100.
    queue_size : int
        Maximum depth of each subscriber queue.  Defaults to 500.
    """

    _instance: Optional["FeedStreamer"] = None

    def __init__(self, history_size: int = 100, queue_size: int = 500) -> None:
        super().__init__()
        self._history: deque[dict] = deque(maxlen=history_size)
        self._subscribers: List[asyncio.Queue] = []
        self._queue_size = queue_size

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls, *, history_size: int = 100, queue_size: int = 500) -> "FeedStreamer":
        """Return the process-wide FeedStreamer singleton."""
        if cls._instance is None:
            cls._instance = cls(history_size=history_size, queue_size=queue_size)
            logger.info(
                "✅ FeedStreamer singleton created (history=%d, queue=%d)", history_size, queue_size
            )
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton — intended for tests only."""
        cls._instance = None

    # ------------------------------------------------------------------
    # Publisher API — sync convenience (original interface preserved)
    # ------------------------------------------------------------------

    def publish_feed(self, msg: FeedMessage) -> None:
        """
        Non-blocking sync publish from any sync or async context.

        Applies registered interceptors for ``msg.feed_type``, checks the
        per-topic throttle, then appends to history and fans out to every
        active subscriber queue.  Slow subscribers (full queue) are skipped.
        """
        if self._is_throttled(msg.feed_type):
            logger.debug("FeedStreamer: throttled topic=%r", msg.feed_type)
            return

        payload = msg.to_dict()
        payload = self._apply_interceptors(msg.feed_type, payload)

        self._history.append(payload)

        dropped = 0
        for q in self._subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dropped += 1

        if dropped:
            logger.debug("FeedStreamer: dropped %d message(s) for slow subscribers", dropped)

    def publish_threadsafe(self, msg: FeedMessage, loop: asyncio.AbstractEventLoop) -> None:
        """
        Thread-safe variant for callers on a foreign thread (e.g. ROS2 executor).

        Uses ``call_soon_threadsafe`` to schedule :meth:`publish_feed` on *loop*.
        """
        loop.call_soon_threadsafe(self.publish_feed, msg)

    # ------------------------------------------------------------------
    # BaseEventBridge.publish — async interface (used by checkHealth etc.)
    # ------------------------------------------------------------------

    async def publish(self, topic: str, payload: Any) -> None:  # type: ignore[override]
        """
        Async publish to *topic* (maps to ``FeedMessage.feed_type``).

        *payload* must be a dict.  Applies throttle + interceptors before
        distributing to subscriber queues.
        """
        if self._is_throttled(topic):
            return

        if isinstance(payload, dict):
            payload = self._apply_interceptors(topic, payload)

        self._history.append(payload)

        dropped = 0
        for q in self._subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dropped += 1

        if dropped:
            logger.debug("FeedStreamer.publish: dropped %d for slow subscribers", dropped)

    # ------------------------------------------------------------------
    # Subscriber API (BaseEventBridge interface)
    # ------------------------------------------------------------------

    def subscribe(self, topic: str = "", maxsize: int = 0) -> asyncio.Queue:  # type: ignore[override]
        """
        Register a new subscriber.

        *topic* is accepted for interface compatibility but ignored —
        FeedStreamer broadcasts all messages to every subscriber.

        Returns a fresh :class:`asyncio.Queue` (size = ``queue_size`` set at
        construction time).  The caller is responsible for calling
        :meth:`unsubscribe` when the consumer closes.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.append(q)
        logger.debug("FeedStreamer: subscriber added (total=%d)", len(self._subscribers))
        return q

    def unsubscribe(self, topic: str, q: asyncio.Queue) -> None:  # type: ignore[override]
        """Remove a subscriber queue (*topic* ignored).  No-op if not registered."""
        try:
            self._subscribers.remove(q)
            logger.debug("FeedStreamer: subscriber removed (total=%d)", len(self._subscribers))
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # History API (BaseEventBridge interface)
    # ------------------------------------------------------------------

    def get_history(self, topic: Optional[str] = None) -> list[dict]:  # type: ignore[override]
        """Return a snapshot of the history ring-buffer (oldest first).  *topic* ignored."""
        return list(self._history)

    def clear_history(self, topic: Optional[str] = None) -> None:  # type: ignore[override]
        """Flush the history ring-buffer — intended for tests."""
        self._history.clear()

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def subscriber_count(self) -> int:
        """Number of currently registered subscribers."""
        return len(self._subscribers)

    @property
    def history_count(self) -> int:
        """Number of messages currently held in the history buffer."""
        return len(self._history)

    # ------------------------------------------------------------------
    # BaseEventBridge health-check support
    # ------------------------------------------------------------------

    def _get_subscriber_queue_info(self) -> list[dict]:
        """Return fill-percentage info for every active subscriber queue."""
        result = []
        for i, q in enumerate(self._subscribers):
            maxsize = q.maxsize or 1
            fill = q.qsize() / maxsize
            result.append({"topic": "feed", "subscriber_index": i, "fill": fill})
        return result
