"""
Global Dependency Injection Container for V.Y.R.A. Modules

This module provides a global container for sharing VyraEntity and Component
instances across different parts of the application (ROS2 core, REST API, etc.)
without requiring gRPC over UDS communication.

Uses the dependency_injector framework for professional DI management.

Architecture:
    ApplicationContainer  —  fixe Kernobjekte (entity, component, task_manager, ...)
    ServiceRegistry       —  dynamische, modulspezifische Dienste (plugin_manager, ...)

    ServiceRegistry ermöglicht das Hinzufügen neuer Dienste zur Laufzeit ohne
    Änderungen am ApplicationContainer. Geeignet für optionale, swappable Dienste.

Usage:
    # In main.py nach Initialisierung:
    from .container_injection import container
    container.entity.set(entity)
    container.component.set(component)

    # ServiceRegistry — dynamische Dienste registrieren/abrufen:
    from .container_injection import register_service, require_service, get_service

    register_service("plugin_manager", plugin_manager_instance)
    pm = require_service("plugin_manager")   # wirft ContainerNotInitializedError
    pm = get_service("plugin_manager")       # gibt None zurück wenn nicht vorhanden

    # In backend_webserver via FastAPI Depends:
    from .container_injection import provide_service
    plugin_manager = Depends(provide_service("plugin_manager"))
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Optional

from dependency_injector import containers, providers

from .logging_config import get_logger, log_exception, log_function_call, log_function_result

if TYPE_CHECKING:
    from vyra_base.core import VyraEntity
    # Only needed for type annotations — kept here to avoid circular imports at runtime.
    # (application/__init__.py → application.py → container_injection.py → application/)
    from .application import Component
    from .taskmanager import TaskManager
    from .state.state_manager import StateManager
    from .user.usermanager import UserManager
    from .plugin.plugin_gateway import PluginGateway
    from .backend_webserver.services.plugin_bridge import PluginBridge

logger = get_logger(__name__)


class ContainerNotInitializedError(Exception):
    """Raised when trying to access container before initialization"""
    pass


# ---------------------------------------------------------------------------
# ServiceRegistry — dynamische, modulspezifische Dienste
# ---------------------------------------------------------------------------

class ServiceRegistry:
    """
    Registry für modulspezifische, optionale Dienste.

    Ermöglicht das dynamische Registrieren und Deregistrieren von Diensten die
    nicht zum fixen Kern (entity, component, task_manager, etc.) gehören, aber
    dennoch über container_injection erreichbar sein sollen.

    Geeignet für: plugin_manager, custom_manager, experiment_manager, ...

    Neue Dienste hinzufügen (Beispiel in application/application.py)::

        from ..container_injection import register_service, unregister_service

        # In initialize():
        register_service("plugin_manager", plugin_manager_instance)

        # In stop():
        unregister_service("plugin_manager")

    Abrufen in Backend-Routern (via FastAPI Depends)::

        from fastapi import Depends
        from ...container_injection import provide_service

        @router.get("/endpoint")
        async def my_handler(pm = Depends(provide_service("plugin_manager"))):
            ...

    Direkter Zugriff::

        pm = require_service("plugin_manager")  # wirft ContainerNotInitializedError
        pm = get_service("plugin_manager")       # gibt None zurück
    """

    def __init__(self) -> None:
        self._services: dict[str, Any] = {}

    def register(self, name: str, instance: Any) -> None:
        """Registriert einen Dienst unter dem angegebenen Namen."""
        self._services[name] = instance
        logger.info("✅ ServiceRegistry: '%s' registered", name)

    def unregister(self, name: str) -> None:
        """Entfernt einen Dienst aus der Registry."""
        if name in self._services:
            del self._services[name]
            logger.info("⏹️  ServiceRegistry: '%s' unregistered", name)

    def get(self, name: str) -> Any | None:
        """Gibt den Dienst zurück oder None wenn nicht vorhanden."""
        return self._services.get(name)

    def require(self, name: str) -> Any:
        """
        Gibt den Dienst zurück oder wirft ContainerNotInitializedError.

        Verwende diese Methode in Backend-Routern um einen 503-Fehler
        (wrong_state) auszulösen wenn der Dienst noch nicht bereit ist.
        """
        instance = self._services.get(name)
        if instance is None:
            raise ContainerNotInitializedError(
                f"Service '{name}' not registered. "
                f"Registered services: {list(self._services.keys())}"
            )
        return instance

    def has(self, name: str) -> bool:
        """Prüft ob ein Dienst registriert ist."""
        return name in self._services

    def list_services(self) -> list[str]:
        """Gibt eine Liste aller registrierten Dienstnamen zurück."""
        return list(self._services.keys())


# Global ServiceRegistry instance
_service_registry = ServiceRegistry()


def register_service(name: str, instance: Any) -> None:
    """Registriert einen modulspezifischen Dienst in der ServiceRegistry."""
    _service_registry.register(name, instance)


def unregister_service(name: str) -> None:
    """Entfernt einen modulspezifischen Dienst aus der ServiceRegistry."""
    _service_registry.unregister(name)


def get_service(name: str) -> Any | None:
    """Gibt einen Dienst zurück oder None."""
    return _service_registry.get(name)


def require_service(name: str) -> Any:
    """Gibt einen Dienst zurück oder wirft ContainerNotInitializedError."""
    return _service_registry.require(name)


def has_service(name: str) -> bool:
    """Prüft ob ein Dienst registriert ist."""
    return _service_registry.has(name)


def list_registered_services() -> list[str]:
    """Gibt alle registrierten Dienstnamen zurück."""
    return _service_registry.list_services()


SERVICE_WAIT_TIMEOUT_S = 30.0
SERVICE_POLL_INTERVAL_S = 0.5


async def _is_service_active(instance: Any) -> bool:
    """True when a registered service instance reports ready (or has no health hook).

    Only ``is_active`` and ``is_connected`` are used.  ``OperationalStateMachine.is_ready()``
    reflects operational FSM state (Idle→Ready), not registry readiness — checking it during
    ``initialize()`` would deadlock because the component is registered before READY is reached.
    """
    if instance is None:
        return False
    for attr in ("is_active", "is_connected"):
        checker = getattr(instance, attr, None)
        if callable(checker):
            result = checker()
            if asyncio.iscoroutine(result):
                result = await result
            return bool(result)
    return True


async def get_inactive_services() -> list[str]:
    """Service names that are missing or not yet active/connected."""
    inactive: list[str] = []
    for name in list_registered_services():
        instance = get_service(name)
        if not await _is_service_active(instance):
            inactive.append(name)
    return inactive


async def all_services_ready() -> bool:
    """True when ServiceRegistry is non-empty and every entry is active."""
    if not list_registered_services():
        return False
    return not await get_inactive_services()


async def wait_for_all_services(
    timeout_s: float = SERVICE_WAIT_TIMEOUT_S,
    poll_interval_s: float = SERVICE_POLL_INTERVAL_S,
) -> bool:
    """Poll until all registered services are active or timeout expires."""
    elapsed = 0.0
    while elapsed < timeout_s:
        if await all_services_ready():
            return True
        await asyncio.sleep(poll_interval_s)
        elapsed += poll_interval_s
    return False


def provide_service(name: str):
    """
    Factory für FastAPI Depends() — gibt einen Provider für einen benannten Dienst zurück.

    Beispiel::

        @router.get("/endpoint")
        async def handler(pm = Depends(provide_service("plugin_manager"))):
            ...
    """
    def _provider():
        return _service_registry.require(name)
    _provider.__name__ = f"provide_{name}"
    return _provider


# ---------------------------------------------------------------------------
# ApplicationContainer — fixe Kernobjekte
# ---------------------------------------------------------------------------

class ApplicationContainer(containers.DeclarativeContainer):
    """
    Dependency Injection Container für fixe Kernobjekte des V2 ModuleManagers.

    Provides singleton instances of core application components:
    - VyraEntity: ROS2 node and communication
    - Component: Application logic
    - TaskManager: Task management
    - StateManager: State broadcasting
    - UserManager: User management

    Modulspezifische, optionale Dienste (z.B. plugin_manager) werden über die
    ServiceRegistry verwaltet — nicht hier. Dies hält den Container schlank und
    ermöglicht das dynamische Hinzufügen neuer Dienste ohne Code-Änderungen.
    """
    
    # Configuration
    config = providers.Configuration()
    
    # Core components as Singleton providers
    entity: providers.Singleton[VyraEntity | None] = providers.Singleton(lambda: None)
    component: providers.Singleton[Component | None] = providers.Singleton(lambda: None)
    task_manager: providers.Singleton[TaskManager | None] = providers.Singleton(lambda: None)
    state_manager: providers.Singleton[StateManager | None] = providers.Singleton(lambda: None)
    user_manager: providers.Singleton[UserManager | None] = providers.Singleton(lambda: None)
    plugin_gateway: providers.Singleton[PluginGateway | None] = providers.Singleton(lambda: None)
    plugin_bridge: providers.Singleton[PluginBridge | None] = providers.Singleton(lambda: None)


# Global container instance
container = ApplicationContainer()


def set_entity(entity_instance) -> None:
    """
    Set the VyraEntity instance in the global container.
    
    Args:
        entity_instance: VyraEntity instance from core application
    """
    container.entity.override(providers.Object(entity_instance))
    logger.info("✅ VyraEntity set in container_injection")


def get_entity():
    """
    Get the VyraEntity instance from the global container.
    
    Returns:
        VyraEntity instance
        
    Raises:
        ContainerNotInitializedError: If entity has not been set yet
    """
    entity_instance = container.entity()
    if entity_instance is None:
        raise ContainerNotInitializedError(
            "VyraEntity not initialized in container. "
            "Make sure initialize_module() has been called."
        )
    return entity_instance


def set_component(component_instance) -> None:
    """
    Set the Component instance in the global container.
    
    Args:
        component_instance: Component instance from application
    """
    container.component.override(providers.Object(component_instance))
    logger.info("✅ Component set in container_injection")


def get_component() -> Component:
    """
    Get the Component instance from the global container.
    
    Returns:
        Component instance
        
    Raises:
        ContainerNotInitializedError: If component has not been set yet
    """
    component_instance = container.component()
    if component_instance is None:
        raise ContainerNotInitializedError(
            "Component not initialized in container. "
            "Make sure initialize_module() has been called."
        )
    return component_instance


def set_task_manager(task_manager_instance) -> None:
    """
    Set the TaskManager instance in the global container.
    
    Args:
        task_manager_instance: TaskManager instance
    """
    container.task_manager.override(providers.Object(task_manager_instance))
    logger.info("✅ TaskManager set in container_injection")


def get_task_manager() -> TaskManager:
    """
    Get the TaskManager instance from the global container.
    
    Returns:
        TaskManager instance
        
    Raises:
        ContainerNotInitializedError: If task_manager has not been set yet
    """
    task_manager_instance = container.task_manager()
    if task_manager_instance is None:
        raise ContainerNotInitializedError(
            "TaskManager not initialized in container. "
            "Make sure runner() has been called."
        )
    return task_manager_instance


def set_state_manager(state_manager_instance) -> None:
    """
    Set the StateManager instance in the global container.
    
    Args:
        state_manager_instance: StateManager instance
    """
    container.state_manager.override(providers.Object(state_manager_instance))
    logger.info("✅ StateManager set in container_injection")


def get_state_manager() -> StateManager:
    """
    Get the StateManager instance from the global container.
    
    Returns:
        StateManager instance
        
    Raises:
        ContainerNotInitializedError: If state_manager has not been set yet
    """
    state_manager_instance = container.state_manager()
    if state_manager_instance is None:
        raise ContainerNotInitializedError(
            "StateManager not initialized in container. "
            "Make sure initialize_module() has been called."
        )
    return state_manager_instance


def is_initialized() -> bool:
    """
    Check if the container has been initialized with all required components.
    
    Returns:
        True if entity, component, task_manager, state_manager, and user_manager are all set
    """
    try:
        for component_name in ['entity', 'component', 'task_manager', 'state_manager', 'user_manager']:
            if container.__getattribute__(component_name)() is None:
                logger.debug(f"Container component not initialized: {component_name}")
        return all([
            container.entity() is not None,
            container.component() is not None,
            container.task_manager() is not None,
            container.state_manager() is not None,
            container.user_manager() is not None
        ])
    except Exception:
        return False


def reset() -> None:
    """
    Reset the container (mainly for testing purposes).
    """
    container.entity.override(providers.Singleton(lambda: None))
    container.component.override(providers.Singleton(lambda: None))
    container.task_manager.override(providers.Singleton(lambda: None))
    container.state_manager.override(providers.Singleton(lambda: None))
    container.user_manager.override(providers.Singleton(lambda: None))
    container.plugin_gateway.override(providers.Singleton(lambda: None))
    container.plugin_bridge.override(providers.Singleton(lambda: None))
    # Clear all dynamically registered services
    for name in _service_registry.list_services():
        _service_registry.unregister(name)
    logger.info("🔄 Container reset")


# Convenience method for FastAPI Depends()
def provide_entity():
    """
    Provider function for FastAPI Depends().
    
    Usage:
        from fastapi import Depends
        
        @router.get("/endpoint")
        async def endpoint(entity = Depends(provide_entity)):
            ...
    """
    return get_entity()


def provide_component():
    """
    Provider function for FastAPI Depends().
    
    Usage:
        from fastapi import Depends
        
        @router.get("/endpoint")
        async def endpoint(component = Depends(provide_component)):
            ...
    """
    return get_component()


def provide_task_manager():
    """
    Provider function for FastAPI Depends().
    
    Usage:
        from fastapi import Depends
        
        @router.get("/endpoint")
        async def endpoint(task_manager = Depends(provide_task_manager)):
            ...
    """
    return get_task_manager()


def provide_state_manager():
    """
    Provider function for FastAPI Depends().
    
    Usage:
        from fastapi import Depends
        
        @router.get("/endpoint")
        async def endpoint(state_manager = Depends(provide_state_manager)):
            ...
    """
    return get_state_manager()


def set_user_manager(user_manager_instance: UserManager) -> None:
    """
    Set the UserManager instance in the global container.
    
    Args:
        user_manager_instance: UserManager instance
    """
    container.user_manager.override(providers.Object(user_manager_instance))
    logger.info("✅ UserManager set in container_injection")


def get_user_manager() -> Optional[UserManager]:
    """
    Get the UserManager instance from the global container.
    
    Returns:
        UserManager instance
        
    Raises:
        ContainerNotInitializedError: If user_manager has not been set yet
    """
    user_manager_instance = container.user_manager()
    if user_manager_instance is None:
        raise ContainerNotInitializedError(
            "UserManager not initialized in container. "
            "Make sure Component.initialize() has been called."
        )
    return user_manager_instance


def provide_user_manager():
    """
    Provider function for FastAPI Depends().
    
    Usage:
        from fastapi import Depends
        
        @router.get("/endpoint")
        async def endpoint(user_manager = Depends(provide_user_manager)):
            ...
    """
    return get_user_manager()


def set_plugin_manager(plugin_manager_instance) -> None:
    """Convenience-Wrapper — delegiert an register_service("plugin_manager")."""
    register_service("plugin_manager", plugin_manager_instance)


def get_plugin_manager() -> "PluginManager":
    """Convenience-Wrapper — delegiert an require_service("plugin_manager")."""
    return require_service("plugin_manager")


def provide_plugin_manager():
    """Provider function for FastAPI Depends() — delegiert an ServiceRegistry."""
    return require_service("plugin_manager")


def set_plugin_gateway(plugin_gateway_instance) -> None:
    container.plugin_gateway.override(providers.Object(plugin_gateway_instance))
    logger.info("✅ PluginGateway set in container_injection")


def get_plugin_gateway():
    instance = container.plugin_gateway()
    if instance is None:
        raise ContainerNotInitializedError(
            "PluginGateway not initialized in container."
        )
    return instance


def provide_plugin_gateway():
    """Provider function for FastAPI Depends()."""
    return get_plugin_gateway()


def set_plugin_bridge(plugin_bridge_instance) -> None:
    container.plugin_bridge.override(providers.Object(plugin_bridge_instance))
    logger.info("✅ PluginBridge set in container_injection")


def get_plugin_bridge():
    instance = container.plugin_bridge()
    if instance is None:
        raise ContainerNotInitializedError(
            "PluginBridge not initialized in container."
        )
    return instance


def provide_plugin_bridge():
    """Provider function for FastAPI Depends()."""
    return get_plugin_bridge()
