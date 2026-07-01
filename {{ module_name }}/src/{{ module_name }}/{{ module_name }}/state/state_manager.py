"""
State Manager

Manages the 3-layer state machine (Lifecycle, Operational, Health) and
exposes **read-only** Zenoh interfaces so that other modules can query the
current state without being able to mutate it.

Architecture
------------
* Runs as an async task embedded in core main.py.
* Uses the VyraEntity instance provided by the application for StateFeeder
  broadcasting and @remote_service interface registration.
* Internal state mutations are triggered only by the application itself
  (container_injection, taskmanager, etc.); they are **never** exposed
  via network interfaces.

Zenoh read interfaces (registered via @remote_service):
    get_state()              — Full 3-layer state snapshot (JSON).
    get_lifecycle_state()    — Current lifecycle state string.
    get_operational_state()  — Current operational state string.
    get_health_state()       — Current health state string.
    get_last_error_state()   — Last stored error context (JSON).
    get_state_summary()      — Human-readable status summary (JSON).
    get_state_history()      — Recent state-change history (JSON list).
"""

from __future__ import annotations

import asyncio
import logging
import yaml
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Optional

# NOTE: auto_register_interfaces is imported lazily inside setup_interfaces()
# to avoid pulling in ament_index_python / rclpy at module load time.
# This keeps state_manager testable without a full ROS2 environment.

from ..logging_config import get_logger, log_exception

from dataclasses import dataclass, field
from datetime import datetime as _dt
from typing import Callable, Awaitable

from vyra_base.com import remote_service, remote_actionServer
from vyra_base.state.state_types import (
    HealthState,
    LifecycleState,
    OperationalState,
)
from vyra_base.state.unified import UnifiedStateMachine
from vyra_base.defaults.entries import StateEntry
from vyra_base.state.lifecycle_guards import (
    RecoveryTimeoutTracker,
    assert_suspend_allowed,
    prepare_operational_for_recovery,
)
from vyra_base.lifecycle import TaskSupervisorConfig
from vyra_base.helper.error_handler import ErrorTraceback

# TYPE_CHECKING-only imports – not executed at runtime, so no rclpy/ament chain
if TYPE_CHECKING:
    from vyra_base.core.entity import VyraEntity

from .state_types import (
    LAYER_ACTIONS,
    StateHistoryEntry,
    StateRequest,
    StateResponse,
    ThreeLayerState,
    is_valid_action,
)

logger = get_logger(__name__)


@dataclass
class LifecycleCallbackEntry:
    """A registered suspend- or resume-phase callback with priority ordering."""

    priority: int
    name: str
    callback: Callable[[], Awaitable[None]]


# ─────────────────────────────────────────────────────────────────────────────
# Default configuration (overridden by module_state_meta.json when present)
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_CONFIG: Dict[str, Any] = {
    "broadcast_interval_hz": 1.0,
    "max_state_history_size": 50,
    "max_error_history_size": 10,
    "debug_transitions": False,
    "task_supervisor": {},
}


class StateManager:
    """
    State manager for the 3-layer state machine (Lifecycle / Operational / Health).

    Lifecycle (intended usage):
    1. Instantiate with the shared VyraEntity.
    2. Call ``await setup_interfaces()`` once the entity is ready.
    3. Schedule ``broadcast_state()`` at the configured interval.
    4. Delegate state mutations explicitly via ``execute_state_action()``.

    Other modules may query the current state via the registered Zenoh
    interfaces; they **cannot** change it through these interfaces.
    """

    def __init__(self, entity: VyraEntity) -> None:
        """
        Args:
            entity: The shared VyraEntity for this module.
        """
        self.entity = entity

        # ── Load StateManager configuration ──────────────────────────────────
        self._config: Dict[str, Any] = self._load_state_meta()
        _max_hist = int(self._config.get("max_state_history_size", 50))
        _max_err = int(self._config.get("max_error_history_size", 10))

        # ── Module identity ───────────────────────────────────────────────────
        module_data = self._load_module_data()
        self.module_name: str = module_data.get("name", "unknown_module")
        self.module_id: str = module_data.get("uuid", "")

        logger.info(f"📋 Module: {self.module_name}")
        logger.info(f"🆔 UUID:   {self.module_id}")

        # ── 3-layer state machine (shared with VyraEntity) ───────────────────
        self._state_machine: UnifiedStateMachine = entity.state_machine
        self._state_machine.on_any_change(self._on_state_change_template)
        self._state_machine.on_lifecycle_change(self._on_lifecycle_change)
        self._state_machine.on_operational_change(self._on_operational_change)
        self._state_machine.on_health_change(self._on_health_change)

        self._recovery_tracker = RecoveryTimeoutTracker(
            float(self._config.get("task_supervisor", {}).get("recovering_total_timeout_s", 120.0))
        )

        # ── History / diagnostics ─────────────────────────────────────────────
        self._state_history: Deque[StateHistoryEntry] = deque(maxlen=_max_hist)
        self._error_history: Deque[Dict[str, Any]] = deque(maxlen=_max_err)
        self._last_error_state: Optional[Dict[str, Any]] = None
        self._last_state: Optional[ThreeLayerState] = None

        # ── Lifecycle phase callbacks ──────────────────────────────────────────
        self._suspend_callbacks: List[LifecycleCallbackEntry] = []
        self._resume_callbacks: List[LifecycleCallbackEntry] = []

        logger.info(f"StateManager initialised for module: {self.module_name}")

    def _on_state_change_template(
        self, layer: str, old_state: str, new_state: str, *_: Any
    ) -> None:
        """Template callback for global state transitions (intentionally no-op)."""
        return None

    def _on_lifecycle_change(self, layer: str, old_state: str, new_state: str) -> None:
        """Callback for lifecycle state changes (intentionally no-op)."""
        return None

    def _on_operational_change(self, layer: str, old_state: str, new_state: str) -> None:
        """Callback for operational state changes (intentionally no-op)."""
        return None

    def _on_health_change(self, layer: str, old_state: str, new_state: str) -> None:
        """Callback for health state changes (intentionally no-op)."""
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Properties
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def state_machine(self) -> UnifiedStateMachine:
        """Direct access to the underlying UnifiedStateMachine."""
        return self._state_machine

    @property
    def task_supervisor_config(self) -> TaskSupervisorConfig:
        """Task supervisor configuration from module_params.yaml."""
        return TaskSupervisorConfig.from_dict(self._config.get("task_supervisor"))

    @property
    def broadcast_interval(self) -> float:
        """Broadcast interval in seconds derived from configured Hz."""
        hz = float(self._config.get("broadcast_interval_hz", 1.0))
        return 1.0 / hz if hz > 0 else 1.0

    # ─────────────────────────────────────────────────────────────────────────
    # Setup
    # ─────────────────────────────────────────────────────────────────────────

    def register_suspend_callback(
        self,
        callback: Callable[[], Awaitable[None]],
        priority: int = 10,
        name: str = "",
    ) -> None:
        """Register an async callback to be invoked during lifecycle suspend.

        Callbacks are executed in ascending priority order (lower number = first).
        Callbacks with the **same** priority are gathered concurrently; groups
        with different priorities are executed sequentially.  If any callback
        raises or times out (10 s), the lifecycle enters RECOVERING.

        Args:
            callback: Zero-argument async callable.
            priority: Execution order (lower = earlier, default 10).
            name:     Human-readable label for diagnostics.
        """
        self._suspend_callbacks.append(
            LifecycleCallbackEntry(
                priority=priority, name=name or callback.__name__, callback=callback
            )
        )
        self._suspend_callbacks.sort(key=lambda e: e.priority)
        logger.debug(f"📌 Registered suspend callback '{name}' (prio={priority})")

    def register_resume_callback(
        self,
        callback: Callable[[], Awaitable[None]],
        priority: int = 10,
        name: str = "",
    ) -> None:
        """Register an async callback to be invoked during lifecycle resume.

        See :meth:`register_suspend_callback` for execution semantics.
        """
        self._resume_callbacks.append(
            LifecycleCallbackEntry(
                priority=priority, name=name or callback.__name__, callback=callback
            )
        )
        self._resume_callbacks.sort(key=lambda e: e.priority)
        logger.debug(f"📌 Registered resume callback '{name}' (prio={priority})")

    async def _run_lifecycle_callbacks(
        self,
        entries: List[LifecycleCallbackEntry],
        phase: str,
        goal_handle: Any,
    ) -> bool:
        """Execute lifecycle callbacks in priority order.

        Callbacks with the same priority are run concurrently via
        ``asyncio.gather``; different-priority groups are run sequentially.
        Each individual callback has a 10-second timeout.  A failure in any
        callback will trigger a transition to RECOVERING.

        Args:
            entries:     Sorted list of :class:`LifecycleCallbackEntry`.
            phase:       ``'suspend'`` or ``'resume'`` (for logging/feedback).
            goal_handle: Action goal handle for publishing feedback.

        Returns:
            ``True`` if all callbacks succeeded, ``False`` on first failure.
        """
        if not entries:
            return True

        # Group entries by priority
        from itertools import groupby

        groups = [(prio, list(grp)) for prio, grp in groupby(entries, key=lambda e: e.priority)]

        total = len(entries)
        done = 0

        for prio, group in groups:
            names = ", ".join(e.name for e in group)
            logger.debug(f"⚙️  [{phase}] Running priority-{prio} callbacks: {names}")

            async def _run_one(entry: LifecycleCallbackEntry) -> None:
                await asyncio.wait_for(entry.callback(), timeout=10.0)

            try:
                await asyncio.gather(*[_run_one(e) for e in group])
                done += len(group)
                progress = int(done / total * 90)  # reserve last 10% for state transition
                goal_handle.publish_feedback(
                    {
                        "status": f"{phase}_callbacks_prio_{prio}_done",
                        "progress": progress,
                    }
                )
            except asyncio.TimeoutError:
                logger.error(f"❌ [{phase}] Timeout in priority-{prio} callbacks ({names})")
                try:
                    self.execute_state_action(
                        StateRequest(
                            layer="lifecycle",
                            action="enter_recovery",
                            metadata={"reason": f"{phase}_callback_timeout", "group": names},
                        )
                    )
                except Exception as exc2:
                    logger.error(f"enter_recovery failed: {exc2}")
                return False
            except Exception as exc:
                logger.error(f"❌ [{phase}] Error in priority-{prio} callbacks ({names}): {exc}")
                try:
                    self.execute_state_action(
                        StateRequest(
                            layer="lifecycle",
                            action="enter_recovery",
                            metadata={"reason": f"{phase}_callback_error", "error": str(exc)},
                        )
                    )
                except Exception as exc2:
                    logger.error(f"enter_recovery failed: {exc2}")
                return False

        return True

    async def register_endpoints(self) -> None:
        """Register all Zenoh @remote_service handlers with the VyraEntity."""
        if getattr(self, "_endpoints_registered", False):
            logger.debug("StateManager: endpoints already registered, skipping")
            return

        from ..interface import register_endpoint_callbacks  # lazy – needs ROS2 env

        register_endpoint_callbacks(self.entity, callback_parent=self)
        self._endpoints_registered = True
        logger.info("✅ StateManager Zenoh interfaces registered")

    async def initialization_start(self) -> bool:
        """
        Run the startup lifecycle sequence.

        Called by ``application.py`` when ``behavior.auto_start: true`` is set
        in ``module_params.yaml``.  The sequence is:

          1. OFFLINE → INITIALIZING  (``start``)
          2. INITIALIZING → ACTIVE   (``complete_initialization``)
          3. IDLE → READY            (``set_ready``)

        Returns:
            ``True`` on success, ``False`` if any step failed.
        """
        try:
            logger.info("🚀 Initialising module lifecycle…")

            prev = self.get_current_state()
            lifecycle = self._state_machine.get_lifecycle_state()

            if lifecycle == LifecycleState.OFFLINE:
                self._state_machine.start(metadata={"source": "application"})
                logger.info("  ✓ Lifecycle: INITIALIZING")
            else:
                logger.info(
                    "  ✓ Lifecycle already %s — skipping start()",
                    lifecycle.value,
                )

            current = self.get_current_state()
            self._record_history_diff(prev, current)

            logger.info("✅ Module successfully activated!")
            return True

        except Exception as exc:
            logger.error(f"❌ Failed to initialise module: {exc}")
            error_details: list[Any] = []
            ErrorTraceback.check_error_exist(error_details=error_details)
            self._record_error(str(exc), {"error_details": error_details})
            try:
                self._state_machine.report_fault(
                    fault_info={"error": str(exc), "details": error_details}
                )
            except Exception:
                pass
            return False

    async def initialization_complete(self) -> bool:
        """Complete the startup sequence (for manual startup)."""
        try:
            prev = self.get_current_state()
            lifecycle = self._state_machine.get_lifecycle_state()

            if lifecycle == LifecycleState.INITIALIZING:
                self._state_machine.complete_initialization(result={"container_ready": True})
            else:
                logger.info(
                    "  ✓ Lifecycle already %s — skipping complete_initialization()",
                    lifecycle.value,
                )

            current = self.get_current_state()
            self._record_history_diff(prev, current)
            return True

        except Exception as exc:
            logger.error(f"❌ Failed to initialise module: {exc}")
            error_details: list[Any] = []
            ErrorTraceback.check_error_exist(error_details=error_details)
            self._record_error(str(exc), {"error_details": error_details})

            try:
                self._state_machine.report_fault(
                    fault_info={"error": str(exc), "details": error_details}
                )
            except Exception:
                pass
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Config loading
    # ─────────────────────────────────────────────────────────────────────────

    def _load_state_meta(self) -> Dict[str, Any]:
        """
        Load StateManager configuration from ``module_params.yaml``.

        Reads the ``state_manager:`` section of
        ``/workspace/.module/module_params.yaml``.
        Falls back to :data:`_DEFAULT_CONFIG` if the file or section is absent.
        """
        params_path = Path("/workspace/.module/module_params.yaml")
        config: Dict[str, Any] = dict(_DEFAULT_CONFIG)

        try:
            if params_path.exists():
                with open(params_path, "r") as fh:
                    data = yaml.safe_load(fh) or {}
                sm_cfg = data.get("state_manager", {})
                if sm_cfg:
                    config.update(sm_cfg)
                    logger.debug(f"Loaded StateManager config from {params_path}: {sm_cfg}")
                else:
                    logger.debug(
                        "No 'state_manager' section in module_params.yaml, " "using defaults."
                    )
            else:
                logger.debug(f"module_params.yaml not found at {params_path}, " "using defaults.")
        except Exception as exc:
            logger.warning(f"⚠️ Could not load module_params.yaml: {exc}")

        return config

    def _load_module_data(self) -> Dict[str, Any]:
        """Load module identity from ``/workspace/.module/module_data.yaml``."""
        module_data_path = Path("/workspace/.module/module_data.yaml")
        try:
            if module_data_path.exists():
                with open(module_data_path, "r") as fh:
                    data = yaml.safe_load(fh)
                    return data or {}
        except Exception as exc:
            logger.error(f"❌ Failed to load module data: {exc}")
        return {}

    # ─────────────────────────────────────────────────────────────────────────
    # Core state accessors (internal)
    # ─────────────────────────────────────────────────────────────────────────

    def get_current_state(self) -> ThreeLayerState:
        """
        Build a :class:`ThreeLayerState` snapshot from the state machine.

        Returns:
            Current state with module metadata attached.
        """
        states = self._state_machine.get_all_states()
        return ThreeLayerState(
            lifecycle=states["lifecycle"],
            operational=states["operational"],
            health=states["health"],
            timestamp=datetime.now(),
            metadata={
                "module_name": self.module_name,
                "module_id": self.module_id,
                "is_operational": self._state_machine.is_operational(),
                "is_healthy": self._state_machine.is_healthy(),
            },
        )

    # Backward-compatibility alias
    def get_current_status(self) -> ThreeLayerState:
        """Alias for :meth:`get_current_state` (backward compatibility)."""
        return self.get_current_state()

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle action-server endpoints (suspend / resume)
    # ─────────────────────────────────────────────────────────────────────────

    @remote_actionServer.on_goal(name="request_lc_suspend")
    async def _on_goal_suspend(self, goal_request: Any) -> bool:
        """Accept suspend goal only if lifecycle is ACTIVE and operational is IDLE."""
        states = self._state_machine.get_all_states()
        if states["lifecycle"] != LifecycleState.ACTIVE.value:
            logger.warning(
                f"⚠️  Reject suspend goal – lifecycle is {states['lifecycle']}, not ACTIVE"
            )
            return False
        try:
            assert_suspend_allowed(
                self._state_machine,
                module_name=self.module_name,
                module_id=self.module_id,
            )
        except Exception as exc:
            logger.warning(f"⚠️  Reject suspend goal – {exc}")
            return False
        logger.info("✅ Suspend goal accepted")
        return True

    @remote_actionServer.on_cancel(name="request_lc_suspend")
    async def _on_cancel_suspend(self, goal_handle: Any) -> bool:
        """Accept cancel requests for the suspend action.

        The execute handler will notice the cancellation via
        ``goal_handle.is_cancel_requested()`` and abort cleanly.
        """
        logger.info("⚠️  Cancel requested for request_lc_suspend")
        return True

    @remote_actionServer.execute(name="request_lc_suspend")
    async def _execute_suspend(self, goal_handle: Any) -> Dict[str, Any]:
        """Execute the suspend lifecycle transition.

        1. Run all registered suspend callbacks (priority-ordered, 10 s timeout each).
        2. Transition lifecycle: ACTIVE → SUSPENDED.
        3. Publish 100 % feedback and return result.
        """
        logger.info("🔵 Suspend action started")
        goal_handle.publish_feedback({"status": "suspend_started", "progress": 0})

        try:
            # Run phase callbacks with 20 s total budget
            success = await asyncio.wait_for(
                self._run_lifecycle_callbacks(self._suspend_callbacks, "suspend", goal_handle),
                timeout=20.0,
            )
            if not success:
                goal_handle.abort()
                return {
                    "success": False,
                    "message": "Suspend aborted – callback failure",
                    "final_state": self._state_machine.get_all_states()["lifecycle"],
                }

            # Execute the state transition
            self.execute_state_action(
                StateRequest(
                    layer="lifecycle", action="suspend", metadata={"reason": "remote_request"}
                )
            )

            goal_handle.publish_feedback({"status": "suspended", "progress": 100})
            goal_handle.succeed()

            final = self._state_machine.get_all_states()["lifecycle"]
            logger.info(f"✅ Suspend complete – lifecycle={final}")
            return {"success": True, "message": "Module suspended", "final_state": final}

        except asyncio.TimeoutError:
            logger.error("❌ Suspend action timed out (20 s)")
            try:
                self.execute_state_action(
                    StateRequest(
                        layer="lifecycle",
                        action="enter_recovery",
                        metadata={"reason": "suspend_timeout"},
                    )
                )
            except Exception:
                pass
            goal_handle.abort()
            return {
                "success": False,
                "message": "Suspend timed out",
                "final_state": self._state_machine.get_all_states()["lifecycle"],
            }
        except Exception as exc:
            logger.error(f"❌ Suspend action error: {exc}")
            goal_handle.abort()
            return {
                "success": False,
                "message": str(exc),
                "final_state": self._state_machine.get_all_states()["lifecycle"],
            }

    @remote_actionServer.on_goal(name="request_lc_resume")
    async def _on_goal_resume(self, goal_request: Any) -> bool:
        """Accept resume goal only if lifecycle is currently SUSPENDED."""
        states = self._state_machine.get_all_states()
        if states["lifecycle"] != LifecycleState.SUSPENDED.value:
            logger.warning(
                f"⚠️  Reject resume goal – lifecycle is {states['lifecycle']}, not SUSPENDED"
            )
            return False
        logger.info("✅ Resume goal accepted")
        return True

    @remote_actionServer.on_cancel(name="request_lc_resume")
    async def _on_cancel_resume(self, goal_handle: Any) -> bool:
        """Accept cancel requests for the resume action.

        The execute handler will notice the cancellation via
        ``goal_handle.is_cancel_requested()`` and abort cleanly.
        """
        logger.info("⚠️  Cancel requested for request_lc_resume")
        return True

    @remote_actionServer.execute(name="request_lc_resume")
    async def _execute_resume(self, goal_handle: Any) -> Dict[str, Any]:
        """Execute the resume lifecycle transition.

        1. Run all registered resume callbacks (priority-ordered, 10 s timeout each).
        2. Transition lifecycle: SUSPENDED → RECOVERING → ACTIVE.
        3. Publish 100 % feedback and return result.
        """
        logger.info("🟢 Resume action started")
        goal_handle.publish_feedback({"status": "resume_started", "progress": 0})

        try:
            success = await asyncio.wait_for(
                self._run_lifecycle_callbacks(self._resume_callbacks, "resume", goal_handle),
                timeout=20.0,
            )
            if not success:
                goal_handle.abort()
                return {
                    "success": False,
                    "message": "Resume aborted – callback failure",
                    "final_state": self._state_machine.get_all_states()["lifecycle"],
                }

            # Transition: SUSPENDED → ACTIVE (via resume_from_suspend)
            self.execute_state_action(
                StateRequest(
                    layer="lifecycle",
                    action="resume_from_suspend",
                    metadata={"reason": "remote_request"},
                )
            )

            goal_handle.publish_feedback({"status": "resumed", "progress": 100})
            goal_handle.succeed()

            final = self._state_machine.get_all_states()["lifecycle"]
            logger.info(f"✅ Resume complete – lifecycle={final}")
            return {"success": True, "message": "Module resumed", "final_state": final}

        except asyncio.TimeoutError:
            logger.error("❌ Resume action timed out (20 s)")
            try:
                self.execute_state_action(
                    StateRequest(
                        layer="lifecycle",
                        action="enter_recovery",
                        metadata={"reason": "resume_timeout"},
                    )
                )
            except Exception:
                pass
            goal_handle.abort()
            return {
                "success": False,
                "message": "Resume timed out",
                "final_state": self._state_machine.get_all_states()["lifecycle"],
            }
        except Exception as exc:
            logger.error(f"❌ Resume action error: {exc}")
            goal_handle.abort()
            return {
                "success": False,
                "message": str(exc),
                "final_state": self._state_machine.get_all_states()["lifecycle"],
            }

    # ─────────────────────────────────────────────────────────────────────────
    # Read-only Zenoh interface methods (decorated with @remote_service)
    # Other modules can call these via Zenoh – they never mutate state.
    # ─────────────────────────────────────────────────────────────────────────

    @remote_service()
    async def get_state(self, request: Dict[str, Any], response: Any = None) -> Dict[str, Any]:
        """
        Return the full 3-layer state snapshot.

        Response fields:
            lifecycle  (str)  – Current lifecycle state value.
            operational (str) – Current operational state value.
            health     (str)  – Current health state value.
            timestamp  (str)  – ISO 8601 timestamp of the snapshot.
            metadata   (dict) – Module name, UUID and derived flags.
            success    (bool) – Always True on successful call.
        """
        try:
            state = self.get_current_state()
            result = state.to_dict()
            result["success"] = True
            return result
        except Exception as exc:
            logger.error(f"get_state failed: {exc}")
            return {"success": False, "error": str(exc)}

    @remote_service()
    async def get_lifecycle_state(
        self, request: Dict[str, Any], response: Any = None
    ) -> Dict[str, Any]:
        """
        Return only the lifecycle layer state.

        Response fields:
            lifecycle (str)  – Current lifecycle state value.
            timestamp (str)  – ISO 8601 timestamp.
            success   (bool) – Always True on successful call.
        """
        try:
            states = self._state_machine.get_all_states()
            return {
                "lifecycle": states["lifecycle"],
                "timestamp": datetime.now().isoformat(),
                "success": True,
            }
        except Exception as exc:
            logger.error(f"get_lifecycle_state failed: {exc}")
            return {"success": False, "error": str(exc)}

    @remote_service()
    async def get_operational_state(
        self, request: Dict[str, Any], response: Any = None
    ) -> Dict[str, Any]:
        """
        Return only the operational layer state.

        Response fields:
            operational (str) – Current operational state value.
            timestamp   (str) – ISO 8601 timestamp.
            success     (bool) – Always True on successful call.
        """
        try:
            states = self._state_machine.get_all_states()
            return {
                "operational": states["operational"],
                "timestamp": datetime.now().isoformat(),
                "success": True,
            }
        except Exception as exc:
            logger.error(f"get_operational_state failed: {exc}")
            return {"success": False, "error": str(exc)}

    @remote_service()
    async def get_health_state(
        self, request: Dict[str, Any], response: Any = None
    ) -> Dict[str, Any]:
        """
        Return only the health layer state.

        Response fields:
            health    (str)  – Current health state value.
            timestamp (str)  – ISO 8601 timestamp.
            success   (bool) – Always True on successful call.
        """
        try:
            states = self._state_machine.get_all_states()
            return {
                "health": states["health"],
                "timestamp": datetime.now().isoformat(),
                "success": True,
            }
        except Exception as exc:
            logger.error(f"get_health_state failed: {exc}")
            return {"success": False, "error": str(exc)}

    @remote_service()
    async def get_last_error_state(
        self, request: Dict[str, Any], response: Any = None
    ) -> Dict[str, Any]:
        """
        Return the last recorded error context.

        Response fields:
            has_error      (bool)  – False when no error has occurred yet.
            error_message  (str)   – Error message (empty string if none).
            error_details  (dict)  – Traceback / additional info.
            timestamp      (str)   – ISO 8601 time of the error.
            error_history  (list)  – Up to N recent error entries.
            success        (bool)  – Always True on successful call.
        """
        try:
            if self._last_error_state is not None:
                result = dict(self._last_error_state)
                result["has_error"] = True
                result["error_history"] = list(self._error_history)
            else:
                result = {
                    "has_error": False,
                    "error_message": "",
                    "error_details": {},
                    "timestamp": datetime.now().isoformat(),
                    "error_history": [],
                }
            result["success"] = True
            return result
        except Exception as exc:
            logger.error(f"get_last_error_state failed: {exc}")
            return {"success": False, "error": str(exc)}

    @remote_service()
    async def get_state_summary(
        self, request: Dict[str, Any], response: Any = None
    ) -> Dict[str, Any]:
        """
        Return a human-readable state summary.

        Response fields:
            summary        (str)   – One-line status description.
            is_operational (bool)  – True when lifecycle=ACTIVE and op≠ERROR.
            is_healthy     (bool)  – True when health=HEALTHY.
            lifecycle      (str)   – Lifecycle state value.
            operational    (str)   – Operational state value.
            health         (str)   – Health state value.
            module_name    (str)   – Module identifier.
            timestamp      (str)   – ISO 8601 timestamp.
            success        (bool)  – Always True on successful call.
        """
        try:
            states = self._state_machine.get_all_states()
            is_op = self._state_machine.is_operational()
            is_healthy = self._state_machine.is_healthy()

            summary = (
                f"{self.module_name}: "
                f"lifecycle={states['lifecycle']} | "
                f"operational={states['operational']} | "
                f"health={states['health']}"
            )

            return {
                "summary": summary,
                "is_operational": is_op,
                "is_healthy": is_healthy,
                "lifecycle": states["lifecycle"],
                "operational": states["operational"],
                "health": states["health"],
                "module_name": self.module_name,
                "timestamp": datetime.now().isoformat(),
                "success": True,
            }
        except Exception as exc:
            logger.error(f"get_state_summary failed: {exc}")
            return {"success": False, "error": str(exc)}

    @remote_service()
    async def get_state_history(
        self, request: Dict[str, Any], response: Any = None
    ) -> Dict[str, Any]:
        """
        Return the recent state-change history.

        Request fields (all optional):
            limit (int) – Maximum number of entries to return (default: all).
            layer (str) – Filter by layer name (lifecycle / operational / health).

        Response fields:
            history   (list) – List of state-change dicts (newest last).
            count     (int)  – Number of entries returned.
            total     (int)  – Total entries in history buffer.
            success   (bool) – Always True on successful call.
        """
        try:
            history = list(self._state_history)

            # Optional filters
            layer_filter: Optional[str] = request.get("layer") if request else None
            if layer_filter:
                history = [e for e in history if e.layer == layer_filter]

            limit: Optional[int] = request.get("limit") if request else None
            if limit and limit > 0:
                history = history[-limit:]

            return {
                "history": [e.to_dict() for e in history],
                "count": len(history),
                "total": len(self._state_history),
                "success": True,
            }
        except Exception as exc:
            logger.error(f"get_state_history failed: {exc}")
            return {"success": False, "error": str(exc)}

    # ─────────────────────────────────────────────────────────────────────────
    # State broadcasting (called by async task runner)
    # ─────────────────────────────────────────────────────────────────────────

    async def broadcast_state(self) -> None:
        """
        Broadcast the current state via the Zenoh StateFeeder.

        Called from shutdown_to_offline() for the final Offline notification.
        """
        try:
            state_data = StateEntry(
                previous="N/A",
                trigger="broadcast",
                current=str(self._state_machine.get_operational_state().value),
                module_id=self.module_id,
                module_name=self.module_name,
                timestamp=_dt.now(),
            )
            await self.entity.state_feeder.feed(state_data)
        except Exception as exc:
            logger.error(f"Failed to broadcast state: {exc}")

    async def broadcast_status(self) -> None:
        """Alias for :meth:`broadcast_state` (backward compatibility)."""
        await self.broadcast_state()

    def trigger_task_recovery(
        self,
        reason: str,
        task_name: str,
        attempt: int,
        max_attempts: int,
    ) -> None:
        """Enter lifecycle recovery due to task hang or crash."""
        self._recovery_tracker.mark_recovery_started()
        self._record_error(
            f"Task '{task_name}' recovery: {reason} (attempt {attempt}/{max_attempts})",
            {"task_name": task_name, "attempt": attempt, "reason": reason},
        )
        lifecycle = self._state_machine.get_lifecycle_state()
        if lifecycle != LifecycleState.RECOVERING:
            try:
                self.execute_state_action(
                    StateRequest(
                        layer="lifecycle",
                        action="enter_recovery",
                        metadata={
                            "reason": reason,
                            "task_name": task_name,
                            "attempt": attempt,
                        },
                    )
                )
            except Exception as exc:
                logger.error(f"enter_recovery failed: {exc}")
        try:
            prepare_operational_for_recovery(
                self._state_machine.fsm,
                module_name=self.module_name,
                module_id=self.module_id,
            )
        except Exception as exc:
            logger.warning(f"prepare_operational_for_recovery: {exc}")

    # ─────────────────────────────────────────────────────────────────────────
    # Internal state-mutation helpers (called only by application code)
    # ─────────────────────────────────────────────────────────────────────────

    def execute_state_action(self, request: StateRequest) -> StateResponse:
        """
        Execute a state-machine action.

        This method is intentionally **not** decorated with @remote_service.
        State mutations are triggered only by application-internal code.

        Args:
            request: Describes the layer, action and optional metadata.

        Returns:
            :class:`StateResponse` with success flag and updated state.

        Raises:
            ValueError: If the layer or action is unknown.
        """
        layer = request.layer
        action = request.action
        metadata = request.metadata or {}

        prev_state = self.get_current_state()

        try:
            self._dispatch_action(layer, action, metadata)

            current = self.get_current_state()
            self._record_history_diff(prev_state, current)

            return StateResponse(
                success=True,
                current_state=current,
                message=f"Successfully executed {layer}.{action}",
            )

        except Exception as exc:
            logger.error(f"Failed to execute action {layer}.{action}: {exc}")
            self._record_error(str(exc), {"layer": layer, "action": action})
            raise

    # Backward-compatibility alias
    def execute_status_action(self, request: "StateRequest") -> "StateResponse":
        """Alias for :meth:`execute_state_action` (backward compatibility)."""
        return self.execute_state_action(request)

    async def shutdown_to_offline(self, reason: str = "shutdown_requested") -> None:
        """
        Perform a full lifecycle shutdown transition: Active → ShuttingDown → Offline.

        Transitions the state machine through the shutdown sequence and broadcasts
        the final Offline state so connected clients are notified before the
        process exits.

        Args:
            reason: Human-readable reason for the shutdown (logged + stored in metadata).
        """
        logger.info(f"🔴 Initiating shutdown_to_offline (reason={reason})")
        try:
            self.execute_state_action(
                StateRequest(
                    layer="lifecycle",
                    action="shutdown",
                    metadata={"reason": reason},
                )
            )
            logger.info("✅ Lifecycle → ShuttingDown")
        except Exception as exc:
            logger.warning(f"shutdown transition failed (may already be in shutdown): {exc}")

        try:
            self.execute_state_action(
                StateRequest(
                    layer="lifecycle",
                    action="complete_shutdown",
                    metadata={},
                )
            )
            logger.info("✅ Lifecycle → Offline")
        except Exception as exc:
            logger.warning(f"complete_shutdown transition failed: {exc}")

        try:
            await self.broadcast_state()
            logger.info("📡 Offline state broadcasted to all clients")
        except Exception as exc:
            logger.warning(f"Failed to broadcast offline state: {exc}")

    def _dispatch_action(self, layer: str, action: str, metadata: Dict[str, Any]) -> None:
        """Translate layer/action pairs to UnifiedStateMachine calls."""
        if layer == "lifecycle":
            if action == "start":
                self._state_machine.start(metadata)
            elif action == "complete_initialization":
                self._state_machine.complete_initialization(metadata)
            elif action == "fail_initialization":
                self._state_machine.fail_initialization(metadata.get("error"))
            elif action == "shutdown":
                self._state_machine.shutdown(metadata.get("reason"))
            elif action == "complete_shutdown":
                self._state_machine.complete_shutdown()
            elif action == "suspend":
                self._state_machine.suspend(metadata.get("reason"))
            elif action == "resume_from_suspend":
                self._state_machine.resume_from_suspend(metadata)
            elif action == "enter_recovery":
                self._state_machine.enter_recovery(metadata.get("error"))
            elif action == "complete_recovery":
                self._state_machine.complete_recovery()
            elif action == "fail_recovery":
                self._state_machine.fail_recovery()
            else:
                raise ValueError(f"Unknown lifecycle action: {action}")

        elif layer == "operational":
            if action == "ready":
                self._state_machine.set_ready()
            elif action == "start_task":
                self._state_machine.start_task(metadata)
            elif action == "pause":
                self._state_machine.pause(metadata.get("reason"))
            elif action == "resume":
                self._state_machine.resume()
            elif action == "reset":
                self._state_machine.reset()
            else:
                raise ValueError(f"Unknown operational action: {action}")

        elif layer == "health":
            if action == "report_warning":
                self._state_machine.report_warning(metadata)
            elif action == "report_fault":
                self._state_machine.report_fault(metadata.get("error"))
            elif action == "recover":
                self._state_machine.recover()
            elif action == "emergency_stop":
                self._state_machine.emergency_stop(metadata.get("reason", "no reason added"))
            else:
                raise ValueError(f"Unknown health action: {action}")

        else:
            raise ValueError(f"Unknown layer: {layer}")

    # ─────────────────────────────────────────────────────────────────────────
    # History & diagnostics helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _record_history_diff(self, before: ThreeLayerState, after: ThreeLayerState) -> None:
        """Record any state changes between two snapshots."""
        now = datetime.now()

        if before.lifecycle != after.lifecycle:
            self._state_history.append(
                StateHistoryEntry(
                    layer="lifecycle",
                    from_state=before.lifecycle,
                    to_state=after.lifecycle,
                    timestamp=now,
                )
            )

        if before.operational != after.operational:
            self._state_history.append(
                StateHistoryEntry(
                    layer="operational",
                    from_state=before.operational,
                    to_state=after.operational,
                    timestamp=now,
                )
            )

        if before.health != after.health:
            self._state_history.append(
                StateHistoryEntry(
                    layer="health",
                    from_state=before.health,
                    to_state=after.health,
                    timestamp=now,
                )
            )

    def _record_error(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Store an error entry in the error history buffer."""
        entry: Dict[str, Any] = {
            "error_message": message,
            "error_details": details or {},
            "timestamp": datetime.now().isoformat(),
            "state_at_error": (
                self.get_current_state().to_dict() if self._state_machine is not None else None
            ),
        }
        self._last_error_state = entry
        self._error_history.append(entry)


# # ─────────────────────────────────────────────────────────────────────────────
# # Async runner (used by main.py / taskmanager) [OBSOLOETE]
# # ─────────────────────────────────────────────────────────────────────────────


# async def state_manager_runner(state_manager: StateManager) -> None:
#     """
#     Async broadcast loop for the StateManager.

#     Runs at the interval configured in ``module_state_meta.json``
#     (default 1 Hz).  Should be scheduled as an asyncio Task.

#     Args:
#         state_manager: The :class:`StateManager` instance to run.
#     """
#     interval = state_manager.broadcast_interval
#     logger.info(
#         f"▶️  StateManager broadcast loop started "
#         f"(interval={interval:.2f}s / "
#         f"{state_manager._config.get('broadcast_interval_hz', 1.0):.1f} Hz)"
#     )
#     while True:
#         try:
#             await state_manager.broadcast_state()
#         except asyncio.CancelledError:
#             logger.info("StateManager broadcast loop cancelled")
#             break
#         except Exception as exc:
#             logger.error(f"StateManager loop error: {exc}")
#         await asyncio.sleep(interval)
