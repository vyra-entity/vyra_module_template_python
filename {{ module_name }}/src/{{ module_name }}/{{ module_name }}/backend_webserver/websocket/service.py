"""
WebSocket Service — reusable infrastructure for the WebSocket router.

Extracted from router.py so that the connection manager and operation-monitor
helper can be imported and used from other parts of the codebase (e.g. tests,
other routers) without pulling in FastAPI route decorators.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, Set

from fastapi import WebSocket

from ..core.dependencies import module_operations

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Tracks active WebSocket connections, grouped by client_id.

    Also manages per-operation subscriptions so that individual operations
    can be watched by any number of clients simultaneously.
    """

    def __init__(self) -> None:
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.operation_subscriptions: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, client_id: str) -> None:
        await websocket.accept()
        self.active_connections.setdefault(client_id, set()).add(websocket)
        logger.info("Client %s connected via WebSocket", client_id)

    def disconnect(self, websocket: WebSocket, client_id: str) -> None:
        if client_id in self.active_connections:
            self.active_connections[client_id].discard(websocket)
            if not self.active_connections[client_id]:
                del self.active_connections[client_id]

        for operation_id in list(self.operation_subscriptions.keys()):
            self.operation_subscriptions[operation_id].discard(websocket)
            if not self.operation_subscriptions[operation_id]:
                del self.operation_subscriptions[operation_id]

        logger.info("Client %s disconnected from WebSocket", client_id)

    async def send_personal_message(self, message: str, websocket: WebSocket) -> None:
        try:
            await websocket.send_text(message)
        except Exception as exc:
            logger.warning("Failed to send message to WebSocket: %s", exc)

    async def send_to_client(self, message: str, client_id: str) -> None:
        if client_id in self.active_connections:
            for websocket in list(self.active_connections[client_id]):
                try:
                    await websocket.send_text(message)
                except Exception as exc:
                    logger.warning("Failed to send message to client %s: %s", client_id, exc)
                    self.active_connections[client_id].discard(websocket)

    async def broadcast(self, message: str) -> None:
        for client_connections in self.active_connections.values():
            for websocket in list(client_connections):
                try:
                    await websocket.send_text(message)
                except Exception as exc:
                    logger.warning("Failed to broadcast message: %s", exc)

    def subscribe_to_operation(self, websocket: WebSocket, operation_id: str) -> None:
        self.operation_subscriptions.setdefault(operation_id, set()).add(websocket)
        logger.info("WebSocket subscribed to operation %s", operation_id)

    async def notify_operation_update(self, operation_id: str, operation_data: dict) -> None:
        if operation_id not in self.operation_subscriptions:
            return
        message = json.dumps(
            {
                "type": "operation_update",
                "operation_id": operation_id,
                "data": operation_data,
            }
        )
        for websocket in list(self.operation_subscriptions[operation_id]):
            try:
                await websocket.send_text(message)
            except Exception as exc:
                logger.warning("Failed to send operation update: %s", exc)
                self.operation_subscriptions[operation_id].discard(websocket)


# Module-level singleton — shared between all routes in websocket/router.py
connection_manager = ConnectionManager()


async def notify_operation_update(operation_id: str, operation_data: dict) -> None:
    """Convenience function — notify all subscribers of *operation_id*."""
    await connection_manager.notify_operation_update(operation_id, operation_data)


async def operation_monitor() -> None:
    """
    Background task that polls ``module_operations`` for changes and
    pushes updates to all subscribed WebSocket clients.
    """
    last_state: dict = {}

    while True:
        try:
            current = dict(module_operations)
            for operation_id, data in current.items():
                if last_state.get(operation_id) != data:
                    await notify_operation_update(operation_id, data)
            last_state = current.copy()
            await asyncio.sleep(1)
        except Exception as exc:
            logger.error("Error in operation monitor: %s", exc)
            await asyncio.sleep(5)
