"""
PluginGateway — Central hub for WASM plugins.

Provides three tightly coupled components:

PluginEventSystem
    Asyncio-based in-process pub/sub and request/reply bus.
    publish() bridges to the PluginBridge so that plugin events
    are automatically forwarded to connected browser clients via WebSocket.

LinkerFactory
    Reads ``.module/plugin_interfaces.yaml`` and registers host functions
    on a wasmtime Linker.  Type inference maps Python signatures to WASM
    ValType automatically; explicit ``wasm_types`` in the YAML take
    precedence.

PluginGateway
    Lifecycle owner: initialised from container_injection (no entity
    argument), registered as a TaskManager task in main.py.
    Owns a GatewayWasmRuntimePool and wires everything together.

    Bidirectional:
    - **Provider** — registers a ``plugin/ui_function_call`` Vyra Transport Remote
      Service so that other modules (and RemoteRuntimeProxy instances
      running in other modules) can call local WASM plugins.
    - **Consumer** — holds a Vyra Transport client to ``plugin/resolve_plugins``
      (self-call to <module_name>/PluginManager) and writes the result
      to ``plugin/cache/plugin_manifest.json``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from vyra_base.com import TransportProviderFactory, remote_service
from .. import container_injection
from ..interface import register_endpoint_callbacks

if TYPE_CHECKING:
    from vyra_base.core import VyraEntity

logger = logging.getLogger(__name__)

_INSTANCE_SUFFIX_RE = re.compile(r"_[0-9a-fA-F]{32}$")

# ---------------------------------------------------------------------------
# Optional wasmtime dependency
# ---------------------------------------------------------------------------
try:
    from wasmtime import (  # type: ignore[import]
        Engine,
        Store,
        Module as WasmModule,
        Linker,
        FuncType,
        ValType,
    )

    _WASMTIME_AVAILABLE = True
except ImportError:
    _WASMTIME_AVAILABLE = False

# Path to plugin_interfaces.yaml relative to the installed package root
_INTERFACES_YAML = Path(__file__).parent.parent.parent.parent / ".module" / "plugin_interfaces.yaml"


# ---------------------------------------------------------------------------
# PluginEventSystem
# ---------------------------------------------------------------------------


class PluginEventSystem:
    """
    Asyncio in-process event bus for plugins.

    publish(topic, payload)
        Puts the event on all subscriber queues *and* bridges it to the
        WebSocket FeedManager so browser clients receive it too.

    subscribe(topic) -> asyncio.Queue
        Returns a queue that will receive every future publish for topic.

    request(topic, payload, timeout) -> dict
        Sends a request and waits for exactly one reply via a temporary
        reply queue.  Raises asyncio.TimeoutError on timeout.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    async def publish(self, topic: str, payload: Any) -> None:
        """Publish an event to all local subscribers and to the WS feed."""
        queues = self._subscribers.get(topic, [])
        for q in queues:
            try:
                q.put_nowait({"topic": topic, "payload": payload})
            except asyncio.QueueFull:
                logger.warning("PluginEventSystem: queue full for topic=%s", topic)

        # Bridge to PluginBridge (best-effort) — imports lazily to avoid circular import
        try:
            from ..backend_webserver.services.plugin_bridge import PluginBridge

            PluginBridge.get_instance().publish_sync(topic, {"topic": topic, "payload": payload})
        except Exception as exc:
            logger.debug("PluginEventSystem: PluginBridge bridge error: %s", exc)

    def subscribe(self, topic: str, maxsize: int = 100) -> asyncio.Queue:
        """Subscribe to a topic and return the receive queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.setdefault(topic, []).append(q)
        return q

    def unsubscribe(self, topic: str, queue: asyncio.Queue) -> None:
        """Remove a previously subscribed queue."""
        queues = self._subscribers.get(topic, [])
        if queue in queues:
            queues.remove(queue)

    async def request(
        self,
        topic: str,
        payload: Any,
        timeout: float = 5.0,
    ) -> dict:
        """
        Publish a request and wait for a single reply.

        The expected reply topic is ``{topic}.reply``.
        Raises asyncio.TimeoutError if no reply arrives within *timeout* seconds.
        """
        reply_topic = f"{topic}.reply"
        reply_queue = self.subscribe(reply_topic, maxsize=1)
        try:
            await self.publish(topic, payload)
            return await asyncio.wait_for(reply_queue.get(), timeout=timeout)
        finally:
            self.unsubscribe(reply_topic, reply_queue)


# ---------------------------------------------------------------------------
# LinkerFactory
# ---------------------------------------------------------------------------


def _python_type_to_val_types(annotation: Any) -> list:
    """Infer a list of wasmtime ValType from a Python type annotation."""
    if not _WASMTIME_AVAILABLE:
        return []
    if annotation is str or annotation is bytes:
        # strings are passed as (ptr: i32, len: i32) pair
        return [ValType.i32(), ValType.i32()]
    if annotation is float:
        return [ValType.f32()]
    if annotation is None or annotation is type(None):
        return []
    # default: i32
    return [ValType.i32()]


def _yaml_type_to_val_type(name: str) -> Any:
    """Convert a YAML wasm_types string (e.g. 'i32') to a wasmtime ValType."""
    _map = {
        "i32": ValType.i32,
        "i64": ValType.i64,
        "f32": ValType.f32,
        "f64": ValType.f64,
    }
    factory = _map.get(name.lower())
    if factory is None:
        raise ValueError(f"Unknown WASM type: '{name}'")
    return factory()


class LinkerFactory:
    """
    Reads plugin_interfaces.yaml and builds a populated wasmtime Linker.

    Linker map entry format::

        linker_map:
          - namespace: "vyra"
            functions:
              - wasm_name: "log"
                host_func: "log"
              - wasm_name: "publish"
                host_func: "publish_event"
              - wasm_name: "request"
                host_func: "send_request"
                wasm_types:            # explicit override
                  params: ["i32", "i32", "i32", "i32"]
                  results: ["i32"]

    If ``wasm_types`` is omitted, the param/result types are inferred from
    the Python signature of the host function via inspect.
    """

    def __init__(self, gateway: "PluginGateway", interfaces_yaml: Path = _INTERFACES_YAML) -> None:
        self._gateway = gateway
        self._yaml_path = interfaces_yaml
        self._linker_map: list[dict] = []
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        if not self._yaml_path.exists():
            logger.warning(
                "LinkerFactory: plugin_interfaces.yaml not found at %s — no host functions registered",
                self._yaml_path,
            )
            self._linker_map = []
            self._loaded = True
            return
        with self._yaml_path.open() as fh:
            data = yaml.safe_load(fh)
        self._linker_map = data.get("linker_map", [])
        self._loaded = True

    def build_linker(self, engine: Any) -> Any:
        """
        Create and populate a wasmtime Linker for the given Engine.

        :param engine: wasmtime.Engine instance
        :returns:      Populated wasmtime.Linker
        :raises ImportError: if wasmtime is not installed
        """
        if not _WASMTIME_AVAILABLE:
            raise ImportError("wasmtime is not installed")
        self._load()
        linker = Linker(engine)
        for namespace_block in self._linker_map:
            ns = namespace_block.get("namespace", "vyra")
            for func_entry in namespace_block.get("functions", []):
                self._register_function(linker, ns, func_entry)
        return linker

    def _register_function(self, linker: Any, namespace: str, entry: dict) -> None:
        """Register a single host function on the linker."""
        wasm_name = entry.get("wasm_name", "")
        host_func_name = entry.get("host_func", wasm_name)

        host_fn = getattr(self._gateway, host_func_name, None)
        if host_fn is None:
            logger.warning(
                "LinkerFactory: PluginGateway has no method '%s' for wasm_name='%s' — skipping",
                host_func_name,
                wasm_name,
            )
            return

        # Determine WASM types
        wasm_types_override = entry.get("wasm_types")
        if wasm_types_override:
            param_types = [_yaml_type_to_val_type(t) for t in wasm_types_override.get("params", [])]
            result_types = [
                _yaml_type_to_val_type(t) for t in wasm_types_override.get("results", [])
            ]
        else:
            param_types, result_types = self._infer_types(host_fn)

        func_type = FuncType(param_types, result_types)

        # Build a synchronous wrapper (wasmtime callbacks must be sync)
        def _make_sync_wrapper(fn):
            if asyncio.iscoroutinefunction(fn):

                def _sync_wrapper(*args):
                    loop = asyncio.get_event_loop()
                    result = loop.run_until_complete(fn(*args))
                    return result
            else:

                def _sync_wrapper(*args):
                    return fn(*args)

            return _sync_wrapper

        sync_fn = _make_sync_wrapper(host_fn)

        try:
            linker.define_func(namespace, wasm_name, func_type, sync_fn)
            logger.debug(
                "LinkerFactory: registered %s::%s → gateway.%s",
                namespace,
                wasm_name,
                host_func_name,
            )
        except Exception as exc:
            logger.error(
                "LinkerFactory: failed to register %s::%s: %s",
                namespace,
                wasm_name,
                exc,
            )

    @staticmethod
    def _infer_types(fn: Any) -> tuple[list, list]:
        """Infer wasmtime param and result types from a Python function signature."""
        sig = inspect.signature(fn)
        param_types: list = []
        for name, param in sig.parameters.items():
            if name == "self":
                continue
            annotation = param.annotation
            if annotation is inspect.Parameter.empty:
                annotation = int  # default to i32
            param_types.extend(_python_type_to_val_types(annotation))

        ret = sig.return_annotation
        if ret is inspect.Parameter.empty or ret is None or ret is type(None):
            result_types: list = []
        else:
            result_types = _python_type_to_val_types(ret)

        return param_types, result_types


# ---------------------------------------------------------------------------
# PluginGateway
# ---------------------------------------------------------------------------


class PluginGateway:
    """
    Central WASM-plugin gateway.

    Responsibilities:
    - Own a PluginEventSystem for internal pub/sub + request/reply
    - Own a LinkerFactory that maps YAML-configured host functions to wasmtime
    - Provide host functions: log, publish_event, send_request
    - Manage WASM runtimes via GatewayWasmRuntimePool (local + RemoteRuntimeProxy)
    - Expose call_plugin() for use by the REST router
    - Provide ``plugin/ui_function_call`` Vyra Transport Remote Service (bidirectional provider)
    - Consume ``plugin/resolve_plugins`` via Vyra Transport self-call; cache result locally

    Lifecycle (main.py)::

        gateway = PluginGateway()
        gateway.setup()                          # reads entity from container_injection
        await gateway.register_endpoints()        # registers @remote_service handlers
        container_injection.set_plugin_gateway(gateway)
        taskmanager.add_task(plugin_gateway_runner)
    """

    def __init__(self) -> None:
        self.entity: "VyraEntity | None" = None
        self.event_system = PluginEventSystem()
        self._linker_factory: LinkerFactory | None = None
        self._runtime_pool: Any = None  # GatewayWasmRuntimePool, injected lazily
        self._resolve_client: Any = None  # Vyra Transport client → plugin/resolve_plugins
        self._get_nfs_path_client: Any = None  # Vyra Transport client → plugin/get_nfs_path
        self._own_module_name: str = ""
        self._own_module_id: str = ""
        self._manifest_cache: dict = {}  # In-memory manifest cache (avoids disk I/O)
        self._module_instance_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """
        Bind the gateway to the VyraEntity from container_injection.
        Creates LinkerFactory and GatewayWasmRuntimePool.
        """
        from .gateway_wasm_runtime import GatewayWasmRuntimePool

        self.entity = container_injection.get_entity()
        module_entry = getattr(self.entity, "module_entry", None)
        self._own_module_name = getattr(module_entry, "name", "")
        self._own_module_id = getattr(module_entry, "uuid", "") or ""
        self._linker_factory = LinkerFactory(gateway=self)
        self._runtime_pool = GatewayWasmRuntimePool(gateway=self)
        logger.info("✅ PluginGateway: setup complete (module=%s)", self._own_module_name)

    async def register_endpoints(self) -> None:
        """Register all @remote_service callbacks with the entity."""
        if self.entity is None:
            raise RuntimeError("PluginGateway.setup() must be called before register_endpoints()")
        register_endpoint_callbacks(self.entity, callback_parent=self)
        logger.info("✅ PluginGateway: endpoints registered")

    async def _setup_resolve_client(self) -> None:
        """
        Create the Vyra transport clients for plugin service calls.

        The target module is read from ``labels.modulemanager.module_id``
        in ``/workspace/.module/module_params.yaml``.
        Both ``module_name`` and ``module_id`` are passed to
        TransportProviderFactory so that a remote TopicBuilder is created,
        routing requests to the correct v2_modulemanager instance.
        """
        full_instance_name = self._read_modulemanager_id()
        # Split "v2_modulemanager_733256b82d6b48a48bc52b5ec73ebfff"
        # into module_name="v2_modulemanager" and module_id="733256b8..."
        parts = full_instance_name.rsplit("_", 1)
        if len(parts) == 2 and len(parts[1]) == 32:
            target_module_name = parts[0]
            target_module_id = parts[1]
        else:
            target_module_name = full_instance_name
            target_module_id = full_instance_name

        logger.debug(
            "PluginGateway: setting up resolve client for target module '%s' (id='%s')",
            target_module_name,
            target_module_id,
        )

        try:
            self._resolve_client = await TransportProviderFactory.create_client(
                name="resolve_plugins",
                module_name=target_module_name,
                module_id=target_module_id,
                namespace="plugin",
            )
            logger.info(
                "✅ PluginGateway: resolve_plugins client ready (module=%s)",
                full_instance_name or self._own_module_name,
            )
        except Exception as exc:
            logger.warning("⚠️  PluginGateway: resolve_plugins client failed: %s", exc)
            self._resolve_client = None

        try:
            self._get_nfs_path_client = await TransportProviderFactory.create_client(
                name="get_nfs_path",
                module_name=target_module_name,
                module_id=target_module_id,
                namespace="plugin",
            )
            logger.info(
                "✅ PluginGateway: get_nfs_path client ready (module=%s)",
                full_instance_name or self._own_module_name,
            )
        except Exception as exc:
            logger.warning("⚠️  PluginGateway: get_nfs_path client failed: %s", exc)
            self._get_nfs_path_client = None

    @staticmethod
    def _read_modulemanager_id() -> str:
        """
        Read ``labels.modulemanager.module_id`` from
        ``/workspace/.module/module_params.yaml``.

        :returns: The full module instance name of the managing v2_modulemanager,
              e.g. ``v2_modulemanager_733256b82d6b48a48bc52b5ec73ebfff``.
        :raises RuntimeError: If ``module_params.yaml`` is missing or
                      ``labels.modulemanager.module_id`` is not set.
        """
        params_path = Path("/workspace/.module/module_params.yaml")
        try:
            with params_path.open() as fh:
                data = yaml.safe_load(fh) or {}
            labels: dict = data.get("labels") or {}
            # Support nested format: labels.modulemanager.module_id
            modulemanager: dict = labels.get("modulemanager") or {}
            module_id: str | None = modulemanager.get("module_id") or None
            # Fallback: flat key format "modulemanager.module_id"
            if not module_id:
                module_id = labels.get("modulemanager.module_id") or None
            module_id = str(module_id).strip() if module_id is not None else ""
            if not module_id:
                raise RuntimeError(
                    "PluginGateway: required labels.modulemanager.module_id is missing in "
                    f"{params_path}. Module startup is invalid."
                )
            logger.debug("PluginGateway: resolved modulemanager.module_id=%s", module_id)
            return module_id
        except FileNotFoundError:
            raise RuntimeError(
                f"PluginGateway: required module params file not found at {params_path}. "
                "Module startup is invalid."
            )
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"PluginGateway: could not read module_params.yaml: {exc}") from exc

    async def run(self) -> None:
        """
        Long-running task registered with TaskManager.
        Sets up the resolve client, then keeps the gateway alive.
        """
        await self._setup_resolve_client()
        logger.info("✅ PluginGateway task running")
        while True:
            await asyncio.sleep(60)

    async def teardown(self) -> None:
        """Stop all running WASM runtimes and close Vyra transport clients."""
        if self._runtime_pool is not None:
            await self._runtime_pool.shutdown()
        if self._resolve_client is not None:
            try:
                await self._resolve_client.close()
            except Exception:
                pass
            self._resolve_client = None
        if self._get_nfs_path_client is not None:
            try:
                await self._get_nfs_path_client.close()
            except Exception:
                pass
            self._get_nfs_path_client = None
        logger.info("PluginGateway: shutdown complete")

    # ------------------------------------------------------------------
    # Manifest cache (consumer side)
    # ------------------------------------------------------------------

    async def resolve_plugins(
        self,
        scope_type: str = "MODULE",
        scope_target: str | None = None,
        module_name: str | None = None,
        module_id: str | None = None,
        p_id: str | None = None,
        request_source: str = "frontend",
    ) -> dict:
        """
        Call ``plugin/resolve_plugins`` via Vyra transport self-call, update the
        in-memory manifest cache, and return the result.

        :param scope_type:   GLOBAL | TEMPLATE | MODULE | INSTANCE
        :param scope_target: Scope target (default: own module name)
        :param module_name:  Requesting module name for slot-scope filtering.
        :param module_id:    Requesting module instance ID.
        :param p_id:         Optional direct filter on plugin pool entry ID.
        :param request_source: Request source (frontend/backend), forwarded to PluginManager.
        :returns:            The resolve_plugins response dict.
        """
        # --- Local fast-path ---------------------------------------------------
        # When this module IS the plugin host (i.e. plugin_manager is registered
        # in the ServiceRegistry), call _resolve_plugins_impl() directly instead
        # of making an unnecessary Zenoh round-trip to ourselves.
        local_pm = container_injection.get_service("plugin_manager")
        if local_pm is not None:
            try:
                result = await local_pm._resolve_plugins_impl(
                    scope_type_raw=scope_type,
                    scope_target=scope_target,
                    module_name=module_name or scope_target or self._own_module_name,
                    module_id=module_id or self._own_module_id,
                    p_id=p_id,
                    request_source=request_source,
                )
                self._manifest_cache = result
                return result
            except Exception as exc:
                logger.error("PluginGateway.resolve_plugins direct impl call failed: %s", exc)
                return self.get_manifest()

        # --- Remote path (consumer modules like v2_dashboard) ------------------
        # No local plugin_manager → call via Zenoh transport.
        if self._resolve_client is None:
            await self._setup_resolve_client()

        request: dict[str, Any] = {
            "scope_type": scope_type,
            "scope_target": scope_target or module_name or self._own_module_name,
            "module_name": module_name or self._own_module_name,
            "module_id": module_id or self._own_module_id,
            "p_id": p_id,
            "request_source": request_source,
        }

        logger.debug(
            f"PluginGateway.resolve_plugins: calling resolve_client with request: {request}"
        )

        result: dict = {}
        if self._resolve_client is not None:
            try:
                result = await self._resolve_client.call(request) or {}
            except Exception as exc:
                logger.error("PluginGateway.resolve_plugins Vyra transport call failed: %s", exc)
                return self.get_manifest() or self._empty_manifest(scope_type, scope_target)
        else:
            logger.warning("PluginGateway.resolve_plugins: no resolve_client available")
            return self.get_manifest() or self._empty_manifest(scope_type, scope_target)

        if not result or "ui_slots" not in result:
            logger.warning("PluginGateway.resolve_plugins: empty/invalid result from remote")
            cached = self.get_manifest()
            if cached and "ui_slots" in cached:
                return cached
            return self._empty_manifest(scope_type, scope_target)

        # Update in-memory cache
        self._manifest_cache = result
        return result

    def get_manifest(self) -> dict:
        """
        Return the in-memory cached plugin manifest without making a network call.

        :returns: Cached manifest dict, or empty dict if no cache has been loaded.
        """
        return self._manifest_cache

    @staticmethod
    def _empty_manifest(scope_type: str = "MODULE", scope_target: str | None = None) -> dict:
        """Return a valid but empty manifest response for the given scope."""
        return {
            "scope_type": scope_type,
            "scope_target": scope_target or "",
            "ui_slots": {},
        }

    # ------------------------------------------------------------------
    # Linker access
    # ------------------------------------------------------------------

    def get_linker(self, engine: Any) -> Any:
        """
        Build and return a populated wasmtime Linker for the given Engine.

        :param engine: wasmtime.Engine instance
        :returns:      Populated Linker with all host functions from plugin_interfaces.yaml
        """
        if self._linker_factory is None:
            raise RuntimeError("PluginGateway not set up — call setup() first")
        return self._linker_factory.build_linker(engine)

    # ------------------------------------------------------------------
    # Vyra transport Remote Service: ui_function_call  (provider side)
    # ------------------------------------------------------------------

    @remote_service(namespace="plugin")
    async def ui_function_call(self, request: dict, response: dict) -> dict:
        """
        Vyra transport Remote Service: ``plugin/ui_function_call``

        Allows other modules (and RemoteRuntimeProxy instances) to execute a
        WASM plugin function hosted in this module.

        Request parameters:
            plugin_id     (str)   — plugin name ID (e.g. 'counter-widget')
            function_name (str)   — exported WASM function name
            data          (dict)  — input parameters

        Response:
            plugin_id  (str)  — mirrored
            success    (bool)
            data       (dict) — result from WASM call
        """
        plugin_id: str = request.get("plugin_id", "")
        function_name: str = request.get("function_name", "")
        data: dict = request.get("data") or {}

        if not plugin_id or not function_name:
            return {
                "plugin_id": plugin_id,
                "success": False,
                "data": {},
                "error": "plugin_id and function_name are required",
            }

        try:
            result = await self.call_plugin(
                plugin_id=plugin_id, function_name=function_name, data=data
            )
            return {"plugin_id": plugin_id, "success": True, "data": result}
        except Exception as exc:
            logger.error(
                "PluginGateway.ui_function_call error [%s.%s]: %s", plugin_id, function_name, exc
            )
            return {"plugin_id": plugin_id, "success": False, "data": {}, "error": str(exc)}

    # ------------------------------------------------------------------
    # Host functions (called from WASM modules)
    # These signatures must match the wasm_types in plugin_interfaces.yaml
    # or be infer-able from their Python type annotations.
    # ------------------------------------------------------------------

    def log(self, msg: str, level: int = 20) -> None:
        """Host function: log a message from a WASM plugin."""
        log_fn = {
            10: logger.debug,
            20: logger.info,
            30: logger.warning,
            40: logger.error,
        }.get(level, logger.info)
        log_fn("[WASM] %s", msg)

    async def publish_event(self, topic: str, payload: str) -> None:
        """Host function: publish an event from a WASM plugin."""
        try:
            data = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            data = {"raw": payload}
        await self.event_system.publish(topic, data)

    async def send_request(self, topic: str, payload: str) -> str:
        """Host function: send a request from a WASM plugin and return the reply."""
        try:
            data = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            data = {"raw": payload}
        try:
            reply = await self.event_system.request(topic, data)
            return json.dumps(reply)
        except asyncio.TimeoutError:
            return json.dumps({"error": "timeout"})

    # ------------------------------------------------------------------
    # Plugin invocation (used by REST router and ui_function_call service)
    # ------------------------------------------------------------------

    async def call_plugin(
        self,
        plugin_id: str,
        function_name: str,
        data: dict,
        nfs_path: str | Path | None = None,
    ) -> dict:
        """
        Call a function on an installed WASM plugin.

        If ``nfs_path`` is not provided, the NFS path is looked up from the
        manifest cache (``get_manifest()``) or directly from the DB via
        ``PluginPool``.  Routing is transparent: local plugins use
        ``GatewayWasmRuntime``; remote plugins use ``RemoteRuntimeProxy``.

        :param plugin_id:     Plugin name ID (e.g. 'counter-widget')
        :param function_name: Exported WASM function name
        :param data:          Input key/value pairs
        :param nfs_path:      Optional NFS path override; looked up if omitted
        :returns:             Result dict from the runtime call
        """
        if self._runtime_pool is None:
            raise RuntimeError("PluginGateway not set up — call setup() first")

        def _find_resolved_plugin_entry(manifest: dict[str, Any]) -> dict[str, Any] | None:
            slots = manifest.get("ui_slots", {}) if isinstance(manifest, dict) else {}
            if not isinstance(slots, dict):
                return None
            for slot_entries in slots.values():
                if not isinstance(slot_entries, list):
                    continue
                for entry in slot_entries:
                    if isinstance(entry, dict) and entry.get("plugin_id") == plugin_id:
                        return entry
            return None

        def module_base_name(name: str | None) -> str:
            text = (name or "").strip()
            if not text:
                return ""
            return _INSTANCE_SUFFIX_RE.sub("", text)

        # Refresh manifest cache so scope-target routing works even when the
        # in-memory cache is cold.
        own_base = module_base_name(self._own_module_name) or self._own_module_name
        try:
            await self.resolve_plugins(scope_type="MODULE", scope_target=own_base)
        except Exception as exc:
            logger.debug("PluginGateway.call_plugin: resolve refresh failed: %s", exc)

        # MODULE resolve can miss INSTANCE-scoped cross-module plugins.
        # If the requested plugin is not present, refresh with INSTANCE scope.
        cached_manifest = self.get_manifest()
        if _find_resolved_plugin_entry(cached_manifest) is None:
            try:
                await self.resolve_plugins(
                    scope_type="INSTANCE",
                    scope_target=own_base,
                    module_name=own_base,
                    module_id=self._own_module_id,
                    request_source="frontend",
                )
            except Exception as exc:
                logger.debug("PluginGateway.call_plugin: instance resolve refresh failed: %s", exc)

        # Resolve cache from plugin/resolve_plugins (not plugin manifest.yaml).
        # ui_slots is optional and mainly used to derive nfs path hints.
        resolved_manifest = self.get_manifest()
        logger.debug(
            "PluginGateway.call_plugin: resolved manifest from cache: %s", resolved_manifest
        )
        ui_slots_raw = (
            resolved_manifest.get("ui_slots", {}) if isinstance(resolved_manifest, dict) else {}
        )
        ui_slots: dict[str, Any] = ui_slots_raw if isinstance(ui_slots_raw, dict) else {}
        plugin_metadata_raw = (
            resolved_manifest.get("plugin_metadata", [])
            if isinstance(resolved_manifest, dict)
            else []
        )
        plugin_metadata: list[dict[str, Any]] = (
            plugin_metadata_raw if isinstance(plugin_metadata_raw, list) else []
        )
        plugin_entry = _find_resolved_plugin_entry(resolved_manifest)
        hosting_module: str | None = None
        scope_target_module: str | None = None
        resolved_nfs_path: Path | None = Path(nfs_path) if nfs_path else None

        # Prefer concrete routing information from resolve_plugins ui slot entry.
        if plugin_entry is not None:
            hosting_module = plugin_entry.get("hosting_module_name")
            scope_target_module = (
                str(plugin_entry.get("communication_module_name") or "").strip()
                or str(plugin_entry.get("scope_target") or "").strip()
                or None
            )
            if resolved_nfs_path is None:
                nfs_js = str(plugin_entry.get("nfs_js_path") or "")
                if nfs_js:
                    resolved_nfs_path = Path(nfs_js).parent.parent

        # Fallback: read target from plugin metadata scope.
        for item in plugin_metadata:
            metadata = item.get("metadata_json") or {}
            item_plugin_id = item.get("plugin_name_id") or metadata.get("id")
            if item_plugin_id != plugin_id:
                continue
            scope = metadata.get("scope") or {}
            if str(scope.get("type", "")).upper() in {"MODULE", "INSTANCE"}:
                target = str(scope.get("target") or "").strip()
                if target and not scope_target_module:
                    scope_target_module = target
            break

        own_base = module_base_name(self._own_module_name)
        scope_target_base = module_base_name(scope_target_module)

        logger.debug(
            f"OWN_BASE: {own_base}, TARGET: {scope_target_module} (base={scope_target_base}), HOSTING_MODULE: {hosting_module}, RESOLVED NFS PATH: {resolved_nfs_path}"
        )

        remote_module_name: str | None = None
        if scope_target_module and scope_target_base != own_base:
            remote_module_name = scope_target_module

        # Fallback: if resolve cache is cold, read MODULE scope directly from
        # manifest.yaml before attempting local runtime startup.
        if not remote_module_name:
            logger.debug(
                "PluginGateway.call_plugin: no remote module target from resolve cache, checking manifest.yaml for MODULE scope"
            )
            if resolved_nfs_path is None:
                resolved_nfs_path = await self._lookup_nfs_path(plugin_id)
            manifest_file = resolved_nfs_path / "manifest.yaml"
            if manifest_file.exists():
                try:
                    with manifest_file.open() as handle:
                        plugin_manifest = yaml.safe_load(handle) or {}
                    scope = plugin_manifest.get("scope") or {}
                    if str(scope.get("type", "")).upper() in {"MODULE", "INSTANCE"}:
                        target = str(scope.get("target") or "").strip()
                        if target and module_base_name(target) != own_base:
                            remote_module_name = target
                except Exception as exc:
                    logger.debug(
                        "PluginGateway.call_plugin: could not read scope target from %s: %s",
                        manifest_file,
                        exc,
                    )

        # Route: remote module → RemoteRuntimeProxy
        if remote_module_name:
            logger.debug("Call proxy for remote module '%s'", remote_module_name)
            return await self._runtime_pool.call(
                plugin_id=plugin_id,
                function_name=function_name,
                data=data,
                remote_module_name=remote_module_name,
            )

        # Local: resolve NFS path from DB if still unknown
        if resolved_nfs_path is None:
            resolved_nfs_path = await self._lookup_nfs_path(plugin_id)

        wasm_path = resolved_nfs_path / "logic.wasm"
        return await self._runtime_pool.call(
            plugin_id=plugin_id,
            function_name=function_name,
            data=data,
            nfs_wasm_path=wasm_path,
        )

    async def _lookup_nfs_path(self, plugin_id: str) -> Path:
        """
        Look up the NFS path for a plugin via the ``plugin/get_nfs_path`` Zenoh
        service exposed by ``<module_name>``.

        No direct database access is performed here.  The target
        ``<module_name>`` instance is determined by reading
        ``labels.<module_name>.module_id`` from ``module_params.yaml``.

        :raises RuntimeError: If the plugin is not found or the service is unavailable.
        """
        if self._get_nfs_path_client is None:
            await self._setup_resolve_client()

        if self._get_nfs_path_client is None:
            raise RuntimeError(
                f"PluginGateway: get_nfs_path client not available — "
                f"cannot resolve NFS path for '{plugin_id}'"
            )

        try:
            response = await self._get_nfs_path_client.call({"plugin_id": plugin_id})
        except Exception as exc:
            raise RuntimeError(
                f"PluginGateway: get_nfs_path call failed for '{plugin_id}': {exc}"
            ) from exc

        nfs_path_str = (response or {}).get("nfs_path", "")
        if not nfs_path_str:
            raise RuntimeError(f"Plugin '{plugin_id}' not found (get_nfs_path returned empty)")
        return Path(nfs_path_str)
