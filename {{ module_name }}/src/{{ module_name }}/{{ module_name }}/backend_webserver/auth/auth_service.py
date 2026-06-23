"""
Authentication Service for {{ module_name }}.

Extends :class:`vyra_base.auth.BaseAuthService` with module-specific overrides:

- ``_get_user_manager()``: resolves via DI container.
- ``_validate_usermanager_credentials()``: HTTP delegation to the v2_usermanager
  REST API (POST /login + GET /verify).
- ``check_usermanager_available()``: HTTP query to v2_modulemanager which owns
  the module registry.

All shared logic lives in ``BaseAuthService``.
"""

from __future__ import annotations

import os
import ssl
from typing import Any, Dict, Optional

import aiohttp
from {{ module_name }}.logging_config import get_logger

from vyra_base.auth import BaseAuthService, UsermanagerUnavailableError
from vyra_base.com.clients.redis import RedisClient

from ...container_injection import get_user_manager

logger = get_logger(__name__)


class AuthenticationService(BaseAuthService):
    """
    Authentication service for {{ module_name }}.

    Delegates external UserManager authentication to the v2_usermanager REST API
    and checks UM availability via the v2_modulemanager registry endpoint.
    """

    def __init__(self, redis_client: RedisClient, module_id: str) -> None:
        super().__init__(redis_client, module_id, logger)

    # ------------------------------------------------------------------
    # Abstract implementation
    # ------------------------------------------------------------------

    def _get_user_manager(self) -> Any:
        """Return the UserManager DI instance."""
        return get_user_manager()

    async def _validate_usermanager_credentials(
        self, username: str, password: str
    ) -> Optional[Dict[str, Any]]:
        """
        Validate credentials by delegating to the external UserManager service.

        POSTs to ``{GATEWAY_URL}/{um_name}/api/auth/login`` then GETs
        ``/auth/verify`` to retrieve the full user claims (user_id, role, level).
        """
        gateway = os.environ.get("GATEWAY_URL", "https://traefik:443")
        um_name = await self._resolve_usermanager_name()
        login_url = f"{gateway}/{um_name}/api/auth/login"

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        payload = {
            "username": username,
            "password": password,
            "auth_mode": "local",
            "module_name": "{{ module_name }}",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    login_url,
                    json=payload,
                    ssl=ssl_ctx,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 403:
                        logger.warning(
                            "❌ Access denied by UserManager for '%s' on {{ module_name }}",
                            username,
                        )
                        raise Exception("access_denied")
                    if resp.status in (502, 503, 504, 404):
                        raise UsermanagerUnavailableError(
                            f"UserManager service not reachable (HTTP {resp.status})"
                        )
                    if resp.status not in (200, 201):
                        logger.warning(
                            f"v2_usermanager login returned HTTP {resp.status} for {username}"
                        )
                        return None
                    data = await resp.json()

                if not data.get("success"):
                    return None

                token: str = data.get("token", "")
                roles: list = data.get("roles", [])
                primary_role: str = roles[0] if roles else "viewer"

                verify_url = f"{gateway}/{um_name}/api/auth/verify"
                async with session.get(
                    verify_url,
                    headers={"Authorization": f"Bearer {token}"},
                    ssl=ssl_ctx,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as verify_resp:
                    if verify_resp.status == 200:
                        verify_data = await verify_resp.json()
                        user_claims: dict = verify_data.get("user", {})
                        logger.info(f"✅ User {username} authenticated via v2_usermanager")
                        return {
                            "user_id": user_claims.get("user_id", 0),
                            "username": username,
                            "role": user_claims.get("role", primary_role),
                            "level": user_claims.get("level", 3),
                            "auth_mode": "usermanager",
                        }

                logger.warning(
                    "v2_usermanager /auth/verify did not return 200; using login response data"
                )
                return {
                    "user_id": 0,
                    "username": username,
                    "role": primary_role,
                    "level": 3,
                    "auth_mode": "usermanager",
                }

        except UsermanagerUnavailableError:
            raise
        except (aiohttp.ClientConnectorError, aiohttp.ServerConnectionError, TimeoutError) as exc:
            logger.warning(f"❌ UserManager not reachable (connection error): {exc}")
            raise UsermanagerUnavailableError("UserManager service not reachable") from exc
        except Exception as exc:
            if "access_denied" in str(exc):
                raise
            logger.error(f"❌ Error authenticating via usermanager: {exc}", exc_info=True)
            raise Exception("usermanager authentication error") from exc

    async def check_usermanager_available(self) -> Dict[str, Any]:
        """
        Check if external usermanager is available.

        Delegates to ``{GATEWAY_URL}/v2_modulemanager/api/auth/check-usermanager``
        since the module registry lives in v2_modulemanager.
        """
        gateway = os.environ.get("GATEWAY_URL", "https://traefik:443")
        url = f"{gateway}/v2_modulemanager/api/auth/check-usermanager"
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return {
                        "available": False,
                        "message": f"Module manager returned status {resp.status}",
                    }
        except Exception as exc:
            logger.error(f"❌ Error checking usermanager availability: {exc}", exc_info=True)
            return {"available": False, "message": f"Error reaching module manager: {exc}"}

    async def _resolve_usermanager_name(self) -> str:
        """Resolve UserManager module name from MM availability response."""
        try:
            um_info = await self.check_usermanager_available()
            modules = um_info.get("modules", []) if isinstance(um_info, dict) else []
            if modules:
                module = modules[0] or {}
                resolved = module.get("module_name") or module.get("name")
                if isinstance(resolved, str) and resolved.strip():
                    return resolved.strip()
        except Exception:
            pass
        return "v2_usermanager"
