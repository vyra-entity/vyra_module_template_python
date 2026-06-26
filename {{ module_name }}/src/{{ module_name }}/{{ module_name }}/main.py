import asyncio
import functools
import json
import os
import uvicorn
from pathlib import Path
import signal
import sys

# Initialize structured logging FIRST
from .logging_config import configure_logging, get_logger, log_exception, log_call
from vyra_base.build_info import log_build_info
from vyra_base.com.core.factory import TransportProviderFactory

# Configure logging before any other imports
configure_logging()
logger = get_logger(__name__)
log_build_info(logger)

# Now import ROS2 conditionally
VYRA_SLIM = os.getenv('VYRA_SLIM', 'false').lower() == 'true'
if not VYRA_SLIM:
    try:
        import rclpy  # pyright: ignore[reportMissingImports]
        logger.info("ros2_imported", slim_mode=False)
    except ImportError as e:
        logger.error("ros2_import_failed", error=str(e), slim_mode=False)
        raise
else:
    rclpy = None  # type: ignore
    logger.info("ros2_skipped", slim_mode=True, reason="VYRA_SLIM mode enabled")

from . import _base_
from .application import application
from .application.application import Component, auto_start_component, load_auto_start_enabled
from .taskmanager import TaskManager, task_supervisor_looper
from .state.state_manager import StateManager
from .user.usermanager import UserManager
from . import container_injection

from vyra_base.core.entity import VyraEntity
from vyra_base.helper.error_handler import ErrorTraceback 

logger.info(
    "module_mode_configured",
    slim_mode=VYRA_SLIM,
    mode="python_only" if VYRA_SLIM else "full_ros2",
    ros2_available=rclpy is not None
)

# ─────────────────────────────────────────────────────────────────────────────
# Graceful shutdown infrastructure
# Replaces synchronous signal.signal() handlers that could not perform async
# lifecycle state transitions (ShuttingDown → Offline) before exiting.
# ─────────────────────────────────────────────────────────────────────────────

class _ShutdownRefs:
    """Module-level mutable refs so the async signal handler can reach the
    statemanager and taskmanager after they are created in runner()."""
    statemanager: "StateManager | None" = None
    taskmanager: "TaskManager | None" = None
    exit_code: int = 0
    sig_name: str = ""
    shutdown_started: bool = False

_shutdown_refs = _ShutdownRefs()


async def _graceful_shutdown_async() -> None:
    """
    Async graceful shutdown — triggered by SIGTERM / SIGINT via asyncio-native
    signal handler (loop.add_signal_handler).

    Sequence:
    1. Transition lifecycle → ShuttingDown → Offline and broadcast state so
       that clients connected via Zenoh still see the final Offline state.
    2. Cancel all managed tasks → unblocks task_supervisor_looper (shutdown_event).
    3. Shut down ROS2 node/context.
    4. Store exit_code=143 so main() can propagate it to Docker Swarm/supervisord.
    """
    if _shutdown_refs.shutdown_started:
        logger.debug("graceful_shutdown_already_in_progress")
        return
    _shutdown_refs.shutdown_started = True
    sig_name = _shutdown_refs.sig_name or "unknown"

    logger.warning(
        "graceful_shutdown_started",
        signal=sig_name,
        slim_mode=VYRA_SLIM,
        pid=os.getpid(),
    )

    # 1. Lifecycle → ShuttingDown → Offline + broadcast
    if _shutdown_refs.statemanager:
        try:
            await _shutdown_refs.statemanager.shutdown_to_offline(
                reason=f"signal:{sig_name}"
            )
            logger.info("lifecycle_offline_broadcasted")
        except Exception as e:
            log_exception(logger, e, context={"operation": "shutdown_to_offline"})

    # 2. Cancel all managed tasks
    if _shutdown_refs.taskmanager:
        try:
            await _shutdown_refs.taskmanager.cancel_all()
            logger.info("all_tasks_cancelled_via_shutdown")
        except Exception as e:
            log_exception(logger, e, context={"operation": "cancel_all_shutdown"})

    # 3. Shutdown transport providers (Zenoh queryables, sessions, …)
    try:
        await TransportProviderFactory.shutdown_all()
        logger.info("transport_providers_shutdown_complete")
    except Exception as e:
        log_exception(logger, e, context={"operation": "transport_shutdown"})

    # 4. Shutdown ROS2
    if not VYRA_SLIM and rclpy and rclpy.ok():
        try:
            logger.info("shutting_down_ros2", reason=sig_name)
            rclpy.shutdown()
            logger.info("ros2_shutdown_complete")
        except Exception as e:
            log_exception(logger, e, context={"operation": "ros2_shutdown"})

    # 4. Store exit code — main() will call sys.exit() after asyncio.run() returns
    _shutdown_refs.exit_code = 143
    logger.info("graceful_shutdown_complete", exit_code=143)

@log_call
async def application_runner() -> None:
    """
    Main application logic runner.

    Runs component auto-start (when enabled) then keeps application.main() alive.
    Managed as an asyncio task by TaskManager.
    """
    taskmanager = container_injection.get_task_manager()
    component = container_injection.get_component()
    if load_auto_start_enabled():
        if not await auto_start_component(component):
            raise RuntimeError("Component auto-start failed — services not ready")
    else:
        logger.info("behavior.auto_start=false — component.initialize() deferred to manual trigger")
    runner_task = asyncio.create_task(application.main())
    while not runner_task.done():
        taskmanager.touch_heartbeat("application_runner")
        await asyncio.sleep(1.0)
    await runner_task
    ErrorTraceback.check_error_exist()

@log_call
async def web_backend_runner() -> None:
    """
    Start the Uvicorn server for the REST API.
    
    This task is only started if ENABLE_BACKEND_WEBSERVER=true in .env
    Waits for container initialization before starting the server.
    """
    logger.info("web_backend_initializing")
    
    auto_start = load_auto_start_enabled()
    wait_count = 0
    while not container_injection.is_initialized() or (
        auto_start and not await container_injection.all_services_ready()
    ):
        wait_count += 1
        inactive = await container_injection.get_inactive_services()
        logger.debug(
            "waiting_for_container_init",
            wait_count=wait_count,
            wait_time_seconds=wait_count * 0.5,
            auto_start=auto_start,
            container_ready=container_injection.is_initialized(),
            services_ready=await container_injection.all_services_ready(),
            inactive_services=inactive,
            registered_services=container_injection.list_registered_services(),
        )
        await asyncio.sleep(0.5)

        if auto_start and wait_count > 60:
            logger.error(
                "container_init_timeout",
                wait_count=wait_count,
                timeout_seconds=30,
                auto_start=auto_start,
                container_ready=container_injection.is_initialized(),
                inactive_services=inactive,
                registered_services=container_injection.list_registered_services(),
            )
            raise TimeoutError("Container initialization timeout after 30 seconds")
    
    logger.info("container_initialized", wait_count=wait_count)
    
    # Get module name dynamically from entity.
    # entity.module_entry.name is the short package name (e.g. "v2_modulemanager"),
    # which is also the top-level Python package installed by colcon.
    entity = container_injection.get_entity()
    module_name = entity.module_entry.name
    app_path = f"{module_name}.backend_webserver.asgi:application"
    
    logger.info(
        "web_backend_config",
        module_name=module_name,
        app_path=app_path
    )
    
    # Load backend webserver config file (analogous to nginx.conf for the frontend).
    # Config file controls SSL/TLS; Traefik terminates TLS externally so the backend
    # runs plain HTTP by default.
    webserver_config_path = "/workspace/config/backend_webserver.json"
    webserver_config: dict = {}
    if os.path.exists(webserver_config_path):
        try:
            with open(webserver_config_path, "r") as f:
                webserver_config = json.load(f)
            logger.info(
                "webserver_config_loaded",
                config_path=webserver_config_path,
                use_ssl=webserver_config.get("use_ssl", False)
            )
        except Exception as e:
            logger.warning(
                "webserver_config_load_failed",
                config_path=webserver_config_path,
                error=str(e)
            )
    else:
        logger.warning(
            "webserver_config_not_found",
            config_path=webserver_config_path,
            fallback="HTTP mode"
        )

    host = webserver_config.get("host", "0.0.0.0")
    port = int(webserver_config.get("port", 8443))
    use_ssl = bool(webserver_config.get("use_ssl", False))

    ssl_enabled = False
    if use_ssl:
        cert_path = "/workspace/storage/certificates/webserver.crt"
        key_path = "/workspace/storage/certificates/webserver.key"
        cert_exists = os.path.exists(cert_path) and os.path.exists(key_path)
        cert_readable = os.access(cert_path, os.R_OK) and os.access(key_path, os.R_OK)
        ssl_enabled = cert_exists and cert_readable
        if not cert_readable and cert_exists:
            logger.error(
                "uvicorn_ssl_permission_denied",
                reason="certificates_not_readable",
                cert_path=cert_path,
                key_path=key_path
            )
        elif not cert_exists:
            logger.error(
                "uvicorn_ssl_certs_missing",
                reason="certificates_not_found",
                expected_cert=cert_path,
                expected_key=key_path
            )

    if ssl_enabled:
        logger.info(
            "uvicorn_ssl_enabled",
            cert_path=cert_path,
            key_path=key_path
        )
        config = uvicorn.Config(
            app=app_path,
            host=host,
            port=port,
            log_level="info",
            log_config=None,
            ssl_certfile=cert_path,
            ssl_keyfile=key_path,
            reload=False
        )
    else:
        logger.info(
            "uvicorn_http_mode",
            reason="config_use_ssl_false" if not use_ssl else "ssl_certs_unavailable",
            host=host,
            port=port
        )
        config = uvicorn.Config(
            app=app_path,
            host=host,
            port=port,
            log_level="info",
            log_config=None,
            reload=False
        )
    
    logger.info(
        "uvicorn_starting",
        host=config.host,
        port=config.port,
        ssl_enabled=ssl_enabled
    )
    
    server = uvicorn.Server(config)
    await server.serve()

@log_call
async def initialize_module(taskmanager: TaskManager) -> tuple[VyraEntity, StateManager]:
    """
    Initialize VYRA entity and configure base settings.
    
    Sets up the module infrastructure including:
    - VyraEntity creation
    - StateManager initialization
    - Component creation and interface registration
    - Container dependency injection
    - Task scheduling (application, ROS2 spinner, web backend)
    
    Args:
        taskmanager: TaskManager instance to manage application tasks
        
    Returns:
        Tuple of (VyraEntity, StateManager)
        
    Raises:
        RuntimeError: If ROS2 node creation fails in non-SLIM mode
    """
    # Build base entity
    logger.debug("building_base_entity")
    entity: VyraEntity
    entity, pre_built_statemanager = await _base_.build_base()
    logger.info(
        "entity_created",
        module_name=entity.module_entry.name,
        node_name=entity.node.get_name() if entity.node else "None"
    )
    
    # Setup state manager
    logger.debug("setting_up_state_manager")
    statemanager: StateManager = await setup_statemanager(entity, pre_built_statemanager)
    logger.info("state_manager_ready")

    # Setup user manager
    logger.debug("setting_up_user_manager")
    user_manager: UserManager = await setup_usermanager(entity)
    logger.info("user_manager_ready")

    # Create Component (reused across task recoveries)
    logger.debug("creating_component")
    unified_state_machine = statemanager.state_machine
    component = Component(unified_state_machine, entity, taskmanager)

    logger.info(
        "component_created",
        component_type=type(component).__name__,
        state_machine=type(unified_state_machine).__name__
    )
    
    # Register remote callable interfaces
    logger.debug("registering_component_interfaces")
    await component.register_endpoints()
    logger.info("component_interfaces_registered")
    
    # Set instances in container_injection for web_backend access
    logger.debug("injecting_container_dependencies")
    container_injection.set_entity(entity)
    container_injection.set_component(component)
    container_injection.set_task_manager(taskmanager)
    container_injection.set_state_manager(statemanager)
    container_injection.set_user_manager(user_manager)
    logger.info("container_dependencies_injected")

    # Setup PluginGateway (WASM runtime hub + event system)
    logger.debug("setting_up_plugin_gateway")
    from .plugin.plugin_gateway import PluginGateway
    plugin_gateway = PluginGateway()
    plugin_gateway.setup()
    await plugin_gateway.register_endpoints()
    container_injection.set_plugin_gateway(plugin_gateway)
    logger.info("plugin_gateway_ready")

    # Setup PluginBridge (bidirectional Logic↔UI event channel)
    logger.debug("setting_up_plugin_bridge")
    from .backend_webserver.services.plugin_bridge import PluginBridge
    plugin_bridge = PluginBridge.get_instance()
    container_injection.set_plugin_bridge(plugin_bridge)
    logger.info("plugin_bridge_ready")


    await statemanager.initialization_complete()

    # Schedule application runner task
    logger.info("scheduling_application_runner_task")
    taskmanager.add_task(application_runner)
    taskmanager.add_task(plugin_gateway_runner)
    logger.debug("application_runner_task_scheduled")
    
    # ROS2-dependent tasks: only start if NOT in SLIM mode
    if not VYRA_SLIM:
        logger.info("scheduling_ros2_tasks", reason="non_slim_mode")
        
        # Validate ROS2 node was created
        if entity.node is None:
            logger.critical(
                "ros2_node_missing",
                entity_module=entity.module_entry.name,
                slim_mode=VYRA_SLIM
            )
            raise RuntimeError("No ROS 2 node created in non-SLIM mode")
        
        taskmanager.add_task(ros_spinner_runner, entity)
        logger.info(
            "ros2_spinner_task_scheduled",
            node_name=entity.node.get_name()
        )
    else:
        logger.info("skipping_ros2_tasks", reason="slim_mode_enabled")
    
    # Conditionally start web backend if ENABLE_BACKEND_WEBSERVER=true (works in both modes)
    enable_webserver = os.getenv('ENABLE_BACKEND_WEBSERVER', 'false').lower() == 'true'
    if enable_webserver:
        logger.info("scheduling_web_backend_task", reason="webserver_enabled")
        taskmanager.add_task(web_backend_runner)
        logger.debug("web_backend_task_scheduled")
    else:
        logger.info("skipping_web_backend_task", reason="webserver_disabled")
    
    task_count = len(taskmanager.tasks)
    task_names = list(taskmanager.tasks.keys())
    
    logger.info(
        "module_initialization_complete",
        task_count=task_count,
        task_names=task_names,
        slim_mode=VYRA_SLIM,
        webserver_enabled=enable_webserver
    )
    
    return entity, statemanager

@ErrorTraceback.w_check_error_exist
async def runner() -> None:
    """
    Main async runner function.
    
    Orchestrates the entire module lifecycle:
    1. TaskManager creation
    2. ROS2 initialization (if not SLIM mode)
    3. Module initialization
    4. Task supervision loop
    5. Graceful cleanup on shutdown
    """
    logger.info(
        "runner_started",
        slim_mode=VYRA_SLIM,
        pid=os.getpid()
    )
    
    taskmanager = None
    entity = None
    statemanager = None

    try:
        # ── Create TaskManager ─────────────────────────────────────────────
        logger.debug("Creating TaskManager")
        taskmanager = TaskManager()
        logger.info("task_manager_created", taskmanager_id=id(taskmanager))

        # ── Shutdown event shared between signal handler & supervisor loop ─
        shutdown_event = asyncio.Event()
        _shutdown_refs.taskmanager = taskmanager

        # ── Register asyncio-native signal handlers ────────────────────────
        # Using loop.add_signal_handler() instead of signal.signal() so that
        # the handler can schedule _graceful_shutdown_async as an asyncio task,
        # enabling proper lifecycle state transitions before the process exits.
        loop = asyncio.get_running_loop()
        for _sig in (signal.SIGTERM, signal.SIGINT):
            _sig_name = "SIGTERM" if _sig == signal.SIGTERM else "SIGINT"
            def _make_handler(name: str):
                def _handler():
                    if not _shutdown_refs.shutdown_started:
                        _shutdown_refs.sig_name = name
                        shutdown_event.set()
                        loop.create_task(_graceful_shutdown_async())
                return _handler
            loop.add_signal_handler(_sig, _make_handler(_sig_name))
        logger.debug("asyncio_signal_handlers_registered")

        # ── Only initialize ROS2 if NOT in SLIM mode ───────────────────────
        if not VYRA_SLIM:
            if rclpy is None:
                logger.critical("rclpy_not_imported", mode="full")
                raise RuntimeError("ROS2 not available in non-SLIM mode")
            
            logger.info("initializing_rclpy", mode="full")
            try:
                rclpy.init()
                logger.info("rclpy_initialized")
            except Exception as e:
                log_exception(logger, e, context={"operation": "rclpy_init"})
                raise
        else:
            logger.info("skipping_rclpy_init", mode="slim")

        # ── Initialize module ──────────────────────────────────────────────
        logger.info("initializing_module")
        entity, statemanager = await initialize_module(taskmanager)
        _shutdown_refs.statemanager = statemanager  # expose to shutdown handler

        task_count = len(taskmanager.tasks)
        task_names = list(taskmanager.tasks.keys())
        logger.info(
            "module_initialized",
            task_count=task_count,
            task_names=task_names,
            module_name=entity.module_entry.name if entity else "unknown"
        )

        # ── Start task supervision loop ────────────────────────────────────
        logger.info("starting_task_supervisor")
        await task_supervisor_looper(
            taskmanager,
            statemanager,
            supervisor_config=statemanager.task_supervisor_config,
            shutdown_event=shutdown_event,
        )
        logger.info("task_supervisor_completed")

    except SystemExit as e:
        logger.info(
            "system_exit_received",
            exit_code=e.code,
            slim_mode=VYRA_SLIM
        )
        if taskmanager:
            logger.debug("cancelling_all_tasks", reason="system_exit")
            await taskmanager.cancel_all()
            
    except KeyboardInterrupt:
        logger.warning(
            "keyboard_interrupt_received",
            pid=os.getpid()
        )
        if taskmanager:
            logger.debug("cancelling_all_tasks", reason="keyboard_interrupt")
            await taskmanager.cancel_all()
            
    except Exception as e:
        log_exception(
            logger,
            e,
            context={
                "function": "runner",
                "slim_mode": VYRA_SLIM,
                "has_entity": entity is not None,
                "has_taskmanager": taskmanager is not None
            }
        )
        if taskmanager:
            logger.debug("cancelling_all_tasks", reason="exception")
            await taskmanager.cancel_all()
            
    finally:
        # Cleanup ROS2 resources if in normal mode
        logger.info("cleanup_started", slim_mode=VYRA_SLIM)
        
        if not VYRA_SLIM:
            if entity and hasattr(entity, 'node') and entity.node is not None:
                try:
                    logger.debug(
                        "destroying_ros2_node",
                        node_name=entity.node.get_name()
                    )
                    entity.node.destroy_node()
                    logger.info("ros2_node_destroyed")
                except Exception as e:
                    log_exception(logger, e, context={"operation": "destroy_ros2_node"})
            else:
                logger.debug("no_ros2_node_to_destroy")

            if rclpy and rclpy.ok():
                try:
                    logger.debug("shutting_down_rclpy")
                    rclpy.shutdown()
                    logger.info("rclpy_shutdown_complete")
                except Exception as e:
                    log_exception(logger, e, context={"operation": "rclpy_shutdown"})
            else:
                logger.debug("rclpy_not_running")
        else:
            logger.debug("skipping_ros2_cleanup", reason="slim_mode")

        try:
            await TransportProviderFactory.shutdown_all()
            logger.info("transport_providers_shutdown_complete")
        except Exception as e:
            log_exception(logger, e, context={"operation": "transport_shutdown"})
            
        if taskmanager:
            logger.debug("final_task_cancellation")
            await taskmanager.cancel_all()
            logger.info("all_tasks_cancelled")
        
        logger.info("cleanup_complete")


def main() -> None:
    """
    Main entry point for the module.
    
    Configures the async event loop and starts the runner.
    Handles top-level exceptions and ensures proper cleanup.
    """
    logger.info(
        "main_entry_point",
        pid=os.getpid(),
        python_version=sys.version.split()[0],
        slim_mode=VYRA_SLIM
    )
    
    try:
        logger.debug("starting_asyncio_runner")
        asyncio.run(runner())
        # Propagate exit code requested by graceful shutdown (e.g. 143 for SIGTERM)
        if _shutdown_refs.exit_code != 0:
            logger.info(
                "exiting_with_shutdown_code",
                exit_code=_shutdown_refs.exit_code,
            )
            sys.exit(_shutdown_refs.exit_code)
        logger.info("module_exited_normally")
        
    except KeyboardInterrupt:
        logger.warning("main_keyboard_interrupt")
        
    except RuntimeError as e:
        logger.error(
            "main_runtime_error",
            error=str(e),
            error_type=type(e).__name__
        )
        
    except Exception as e:
        log_exception(
            logger,
            e,
            context={"function": "main"}
        )
        
    finally:
        logger.info("main_exit_complete")


if __name__ == '__main__':
    logger.info(
        "module_direct_execution",
        file=__file__,
        pid=os.getpid()
    )
    main()
