import asyncio
from datetime import datetime
import json
from ..logging_config import get_logger, log_exception
from typing import Any
import yaml

from pathlib import Path

from vyra_base import state
from vyra_base.state import (
    UnifiedStateMachine,
    OperationalStateMachine
)
from vyra_base.defaults.entries import StateEntry
from vyra_base.core.entity import VyraEntity
from vyra_base.com import remote_service

from ..taskmanager import TaskManager
from ..interface import register_endpoint_callbacks
from .. import container_injection

logger = get_logger(__name__)


class Component(OperationalStateMachine):
    """
    Base component class for {{ module_name }} application.
    
    Provides operational state management following industrial automation
    best practices with automatic state transitions.
    
    The Component uses the operational layer of the UnifiedStateMachine to manage
    its state (IDLE, READY, RUNNING, PAUSED, STOPPED, ERROR).
    
    User-defined lifecycle methods are prefixed with 'on_' (on_initialize, on_pause, etc.)
    and are automatically wrapped by the metaclass to handle state transitions.
    
    Public API methods are called without 'on_' prefix:
    - component.initialize()  -> calls on_initialize() with state management
    - component.pause()       -> calls on_pause() with state management
    - component.resume()      -> calls on_resume() with state management
    - component.stop()        -> calls on_stop() with state management
    - component.reset()       -> calls on_reset() with state management
    """
    
    def __init__(self, unified_state_machine: UnifiedStateMachine, entity: VyraEntity, task_manager: TaskManager):
        """
        Initialize the Component with unified state machine and entity.
        
        Args:
            unified_state_machine: The UnifiedStateMachine instance from StatusManager
            entity: The VyraEntity containing the ROS 2 node
            task_manager: The TaskManager instance to manage parallel application tasks
        """
        # Initialize parent OperationalStateMachine
        super().__init__(unified_state_machine.fsm)
        
        self.entity = entity
        self.task_manager = task_manager
        
        # Component instances
        # Define your instances here
        self._heartbeat_task: asyncio.Task | None = None
        self._state_heartbeat_task: asyncio.Task | None = None
        
        # Sub-manager instances (set during initialize())
        self.internal_usermanager = None
        self.usermanager_detector = None
    
    async def register_endpoints(self):
        """
        Setup @remote_service interfaces in VyraEntity.
        Automatically registers all methods decorated with @remote_service.
        """
        register_endpoint_callbacks(self.entity, callback_parent=self)
    
    @remote_service()
    async def initialize(self, request: Any=None, response: Any=None) -> bool:
        """
        Initialize the {{ module_name }} components.
        
        State Transition: IDLE -> READY
        On Success: {{ module_name }} fully initialized and ready for operations
        On Failure: IDLE -> ERROR
        
        Returns:
            bool: True on success, False on failure
        """
        try:           
            # Start periodic NewsFeed heartbeat (5 s interval)
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            logger.info("✅ NewsFeed heartbeat started (5 s interval)")

            # Start periodic StateFeed heartbeat (5 s interval)
            self._state_heartbeat_task = asyncio.create_task(self._state_heartbeat_loop())
            logger.info("✅ StateFeed heartbeat started (5 s interval)")

            logger.info("✅ Component initialization complete")
            return True
            
        except Exception as e:
            logger.exception(f"❌ Component initialization failed: {e}")
            return False

    async def _heartbeat_loop(self) -> None:
        """
        Publish a NewsFeed heartbeat every 5 seconds.

        Runs indefinitely as a background asyncio task started in initialize().
        Cancelled by stop() via self._heartbeat_task.cancel().
        """
        logger.info("💓 NewsFeed heartbeat loop started")
        while True:
            try:
                await self.entity.news_feeder.feed(f"heartbeat: {datetime.now().isoformat()}")
            except Exception as e:
                logger.warning(f"Heartbeat publish failed: {e}")
            await asyncio.sleep(5.0)

    async def _state_heartbeat_loop(self) -> None:
        """
        Publish a StateFeed heartbeat every 5 seconds.

        Runs indefinitely as a background asyncio task started in initialize().
        Cancelled by stop() via self._state_heartbeat_task.cancel().
        """
        logger.info("💓 StateFeed heartbeat loop started")
        while True:
            try:
                state_data = StateEntry(
                    previous="N/A",
                    trigger="heartbeat",
                    current=str(self._state_machine.get_operational_state().value),
                    module_id=self.entity.module_entry.uuid,
                    module_name=self.entity.module_entry.name,
                    timestamp=datetime.now()
                )
                await self.entity.state_feeder.feed(state_data)
            except Exception as e:
                logger.warning(f"StateFeed heartbeat publish failed: {e}")
            await asyncio.sleep(5.0)

    @remote_service()
    async def pause(self, request: Any=None, response: Any=None) -> bool:
        """
        Pause ongoing operations.
        
        State Transition: RUNNING -> PAUSED
        On Success: Operations temporarily suspended
        On Failure: RUNNING -> ERROR
        
        Returns:
            bool: True on success, False on failure
        """
        # TODO: Implement pause logic
        # - Suspend ongoing operations
        # - Save current state/checkpoints
        # - Release temporary resources
        logger.info("⏸️  Component pause requested")
        return True  # Placeholder for actual implementation
    
    @remote_service()
    async def resume(self, request: Any=None, response: Any=None) -> bool:
        """
        Resume from paused state.
        
        State Transition: PAUSED -> READY
        On Success: {{ module_name }} operations resumed, operation counter reset
        On Failure: PAUSED -> ERROR
        
        Returns:
            bool: True on success, False on failure
        """
        # TODO: Implement resume logic
        # - Restore saved state/checkpoints
        # - Re-acquire resources
        # - Resume suspended operations
        logger.info("▶️  Component resume requested")
        return True
    
    @remote_service()
    async def stop(self, request: Any=None, response: Any=None) -> bool:
        """
        Stop component operations cleanly.
        
        State Transition: RUNNING/PAUSED -> STOPPED
        On Success: {{ module_name }} clean shutdown completed
        On Failure: -> ERROR
        
        Returns:
            bool: True on success, False on failure
        """
        # TODO: Implement stop logic
        # - Stop gRPC servers
        # - Close database connections
        # - Finalize ongoing operations
        # - Release all resources
        logger.info("⏹️  Component stop requested")

        # Cancel heartbeat loops
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._state_heartbeat_task is not None and not self._state_heartbeat_task.done():
            self._state_heartbeat_task.cancel()
            logger.info("⏹️  Heartbeat loops cancelled")

        return True
    
    @remote_service()
    async def reset(self, request: Any=None, response: Any=None) -> bool:
        """
        Reset component to initial state.
        
        State Transition: STOPPED/ERROR -> IDLE
        On Success: Ready for re-initialization
        
        Returns:
            bool: True on success, False on failure
        """
        # TODO: Implement reset logic
        # - Clear all state
        # - Release remaining resources
        # - Reset to initial configuration
        logger.info("🔄 Component reset requested")
        
        return True
    
    # @remote_service()
    # @state.operation
    # async def template_test(self, request: Any=None, response: Any=None) -> dict:
    #     """
    #     Template test function demonstrating @remote_service + @state.operation.
        
    #     This function serves as a template showing how to combine both decorators:
    #     - @remote_service: Exposes method as ROS2 service
    #     - @state.operation: Automatic READY <-> RUNNING state management with reference counting
        
    #     State Flow: READY -> RUNNING (counter++) -> execute -> RUNNING (counter--) -> READY (if counter=0)
        
    #     Args:
    #         test_data: Optional test data dictionary
            
    #     Returns:
    #         dict: Test result with status and data
    #     """
    #     test_data = request.test_data if request and hasattr(request, 'test_data') else {}
        
    #     logger.info(f"🧪 Template test function called with data: {test_data}")
        
    #     # TODO: Implement actual test logic here
    #     # This is just a template demonstration
        
    #     logger.info(f"✅ Template test completed: {result['message']}")
    #     return result


async def main() -> None:
    """
    Main application entry point for {{ module_name }}.
    
    Loads configuration, initializes component, and manages lifecycle based on
    module_params.yaml configuration.
    
    All dependencies (task_manager, state_manager, component) are resolved via
    container_injection.
    """
    task_manager = container_injection.get_task_manager()
    status_manager = container_injection.get_state_manager()
    component = container_injection.get_component()
    logger.info("🚀 Starting {{ module_name }}...")
    
    if not component:
        logger.error("❌ Component not available from container injection")
        return
    
    # Load module configuration
    module_params_path = Path(".module/module_params.yaml")
    if not module_params_path.exists():
        logger.warning(f"⚠️  Module params not found at {module_params_path}, using defaults")
        auto_start = True  # Default to auto-start
    else:
        with open(module_params_path, "r") as f:
            module_params = yaml.safe_load(f)
            auto_start = module_params.get("behavior", {}).get("auto_start", True)
    
    logger.info(f"📋 Configuration: auto_start={auto_start}")
    
    # Auto-start if configured
    if auto_start:
        # Check current operational state and component initialization status
        current_state = component.get_operational_state()
        logger.info(f"🔍 Current operational state: {current_state}")

        if current_state.value == "Idle":
            logger.info("🔄 Initializing component via initialize() method...")
            
            success = await component.initialize()
            
            if not success:
                logger.error("❌ Component initialization failed")
                if component.is_error():
                    logger.error("💥 Component in ERROR state - manual reset required")
        else:
            # State is already READY but components not initialized (e.g., after recovery)
            # Initialize components directly without state transition
            logger.info(f"🔄 Component in state {current_state} but not initialized, initializing components directly...")
            try:
                # TODO: Implement initialization logic here if manually needed
                
                success = True
            except Exception as e:
                logger.exception(f"❌ Component initialization failed: {e}")
                success = False

        
        if success:
            # Setup async components after initialization
                        
            logger.info("✅ Application setup complete - service running")
    else:
        logger.info("⏸️  Auto-start disabled, waiting for manual initialization")

    # Keep service running indefinitely
    logger.info("♾️  Service running indefinitely...")

    while True:
        await asyncio.sleep(10)
