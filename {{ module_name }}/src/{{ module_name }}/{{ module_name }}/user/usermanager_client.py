"""
External UserManager client.

This client does not store local users. It only connects to a dedicated
external usermanager module and requests authorization tokens for secure
inter-module operations.
"""

import asyncio
import os
from typing import Any, Optional

from vyra_base.com import TransportProviderFactory, ProtocolType
from vyra_base.core.entity import VyraEntity

from ..logging_config import get_logger

logger = get_logger(__name__)


class UserManagerClient:
    """Client for external usermanager module token requests."""

    def __init__(self, entity: VyraEntity):
        self.entity = entity
        self.target_module_name = os.getenv("EXTERNAL_USERMANAGER_MODULE", "v2_usermanager")
        self.target_module_id = os.getenv("EXTERNAL_USERMANAGER_MODULE_ID", "")
        self.request_token_function = os.getenv("USERMANAGER_TOKEN_FUNCTION", "request_access_token")
        self.healthcheck_function = os.getenv("USERMANAGER_HEALTH_FUNCTION", "ping")
        self._connected = False
        # Auto-disable when no external module ID is configured
        _client_enabled = os.getenv("USERMANAGER_CLIENT_ENABLED", "true").lower() == "true"
        self.enabled = _client_enabled and bool(self.target_module_id)

    async def initialize(self) -> bool:
        if not self.enabled:
            logger.info("usermanager_client_disabled")
            return True

        logger.info(
            "usermanager_client_initializing",
            target_module=self.target_module_name,
            target_module_id=self.target_module_id,
        )
        self._connected = True
        return True

    async def shutdown(self) -> None:
        self._connected = False
        logger.info("usermanager_client_stopped")

    async def _call_external(self, function_name: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        client = await TransportProviderFactory.create_client(
            name=function_name,
            protocols=[ProtocolType.ZENOH],
            module_name=self.target_module_name,
            module_id=self.target_module_id,
        )
        result = await client.call(payload or {})
        return result if isinstance(result, dict) else {"result": result}

    async def request_authorization_token(
        self,
        target_module_name: str,
        target_module_id: str,
        required_access_level: int,
        scope: str = "module_access",
    ) -> dict[str, Any]:
        """
        Request an authorization token from external usermanager module.
        """
        if not self.enabled:
            return {"success": False, "message": "usermanager_client is disabled"}

        payload = {
            "requester_module": self.entity.module_entry.name,
            "requester_module_id": self.entity.module_entry.uuid,
            "target_module": target_module_name,
            "target_module_id": target_module_id,
            "required_access_level": required_access_level,
            "scope": scope,
        }
        return await self._call_external(self.request_token_function, payload)

    async def healthcheck(self) -> dict[str, Any]:
        if not self.enabled:
            return {"success": True, "message": "disabled"}
        return await self._call_external(self.healthcheck_function, {})


async def usermanager_client_runner(entity: VyraEntity) -> None:
    """TaskManager runner for external usermanager client."""
    client = UserManagerClient(entity)

    try:
        if not await client.initialize():
            logger.error("usermanager_client_init_failed")
            return

        logger.info("usermanager_client_running")
        while True:
            try:
                await client.healthcheck()
            except Exception as exc:
                logger.warning("usermanager_client_healthcheck_failed", error=str(exc))
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        logger.info("usermanager_client_cancelled")
    finally:
        await client.shutdown()
