"""
Redis Service for REST API backend_webserver

Thin singleton that provisions a connected vyra_base RedisClient.
All pub/sub, stream and storage operations are performed directly on
the RedisClient — this service only handles lifecycle (connect / health
/ cleanup) and provides a shared instance to the application.

Usage:
    from .services.redis_service import redis_service, get_redis_client

    client = await get_redis_client()           # direct RedisClient access
    await client.publish_message(ch, payload)
    await client.create_pubsub_listener(ch, cb)
    await client.set(key, value)
"""

from typing import Any, Dict, Optional

from vyra_base.com.clients.redis import RedisClient

from {{ module_name }}.logging_config import (
    get_logger,
    log_exception,
)

logger = get_logger(__name__)


class RestApiRedisService:
    """
    Singleton lifecycle manager for the vyra_base RedisClient.

    Responsibilities:
    - Instantiate and connect a RedisClient on first use
    - Provide thread-safe singleton access via get_client()
    - Forward health_check() and cleanup() to the underlying client

    All publish/subscribe/stream/storage operations belong to the
    RedisClient itself — this class does NOT duplicate them.
    """

    _instance: Optional["RestApiRedisService"] = None
    _client: Optional[RedisClient] = None

    # ------------------------------------------------------------------
    # Singleton construction
    # ------------------------------------------------------------------

    def __new__(cls) -> "RestApiRedisService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "_initialized"):
            self._initialized = True
            self._create_client()

    def _create_client(self) -> None:
        """Instantiate the vyra_base RedisClient (connection established lazily on first use)."""
        try:
            self._client = RedisClient(module_name="{{ module_name }}_rest_api")
            logger.info("🔗 RestApiRedisService: RedisClient created (connection deferred until first use)")
        except Exception as exc:
            log_exception(logger, exc, context={"operation": "RedisClient.__init__"})
            raise

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_client(self) -> RedisClient:
        """
        Return a connected RedisClient, establishing the connection if necessary.

        Returns:
            RedisClient: Connected vyra_base Redis client
        """
        if self._client is None:
            raise RuntimeError("RedisClient was not created — RestApiRedisService init failed")

        if not self._client._connected:
            await self._client.connect()

        return self._client

    async def health_check(self) -> Dict[str, Any]:
        """Delegate health check to the underlying RedisClient."""
        try:
            client = await self.get_client()
            healthy = await client.health_check()
            return {
                "status": "healthy" if healthy else "unhealthy",
                "service": "RestApiRedisService",
                "backend": "vyra_base.RedisClient",
                "connected": healthy,
                "module_name": "{{ module_name }}_rest_api",
            }
        except Exception as exc:
            return {
                "status": "error",
                "service": "RestApiRedisService",
                "backend": "vyra_base.RedisClient",
                "connected": False,
                "error": str(exc),
            }

    async def cleanup(self) -> None:
        """Close the underlying Redis connection."""
        try:
            if self._client is not None:
                await self._client.close()
                logger.info("✅ RestApiRedisService: RedisClient closed")
        except Exception as exc:
            logger.error(f"❌ RestApiRedisService cleanup error: {exc}")


# ---------------------------------------------------------------------------
# Module-level singleton + helpers
# ---------------------------------------------------------------------------

#: Shared singleton used by websocket/router and main_rest lifespan
redis_service = RestApiRedisService()


async def get_redis_client() -> RedisClient:
    """Convenience shortcut — returns the connected shared RedisClient."""
    return await redis_service.get_client()


async def redis_health_check() -> Dict[str, Any]:
    """Health check helper for monitoring endpoints."""
    return await redis_service.health_check()
