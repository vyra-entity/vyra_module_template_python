"""
BaseEventBridge — Abstract base class for all event bridge implementations.

Defines the common interface for unidirectional (FeedStreamer) and
bidirectional (PluginBridge) event channels used by the WebSocket layer.

Concrete classes:
    FeedStreamer  — unidirectional Logic→UI, broadcasts to all WS clients
    PluginBridge  — bidirectional Logic↔UI, per-plugin per-channel routing

Features provided by this base class:
    setThrottle(topic, rate_ms)
        Rate-limit publishes per topic.  Subsequent calls within the window
        are silently dropped; the caller is never blocked.

    intercept(topic, fn)
        Register a synchronous data-transform function.  Receives the
        payload dict and must return a (possibly modified) dict.
        Multiple interceptors per topic are applied in registration order.

    checkHealth()
        Async health probe:
        - Measures queue fill percentage for each active subscriber.
        - Publishes a "system" event (topic "system" / feed_type "system")
          if any queue exceeds QUEUE_WARN_THRESHOLD (default 80 %).
        - Returns a dict with health details.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Fraction of queue capacity that triggers a system warning.
QUEUE_WARN_THRESHOLD = 0.80


class BaseEventBridge(ABC):
    """
    Abstract event bridge.

    Subclasses must implement :meth:`publish`, :meth:`subscribe`,
    :meth:`unsubscribe`, :meth:`get_history`, and :meth:`clear_history`.

    The concrete helpers :meth:`setThrottle`, :meth:`intercept`, and
    :meth:`checkHealth` are provided here and shared by all subclasses.
    """

    def __init__(self) -> None:
        # topic → last publish timestamp (monotonic seconds)
        self._throttle_map: dict[str, float] = {}
        # topic → rate in seconds (0 = disabled)
        self._throttle_rate: dict[str, float] = {}
        # topic → list of transform functions
        self._interceptors: dict[str, list[Callable[[dict], dict]]] = {}

    # ------------------------------------------------------------------
    # Abstract interface — must be implemented by each subclass
    # ------------------------------------------------------------------

    @abstractmethod
    async def publish(self, topic: str, payload: Any) -> None:
        """
        Publish *payload* to all subscribers on *topic*.

        Implementations should apply throttle and interceptor hooks
        (``_apply_throttle`` / ``_apply_interceptors``) before distributing.
        """

    @abstractmethod
    def subscribe(self, topic: str, maxsize: int = 100) -> asyncio.Queue:
        """
        Register a new subscriber for *topic*.

        Returns a fresh :class:`asyncio.Queue` that will receive every
        future publish.  The caller is responsible for calling
        :meth:`unsubscribe` when the consumer closes.
        """

    @abstractmethod
    def unsubscribe(self, topic: str, q: asyncio.Queue) -> None:
        """Remove a subscriber queue.  No-op if not registered."""

    @abstractmethod
    def get_history(self, topic: Optional[str] = None) -> list[dict]:
        """
        Return a snapshot of the history ring-buffer.

        :param topic: If given, filter to this topic (implementation choice).
        """

    @abstractmethod
    def clear_history(self, topic: Optional[str] = None) -> None:
        """
        Flush the history ring-buffer.

        :param topic: If given, clear only this topic's history.
        """

    # ------------------------------------------------------------------
    # Concrete helpers — shared across all subclasses
    # ------------------------------------------------------------------

    def setThrottle(self, topic: str, rate_ms: float) -> None:
        """
        Enable per-topic rate limiting.

        :param topic:   Topic name (or empty string for global throttle).
        :param rate_ms: Minimum interval between publishes in milliseconds.
                        Set to 0 to disable throttle for this topic.
        """
        self._throttle_rate[topic] = rate_ms / 1000.0
        if rate_ms == 0:
            self._throttle_map.pop(topic, None)
        logger.debug(
            "%s.setThrottle: topic=%r rate_ms=%.0f",
            type(self).__name__,
            topic,
            rate_ms,
        )

    def intercept(self, topic: str, fn: Callable[[dict], dict]) -> None:
        """
        Register a synchronous transform function for *topic*.

        The function receives the payload dict and must return a dict.
        Multiple interceptors are applied in registration order before
        the payload is placed on subscriber queues.

        :param topic: Topic name the interceptor applies to.
        :param fn:    ``(payload: dict) -> dict`` transform.
        """
        self._interceptors.setdefault(topic, []).append(fn)
        logger.debug(
            "%s.intercept: registered fn=%s for topic=%r",
            type(self).__name__,
            fn.__name__,
            topic,
        )

    async def checkHealth(self) -> dict:
        """
        Health probe for this bridge.

        Checks all active subscriber queues and emits a "system" warning
        event (via :meth:`publish`) if any queue fill exceeds
        ``QUEUE_WARN_THRESHOLD``.

        :returns: ``{"healthy": bool, "details": [...]}``
        """
        details: list[dict] = []
        healthy = True

        for info in self._get_subscriber_queue_info():
            fill = info["fill"]
            details.append(info)
            if fill >= QUEUE_WARN_THRESHOLD:
                healthy = False
                logger.warning(
                    "%s.checkHealth: queue near-full — topic=%r fill=%.0f%%",
                    type(self).__name__,
                    info.get("topic", "?"),
                    fill * 100,
                )
                try:
                    await self.publish(
                        "system",
                        {
                            "event": "queue_overload_warning",
                            "bridge": type(self).__name__,
                            "topic": info.get("topic", "?"),
                            "fill_pct": round(fill * 100, 1),
                            "threshold_pct": round(QUEUE_WARN_THRESHOLD * 100, 1),
                        },
                    )
                except Exception as exc:
                    logger.debug("checkHealth: could not publish system warning: %s", exc)

        return {"healthy": healthy, "details": details}

    # ------------------------------------------------------------------
    # Internal helper hooks — used inside publish() implementations
    # ------------------------------------------------------------------

    def _is_throttled(self, topic: str) -> bool:
        """Return True if this topic is within its throttle interval."""
        rate = self._throttle_rate.get(topic, 0.0)
        if rate <= 0:
            return False
        last = self._throttle_map.get(topic, 0.0)
        now = time.monotonic()
        if now - last < rate:
            return True
        self._throttle_map[topic] = now
        return False

    def _apply_interceptors(self, topic: str, payload: Any) -> Any:
        """Apply all registered interceptors for *topic* to *payload*."""
        fns = self._interceptors.get(topic, [])
        for fn in fns:
            try:
                payload = fn(payload)
            except Exception as exc:
                logger.warning(
                    "%s._apply_interceptors: fn=%s topic=%r raised %s",
                    type(self).__name__,
                    fn.__name__,
                    topic,
                    exc,
                )
        return payload

    def _get_subscriber_queue_info(self) -> list[dict]:
        """
        Return fill-percentage info for all active subscriber queues.

        Subclasses can override or extend this; the default returns an
        empty list (no queue info available at base level).
        """
        return []
