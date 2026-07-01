"""
Plugin API — Pydantic schemas for the plugin REST endpoints.

WASM runtime management has moved to PluginGateway
({{ module_name }}.plugin.plugin_gateway).  The legacy WasmRuntimePool
class is kept here only for reference — it is no longer instantiated
as a module-level singleton.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from vyra_base.plugin.runtime import create_plugin_runtime, PluginRuntime, PluginCallError
from vyra_base.plugin.host_functions import NullHostFunctions

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic-Schemas
# ---------------------------------------------------------------------------


class PluginCallRequest(BaseModel):
    function_name: str = Field(description="Name of the plugin function to call")
    data: dict[str, Any] = Field(default_factory=dict, description="Input parameters")


class PluginCallResponse(BaseModel):
    plugin_id: str
    function_name: str
    result: dict[str, Any]
    success: bool = True


class PluginListEntry(BaseModel):
    id: str
    name: str
    version: str
    description: str
    status: str
    scope: dict[str, Any]
    icon: str | None = None


class UiManifestEntry(BaseModel):
    comp_id: str = ""
    slot_id: str
    component_name: str
    js_entry_point: str  # URL via /plugin/assets/ proxy
    nfs_js_path: str = ""  # Absolute NFS path (backend-internal only)
    plugin_id: str
    version: str
    assignment_id: str = ""
    is_active: bool = True
    scope_type: str = ""
    scope_target: str | None = None
    slot_scope_type: str | None = None
    slot_scope_target: str | None = None
    is_frontend_scope: bool = False
    ui_binding_id: str | None = None
    communication_module_name: str | None = None
    hosting_module_name: str = ""
    # Plugin slot infrastructure fields (mirrors manifest.yaml / TypeScript UiManifestEntry)
    slot_ids: list[str] = Field(default_factory=list)
    title: str = ""
    priority: int = 50
    min_user_role: str = "operator"
    search_keywords: list[str] = Field(default_factory=list)
    icon: str | None = None
    slot_type: str = ""


class UiManifestResponse(BaseModel):
    # Field names mirror the dict returned by PluginManager._resolve_plugins_impl
    # and the TypeScript ResolvePluginsResponse interface in plugin.api.ts.
    scope_type: str = ""
    scope_target: str | None = None
    p_id: str | None = None
    ui_slots: dict[str, list[UiManifestEntry]] = Field(default_factory=dict)
    plugin_metadata: list[Any] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# WasmRuntimePool — Singleton, hält Runtimes für alle aktiven Plugins
# ---------------------------------------------------------------------------


class WasmRuntimePool:
    """
    Legacy WASM runtime pool — kept for reference only.
    Active runtime management is now handled by GatewayWasmRuntimePool
    inside PluginGateway.
    """

    def __init__(self) -> None:
        self._runtimes: dict[str, PluginRuntime] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._host_functions: Any = NullHostFunctions()

    def set_host_functions(self, host_functions: Any) -> None:
        """Set the HostFunctions implementation for all future runtimes."""
        self._host_functions = host_functions
        logger.info("✅ WasmRuntimePool: HostFunctions set (%s)", type(host_functions).__name__)

    async def get_or_start_runtime(
        self,
        plugin_id: str,
        nfs_wasm_path: Path,
        initial_state: dict[str, Any] | None = None,
    ) -> PluginRuntime:
        """
        Return the runtime for a plugin, starting it lazily if needed.

        :param plugin_id:      Plugin ID (e.g. 'counter-widget')
        :param nfs_wasm_path:  Absolute NFS path to logic.wasm (from plugin_pool DB)
        :param initial_state:  Optional initial state for the WASM init call
        """
        if plugin_id not in self._locks:
            self._locks[plugin_id] = asyncio.Lock()

        async with self._locks[plugin_id]:
            if plugin_id not in self._runtimes:
                rt = create_plugin_runtime(
                    plugin_id=plugin_id,
                    wasm_path=nfs_wasm_path,
                    host=self._host_functions,
                    initial_state=initial_state,
                )
                await rt.start()
                self._runtimes[plugin_id] = rt
                logger.info(
                    "✅ WasmRuntimePool: '%s' started as %s",
                    plugin_id,
                    type(rt).__name__,
                )

        return self._runtimes[plugin_id]

    async def call(
        self,
        plugin_id: str,
        function_name: str,
        data: dict[str, Any] | None = None,
        nfs_wasm_path: Path | None = None,
        initial_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Call a plugin function.

        If the runtime is not yet running and ``nfs_wasm_path`` is given,
        it is started lazily.  Otherwise the runtime must already be active.
        """
        if plugin_id in self._runtimes:
            rt = self._runtimes[plugin_id]
        elif nfs_wasm_path is not None:
            rt = await self.get_or_start_runtime(plugin_id, nfs_wasm_path, initial_state)
        else:
            raise PluginCallError(
                plugin_id,
                function_name,
                f"Plugin '{plugin_id}' not active and no nfs_wasm_path provided",
            )

        return await rt.call(function_name, data or {})

    async def shutdown(self) -> None:
        """Stop all running runtimes."""
        for plugin_id, rt in list(self._runtimes.items()):
            await rt.stop()
            logger.info("🕑 WasmRuntimePool: '%s' stopped", plugin_id)
        self._runtimes.clear()
