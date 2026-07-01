"""
PluginBridge — Bidirectional Plugin Event Channel

Provides real-time communication between application logic and
WebSocket-connected plugin UI components.

Direction comparison
--------------------
FeedStreamer  — unidirectional  Logic → UI  (module status / news)
PluginBridge  — bidirectional   Logic ↔ UI  (plugin commands / responses)

Channel model
-------------
Messages are routed by ``channel`` (arbitrary string, e.g. ``"counter"``,
``"alert"``, ``"state"``).  Multiple frontend clients can subscribe to the
same channel; they all receive every message published to that channel.

Usage (application / plugin logic)
------------------------------------
    bridge = PluginBridge.get_instance()

    # Publish to all subscribers on a channel (async context)
    await bridge.publish("counter.update", {"value": 42})

    # Same from a sync context (e.g. WASM host function, ROS2 callback)
    bridge.publish_sync("counter.update", {"value": 42})

    # Receive messages sent by a plugin UI → register a handler
    bridge.register_handler("counter.reset", handle_reset)

Usage (WebSocket endpoint)
--------------------------
    bridge = PluginBridge.get_instance()
    queue = bridge.subscribe("counter.update")
    try:
        while True:
            payload = await queue.get()
            await ws.send_json({"type": "plugin_event", "data": payload})
    finally:
        bridge.unsubscribe("counter.update", queue)

    # When the UI sends a message:
    bridge.receive_sync("counter.reset", {"origin": "ui"})
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any, Callable, Optional

from .base_event_bridge import BaseEventBridge

logger = logging.getLogger(__name__)

# How many messages to keep per channel in the history ring-buffer.
_HISTORY_SIZE = 50


class PluginBridge(BaseEventBridge):
    """
    Singleton bidirectional event bridge for plugin communication.

    Logic → UI direction
    ~~~~~~~~~~~~~~~~~~~~
    :meth:`publish`       async publish from application/task code
    :meth:`publish_sync`  sync publish for WASM host functions / non-async callers

    UI → Logic direction
    ~~~~~~~~~~~~~~~~~~~~
    :meth:`receive`        async delivery of a UI-originated message to all
                           registered backend handlers.
    :meth:`receive_sync`   sync variant (schedules on the running event loop).
    :meth:`register_handler`   register ``async def handler(payload)`` for a channel.
    :meth:`unregister_handler` remove a previously registered handler.

    Subscriber management (BaseEventBridge interface)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    :meth:`subscribe`     returns an :class:`asyncio.Queue` for a channel.
    :meth:`unsubscribe`   removes a queue when the WebSocket closes.

    History (BaseEventBridge interface)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    :meth:`get_history`   returns last N messages for a channel.
    :meth:`clear_history` flushes the ring-buffer.
    """

    _instance: Optional["PluginBridge"] = None

    def __init__(self) -> None:
        super().__init__()
        # channel → list of subscriber queues
        self._out_subs: dict[str, list[asyncio.Queue]] = {}
        # channel → list of backend handler coroutines
        self._in_handlers: dict[str, list[Callable[[dict], Any]]] = {}
        # channel → ring-buffer of sent messages
        self._history: dict[str, deque[dict]] = {}
        # reference to the running event loop (set lazily in publish_sync)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> "PluginBridge":
        """Return the process-wide PluginBridge singleton."""
        if cls._instance is None:
            cls._instance = cls()
            logger.info("✅ PluginBridge singleton created")
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton — intended for tests only."""
        cls._instance = None

    # ------------------------------------------------------------------
    # Logic → UI  (async publish)
    # ------------------------------------------------------------------

    async def publish(self, topic: str, payload: Any) -> None:
        """
        Async publish to all subscribers on *topic* / *channel*.

        Applies throttle and interceptors before distribution.
        Records every message in the per-channel history ring-buffer.
        """
        if self._is_throttled(topic):
            logger.debug("PluginBridge: throttled topic=%r", topic)
            return

        if isinstance(payload, dict):
            payload = self._apply_interceptors(topic, payload)

        # Record in history
        buf = self._history.setdefault(topic, deque(maxlen=_HISTORY_SIZE))
        buf.append(payload)

        # Fan-out to all subscribers
        subs = self._out_subs.get(topic, [])
        dropped = 0
        for q in subs:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dropped += 1

        if dropped:
            logger.debug(
                "PluginBridge.publish: dropped %d for slow subscribers on %r", dropped, topic
            )

    def publish_sync(self, topic: str, payload: Any) -> None:
        """
        Sync publish for non-async callers (WASM host functions, ROS2 callbacks).

        Schedules :meth:`publish` on the running event loop.  If no loop is
        running yet the call is silently ignored (early init phase).
        """
        try:
            loop = self._loop or asyncio.get_event_loop()
            if loop.is_running():
                loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self.publish(topic, payload), loop=loop)
                )
            else:
                # Fallback: run synchronously (test / startup scenarios)
                loop.run_until_complete(self.publish(topic, payload))
        except RuntimeError:
            logger.debug("PluginBridge.publish_sync: no event loop, dropping topic=%r", topic)

    # ------------------------------------------------------------------
    # UI → Logic  (receive direction)
    # ------------------------------------------------------------------

    async def receive(self, channel: str, payload: Any) -> None:
        """
        Deliver a UI-originated message to all registered backend handlers.

        Called by the ``/ws/plugin/{plugin_id}/{channel}`` WebSocket endpoint
        when a ``client_to_server`` message arrives.
        """
        handlers = self._in_handlers.get(channel, [])
        for fn in handlers:
            try:
                result = fn(payload)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.warning(
                    "PluginBridge.receive: handler %s raised on channel=%r: %s",
                    fn.__name__,
                    channel,
                    exc,
                )

    def receive_sync(self, channel: str, payload: Any) -> None:
        """
        Sync variant of :meth:`receive` for thread-safe contexts.
        """
        try:
            loop = self._loop or asyncio.get_event_loop()
            if loop.is_running():
                loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self.receive(channel, payload), loop=loop)
                )
            else:
                loop.run_until_complete(self.receive(channel, payload))
        except RuntimeError:
            logger.debug("PluginBridge.receive_sync: no event loop, dropping channel=%r", channel)

    def register_handler(self, channel: str, fn: Callable[[dict], Any]) -> None:
        """
        Register *fn* as a backend handler for *channel*.

        *fn* may be a regular function or a coroutine function.
        Multiple handlers per channel are supported.
        """
        self._in_handlers.setdefault(channel, []).append(fn)
        logger.debug("PluginBridge: registered handler %s for channel=%r", fn.__name__, channel)

    def unregister_handler(self, channel: str, fn: Callable[[dict], Any]) -> None:
        """Remove a previously registered handler.  No-op if not found."""
        handlers = self._in_handlers.get(channel, [])
        try:
            handlers.remove(fn)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Subscriber management (BaseEventBridge interface)
    # ------------------------------------------------------------------

    def subscribe(self, topic: str, maxsize: int = 100) -> asyncio.Queue:
        """
        Register a new subscriber queue for *topic* / *channel*.

        Returns a fresh :class:`asyncio.Queue`.  The caller must call
        :meth:`unsubscribe` when the WebSocket closes.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._out_subs.setdefault(topic, []).append(q)
        logger.debug(
            "PluginBridge: subscriber added for channel=%r (total=%d)",
            topic,
            len(self._out_subs[topic]),
        )
        return q

    def unsubscribe(self, topic: str, q: asyncio.Queue) -> None:
        """Remove a subscriber queue.  No-op if not registered."""
        subs = self._out_subs.get(topic, [])
        try:
            subs.remove(q)
            logger.debug(
                "PluginBridge: subscriber removed from channel=%r (remaining=%d)",
                topic,
                len(subs),
            )
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # History (BaseEventBridge interface)
    # ------------------------------------------------------------------

    def get_history(self, topic: Optional[str] = None) -> list[dict]:
        """
        Return history ring-buffer contents.

        :param topic: If given, return history for this channel only.
                      If ``None``, return flattened history for all channels.
        """
        if topic is not None:
            return list(self._history.get(topic, []))
        merged = []
        for buf in self._history.values():
            merged.extend(buf)
        return merged

    def clear_history(self, topic: Optional[str] = None) -> None:
        """Flush history.  If *topic* is given, clear only that channel."""
        if topic is not None:
            if topic in self._history:
                self._history[topic].clear()
        else:
            self._history.clear()

    # ------------------------------------------------------------------
    # Health-check support
    # ------------------------------------------------------------------

    def _get_subscriber_queue_info(self) -> list[dict]:
        """Return fill-percentage info for every active subscriber queue."""
        result = []
        for channel, subs in self._out_subs.items():
            for i, q in enumerate(subs):
                maxsize = q.maxsize or 1
                fill = q.qsize() / maxsize
                result.append({"topic": channel, "subscriber_index": i, "fill": fill})
        return result

    # ------------------------------------------------------------------
    # Internal: cache event loop reference on first async use
    # ------------------------------------------------------------------

    def _cache_loop(self) -> None:
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
