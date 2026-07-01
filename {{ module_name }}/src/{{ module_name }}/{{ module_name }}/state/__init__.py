"""
State management package for {{ module_name }}.

Provides the 3-layer state machine integration (Lifecycle / Operational / Health)
with read-only Zenoh interface exposure for external modules.

Quick start
-----------
The ``StateManager`` is instantiated in ``main.py`` with the shared
``VyraEntity`` and scheduled as an async broadcast task:

Note
----
``StateManager`` is lazily imported (via
``__getattr__``) to avoid pulling in ROS2/Zenoh dependencies at package
import time.  Direct access via ``from .state_types import ...`` is always
available without side-effects.
"""

from __future__ import annotations

# ── Eager imports — no heavy dependencies ────────────────────────────────────

# Import enums directly from vyra_base to guarantee symbol resolution
# (avoids IDE/linter false-negatives on re-exported symbols)
from vyra_base.state.state_types import (
    LifecycleState,
    OperationalState,
    HealthState,
)

from .state_types import (
    # Data structures
    ThreeLayerState,
    StateRequest,
    StateResponse,
    StateHistoryEntry,
    # Dynamic action helpers
    get_layer_actions,
    LAYER_ACTIONS,
    is_valid_action,
    # Backward-compatibility aliases
    ThreeLayerStatus,
    StatusRequest,
    StatusResponse,
)

# ── Lazy imports — only loaded when actually requested ────────────────────────
# StateManager pull in ament_index_python, interface.py,
# logging_config.py etc. – packages that require a running ROS2 environment.
# Using __getattr__ prevents import errors in unit-test environments.


def __getattr__(name: str):  # noqa: N807
    if name in ("StateManager"):
        from .state_manager import StateManager  # noqa: PLC0415

        globals()["StateManager"] = StateManager
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Manager (lazy)
    "StateManager",
    # Enums (direct from vyra_base)
    "LifecycleState",
    "OperationalState",
    "HealthState",
    # Data structures
    "ThreeLayerState",
    "StateRequest",
    "StateResponse",
    "StateHistoryEntry",
    # Action helpers
    "get_layer_actions",
    "LAYER_ACTIONS",
    "is_valid_action",
    # Backward-compatibility aliases
    "ThreeLayerStatus",
    "StatusRequest",
    "StatusResponse",
]
