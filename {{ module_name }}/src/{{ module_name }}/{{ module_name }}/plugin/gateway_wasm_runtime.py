"""
GatewayWasmRuntime — WasmRuntime subclass that uses the PluginGateway linker.

GatewayWasmRuntime
    Subclasses WasmRuntime and overrides start() to inject the linker
    produced by PluginGateway.get_linker() instead of a bare Linker(engine).
    Also reads ``manifest.yaml`` (with fallback to ``metadata.json``) for
    export metadata.

RemoteRuntimeProxy
    Implements the same PluginRuntime interface as GatewayWasmRuntime, but
    routes all calls to a remote module's ``ui_function_call`` Zenoh service
    instead of executing WASM locally.  The transport mechanism is an
    implementation detail — could be Zenoh, HTTP, or any other supported
    transport in the future.  GatewayWasmRuntimePool selects local vs remote
    transparently.

GatewayWasmRuntimePool
    Same interface as the legacy WasmRuntimePool but creates
    GatewayWasmRuntime or RemoteRuntimeProxy instances.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from vyra_base.plugin.runtime import PluginRuntime, PluginCallError

import yaml

if TYPE_CHECKING:
    from .plugin_gateway import PluginGateway

logger = logging.getLogger(__name__)

_INSTANCE_NAME_RE = re.compile(r"^(?P<name>.+)_(?P<id>[0-9a-fA-F]{32})$")

try:
    from wasmtime import Store, Module as WasmModule, Engine  # type: ignore[import]
    _WASMTIME_AVAILABLE = True
except ImportError:
    _WASMTIME_AVAILABLE = False


# ---------------------------------------------------------------------------
# GatewayWasmRuntime
# ---------------------------------------------------------------------------

class GatewayWasmRuntime(PluginRuntime):
    """
    WASM runtime that uses the PluginGateway to build the wasmtime Linker.

    Instead of using a bare ``Linker(engine)``, this class requests a
    pre-populated linker from the gateway so that all host functions
    declared in ``plugin_interfaces.yaml`` are available to the WASM module.

    Export metadata is read from ``manifest.yaml``; ``metadata.json`` is
    accepted as a fallback for backwards compatibility.
    """

    def __init__(
        self,
        plugin_id: str,
        wasm_path: str | Path,
        gateway: "PluginGateway",
        initial_state: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(plugin_id, str(wasm_path), host=None)
        self._gateway = gateway
        self._initial_state = initial_state.copy() if initial_state else {}
        self._store: Any = None
        self._instance: Any = None
        self._exports: dict[str, Any] = {}
        self._exports_meta: dict[str, list[dict[str, str]]] = {}
        self._service_exports: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            logger.warning("[%s] GatewayWasmRuntime already started", self.plugin_id)
            return

        wasm_path = Path(self.wasm_path)

        # --- load export metadata (manifest.yaml preferred, metadata.json fallback) ---
        self._load_export_meta(wasm_path.parent)

        if wasm_path.exists():
            if not _WASMTIME_AVAILABLE:
                raise ImportError(
                    "wasmtime is not installed. Install it with: pip install wasmtime"
                )

            # --- load WASM module with gateway linker ---
            engine = Engine()
            self._store = Store(engine)
            wasm_module = WasmModule(engine, wasm_path.read_bytes())

            # Use gateway linker (pre-populated with host functions)
            linker = self._gateway.get_linker(engine)
            self._instance = linker.instantiate(self._store, wasm_module)

            # Cache known exports
            exports_obj = self._instance.exports(self._store)
            for fn_name in self._exports_meta:
                fn = exports_obj.get(fn_name)
                if fn is not None:
                    self._exports[fn_name] = fn
                else:
                    logger.warning(
                        "[%s] WASM does not export '%s' (declared in manifest)",
                        self.plugin_id, fn_name,
                    )
        elif not self._service_exports:
            raise FileNotFoundError(
                f"[{self.plugin_id}] WASM file not found: {wasm_path}"
            )
        else:
            logger.info(
                "[%s] starting in service-export mode (no local logic.wasm)",
                self.plugin_id,
            )

        self._started = True
        logger.info(
            "✅ [%s] GatewayWasmRuntime started | exports=%s | size=%s bytes",
            self.plugin_id,
            list(self._exports.keys()) + list(self._service_exports.keys()),
            wasm_path.stat().st_size if wasm_path.exists() else 0,
        )

        if self._initial_state and ("init" in self._exports or "init" in self._service_exports):
            await self.call("init", self._initial_state)

    async def stop(self) -> None:
        self._started = False
        self._instance = None
        self._exports = {}
        self._exports_meta = {}
        self._service_exports = {}
        logger.info("🛑 [%s] GatewayWasmRuntime stopped", self.plugin_id)

    async def call(self, function_name: str, data: dict[str, Any]) -> dict[str, Any]:
        if not self._started:
            raise PluginCallError(self.plugin_id, function_name, "Runtime not started")
        return await self._dispatch_wasm(function_name, data)

    async def on_event(self, event_name: str, data: dict[str, Any]) -> None:
        logger.debug("[%s] on_event(%s)", self.plugin_id, event_name)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_export_meta(self, plugin_dir: Path) -> None:
        """Load export metadata from manifest.yaml or metadata.json."""
        manifest_path = plugin_dir / "manifest.yaml"
        meta_path = plugin_dir / "metadata.json"

        if manifest_path.exists():
            try:
                with manifest_path.open() as fh:
                    data = yaml.safe_load(fh)
                exports = (
                    data.get("entry_points", {})
                        .get("backend", {})
                        .get("exports", [])
                )
                for export in exports:
                    fn_name = export.get("name", "")
                    if fn_name:
                        self._exports_meta[fn_name] = export.get("args", [])

                service_exports = (
                    data.get("entry_points", {})
                        .get("backend", {})
                        .get("service_exports", [])
                )
                for export in service_exports:
                    fn_name = export.get("name", "")
                    if fn_name:
                        self._service_exports[fn_name] = export
                logger.info(
                    "📋 [%s] manifest.yaml loaded | wasm_exports=%s | service_exports=%s",
                    self.plugin_id,
                    list(self._exports_meta.keys()),
                    list(self._service_exports.keys()),
                )
                return
            except Exception as exc:
                logger.warning("[%s] manifest.yaml not readable: %s", self.plugin_id, exc)

        if meta_path.exists():
            try:
                data = json.loads(meta_path.read_text())
                for export in data.get("exports", []):
                    fn_name = export.get("name", "")
                    if fn_name:
                        self._exports_meta[fn_name] = export.get("args", [])
                logger.info(
                    "📋 [%s] metadata.json loaded (fallback) | exports=%s",
                    self.plugin_id, list(self._exports_meta.keys()),
                )
                return
            except Exception as exc:
                logger.warning("[%s] metadata.json not readable: %s", self.plugin_id, exc)

        logger.warning(
            "[%s] No manifest.yaml or metadata.json found in %s",
            self.plugin_id, plugin_dir,
        )

    async def _dispatch_wasm(
        self, function_name: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Map data keys to i32 arguments and call the WASM function."""
        if function_name == "ping":
            return {"status": "ok", "plugin_id": self.plugin_id, "runtime": "gateway_wasm"}

        if function_name in self._service_exports:
            return await self._dispatch_service_export(function_name, data)

        if function_name not in self._exports_meta:
            raise PluginCallError(
                self.plugin_id, function_name,
                f"Unknown function '{function_name}'. "
                f"Declared exports: {list(self._exports_meta.keys())}"
            )

        fn = self._exports.get(function_name)
        if fn is None:
            raise PluginCallError(
                self.plugin_id, function_name,
                f"WASM function '{function_name}' not available"
            )

        arg_defs = self._exports_meta[function_name]
        args: list[int] = [int(data.get(a.get("name", ""), 0)) for a in arg_defs]

        raw_result = fn(self._store, *args)

        if isinstance(raw_result, int):
            return {"result": raw_result}
        if isinstance(raw_result, (list, tuple)):
            return {"result": list(raw_result)}
        if raw_result is None:
            return {}
        return {"result": raw_result}

    async def _dispatch_service_export(
        self,
        function_name: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Dispatch a manifest-declared service export via ServiceRegistry."""
        export = self._service_exports.get(function_name) or {}
        service_name = str(export.get("service") or "").strip()
        method_name = str(export.get("method") or "").strip()
        passthrough = export.get("passthrough") or []
        static_kwargs = export.get("kwargs") or {}

        if not service_name or not method_name:
            raise PluginCallError(
                self.plugin_id,
                function_name,
                "Invalid service export config: 'service' and 'method' are required",
            )

        from .. import container_injection

        service = container_injection.get_service(service_name)
        if service is None:
            raise PluginCallError(
                self.plugin_id,
                function_name,
                f"Service '{service_name}' is not registered",
            )

        method = getattr(service, method_name, None)
        if method is None or not callable(method):
            raise PluginCallError(
                self.plugin_id,
                function_name,
                f"Service '{service_name}' has no callable method '{method_name}'",
            )

        kwargs = dict(static_kwargs)
        if passthrough == "*":
            kwargs.update(data)
        elif isinstance(passthrough, list):
            for key in passthrough:
                if key in data:
                    kwargs[key] = data[key]

        result = method(**kwargs)
        if inspect.isawaitable(result):
            result = await result

        if isinstance(result, dict):
            return result
        return {"result": result}


# ---------------------------------------------------------------------------
# GatewayWasmRuntimePool
# ---------------------------------------------------------------------------

class GatewayWasmRuntimePool:
    """
    Manages GatewayWasmRuntime and RemoteRuntimeProxy instances — one per plugin_id.

    Drop-in replacement for the legacy WasmRuntimePool.  Creates
    GatewayWasmRuntime for local plugins and RemoteRuntimeProxy for external
    ones.  Both types share the same call() interface so callers don't need
    to distinguish between them.
    """

    def __init__(self, gateway: "PluginGateway") -> None:
        self._gateway = gateway
        self._runtimes: dict[str, Union[GatewayWasmRuntime, RemoteRuntimeProxy]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_or_start_runtime(
        self,
        plugin_id: str,
        nfs_wasm_path: Path,
        initial_state: dict[str, Any] | None = None,
    ) -> GatewayWasmRuntime:
        """Return (and lazily start) the local WASM runtime for a plugin."""
        if plugin_id not in self._locks:
            self._locks[plugin_id] = asyncio.Lock()

        async with self._locks[plugin_id]:
            rt = self._runtimes.get(plugin_id)
            if rt is not None:
                if not isinstance(rt, GatewayWasmRuntime):
                    raise PluginCallError(
                        plugin_id, "<start>",
                        f"Plugin '{plugin_id}' is mapped to a RemoteRuntimeProxy, "
                        "not a local GatewayWasmRuntime",
                    )
                return rt
            new_rt = GatewayWasmRuntime(
                plugin_id=plugin_id,
                wasm_path=nfs_wasm_path,
                gateway=self._gateway,
                initial_state=initial_state,
            )
            await new_rt.start()
            self._runtimes[plugin_id] = new_rt
            logger.info("✅ GatewayWasmRuntimePool: '%s' started (local)", plugin_id)

        return self._runtimes[plugin_id]  # type: ignore[return-value]

    async def get_or_create_proxy(
        self,
        plugin_id: str,
        module_name: str,
    ) -> "RemoteRuntimeProxy":
        """Return (and lazily start) a RemoteRuntimeProxy for a remote module."""
        if plugin_id not in self._locks:
            self._locks[plugin_id] = asyncio.Lock()

        async with self._locks[plugin_id]:
            rt = self._runtimes.get(plugin_id)
            if rt is not None:
                if not isinstance(rt, RemoteRuntimeProxy):
                    raise PluginCallError(
                        plugin_id, "<proxy>",
                        f"Plugin '{plugin_id}' is already mapped to a local GatewayWasmRuntime",
                    )
                return rt
            proxy = RemoteRuntimeProxy(plugin_id=plugin_id, module_name=module_name)
            await proxy.start()
            self._runtimes[plugin_id] = proxy  # type: ignore[assignment]
            logger.info(
                "✅ GatewayWasmRuntimePool: '%s' started (remote → %s)",
                plugin_id, module_name,
            )

        return self._runtimes[plugin_id]  # type: ignore[return-value]

    async def call(
        self,
        plugin_id: str,
        function_name: str,
        data: dict[str, Any] | None = None,
        nfs_wasm_path: Path | None = None,
        initial_state: dict[str, Any] | None = None,
        remote_module_name: str | None = None,
    ) -> dict[str, Any]:
        """
        Call a plugin function, lazily starting the correct runtime if needed.

        - ``remote_module_name`` set → uses RemoteRuntimeProxy (no nfs_wasm_path needed).
        - ``nfs_wasm_path`` set      → uses local GatewayWasmRuntime.
        - Neither set, plugin already active → uses existing runtime (local or proxy).
        """
        rt = self._runtimes.get(plugin_id)
        if rt is not None:
            return await rt.call(function_name, data or {})

        if remote_module_name is not None:
            rt = await self.get_or_create_proxy(plugin_id, remote_module_name)
        elif nfs_wasm_path is not None:
            rt = await self.get_or_start_runtime(plugin_id, nfs_wasm_path, initial_state)
        else:
            raise PluginCallError(
                plugin_id, function_name,
                f"Plugin '{plugin_id}' not active and neither nfs_wasm_path nor "
                "remote_module_name was provided",
            )
        return await rt.call(function_name, data or {})

    async def stop_runtime(self, plugin_id: str) -> None:
        """Stop a specific plugin runtime."""
        rt = self._runtimes.pop(plugin_id, None)
        if rt:
            await rt.stop()

    async def shutdown(self) -> None:
        """Stop all running runtimes."""
        for plugin_id, rt in list(self._runtimes.items()):
            await rt.stop()
            logger.info("🛑 GatewayWasmRuntimePool: '%s' stopped", plugin_id)
        self._runtimes.clear()


# ---------------------------------------------------------------------------
# RemoteRuntimeProxy
# ---------------------------------------------------------------------------

class RemoteRuntimeProxy(PluginRuntime):
    """
    Drop-in replacement for GatewayWasmRuntime that routes calls to a remote
    module's ``ui_function_call`` service instead of executing WASM locally.

    The transport is an implementation detail (currently Zenoh via
    TransportProviderFactory).  ``GatewayWasmRuntimePool`` selects a local
    ``GatewayWasmRuntime`` or a ``RemoteRuntimeProxy`` transparently — the
    caller always sees the same ``start / stop / call / on_event`` interface.

    :param plugin_id:    Plugin name ID (e.g. 'counter-widget').
    :param module_name:  Name of the remote module that hosts the WASM runtime
                         (e.g. 'v2_modulemanager').
    """

    def __init__(self, plugin_id: str, module_name: str) -> None:
        super().__init__(plugin_id, wasm_path="", host=None)
        self._module_name = module_name
        self._client: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Create the Zenoh client for the remote ``ui_function_call`` service."""
        if self._started:
            return
        try:
            from vyra_base.com.core.factory import TransportProviderFactory  # type: ignore[import]

            target_name = (self._module_name or "").strip()
            target_module_name = target_name
            target_module_id: str | None = None

            match = _INSTANCE_NAME_RE.match(target_name)
            if match:
                target_module_name = match.group("name")
                target_module_id = match.group("id")
            elif target_name:
                target_module_id = target_name

            self._client = await TransportProviderFactory.create_client(
                name="ui_function_call",
                module_name=target_module_name,
                module_id=target_module_id,
                namespace="plugin",
            )
            self._started = True
            logger.info(
                "✅ RemoteRuntimeProxy [%s] → %s started",
                self.plugin_id,
                target_name,
            )
        except Exception as exc:
            logger.warning(
                "⚠️  RemoteRuntimeProxy [%s] client could not be started: %s",
                self.plugin_id, exc,
            )

    async def stop(self) -> None:
        """Close the remote client."""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None
        self._started = False
        logger.info("🛑 RemoteRuntimeProxy [%s] stopped", self.plugin_id)

    async def call(self, function_name: str, data: dict[str, Any]) -> dict[str, Any]:
        """Forward the call to the remote ``ui_function_call`` Zenoh service."""
        if not self._started or self._client is None:
            raise PluginCallError(
                self.plugin_id, function_name, "RemoteRuntimeProxy not started"
            )
        try:
            result: dict[str, Any] | None = await self._client.call({
                "plugin_id":     self.plugin_id,
                "function_name": function_name,
                "data":          data,
            })
            if result is None:
                raise PluginCallError(
                    self.plugin_id,
                    function_name,
                    "Remote call returned no response payload",
                )
            return result
        except Exception as exc:
            raise PluginCallError(
                self.plugin_id, function_name, f"Remote call failed: {exc}"
            ) from exc

    async def on_event(self, event_name: str, data: dict[str, Any]) -> None:
        """No-op: remote events are handled by the remote module."""
        logger.debug("RemoteRuntimeProxy [%s] on_event(%s) — not forwarded", self.plugin_id, event_name)
