
import asyncio
from .logging_config import get_logger
from collections import deque
from typing import Callable, Any, Optional

from vyra_base.lifecycle import TaskSupervisorConfig, lifecycle_task_supervisor

from .state.state_manager import StateManager

logger = get_logger(__name__)


class TaskManager:
    """Manage asyncio tasks with heartbeat-based lifecycle supervision."""

    TIMEOUT_CANCEL_TASK = 30
    DEFAULT_HANG_TIMEOUT_S = 30.0

    def __init__(self) -> None:
        self.tasks: dict[str, tuple[Callable, asyncio.Task, list[Any], dict[str, Any]]] = {}
        self.history: deque[dict] = deque(maxlen=100)
        self.metadata: dict[str, dict[str, Any]] = {}

    def add_task(
        self,
        coro: Callable,
        *args,
        watchdog_enabled: bool = True,
        hang_timeout_s: float = DEFAULT_HANG_TIMEOUT_S,
        **kwargs,
    ) -> str:
        if coro.__name__ in self.tasks:
            logger.error(f"Task <{coro.__name__}> already exists. Use restart_task to restart it.")
            return coro.__name__
        self._create_task(
            coro,
            *args,
            watchdog_enabled=watchdog_enabled,
            hang_timeout_s=hang_timeout_s,
            **kwargs,
        )
        logger.info(f"Task <{coro.__name__}> has been added.")
        return coro.__name__

    async def remove_task(self, coro: Callable, force: bool = True) -> None:
        if coro.__name__ in self.tasks:
            await self.cancel_task(coro, force=True)
            del self.tasks[coro.__name__]
            self.history.append({
                "task_name": coro.__name__,
                "removed_at": asyncio.get_event_loop().time(),
            })
            logger.info(f"Task <{coro.__name__}> has been removed.")

    async def restart_task(self, coro: Callable, force_restart: bool = False, force_time: float = 5) -> None:
        if coro.__name__ not in self.tasks:
            logger.error(f"Task <{coro.__name__}> does not exist.")
            return
        if not self.tasks[coro.__name__][1].done():
            await self.cancel_task(coro, False)
            if force_restart:
                await asyncio.sleep(force_time)
                await self.cancel_task(coro, True)
        coro_fn, _, args, kwargs = self.tasks[coro.__name__]
        meta = self.metadata.get(coro.__name__, {})
        self._create_task(
            coro_fn,
            *args,
            watchdog_enabled=meta.get("watchdog_enabled", True),
            hang_timeout_s=meta.get("hang_timeout_s", self.DEFAULT_HANG_TIMEOUT_S),
            **kwargs,
        )

    async def cancel_task(self, coro: Callable, force: bool) -> None:
        if coro.__name__ not in self.tasks:
            return
        _, task, _, _ = self.tasks[coro.__name__]
        task.cancel()
        start_time = asyncio.get_event_loop().time()
        while not task.done() and force and (asyncio.get_event_loop().time() - start_time) < self.TIMEOUT_CANCEL_TASK:
            await asyncio.sleep(0.1)
            task.cancel()
        self.add_history_entry("cancelled", coro.__name__)

    async def cancel_all(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for coro, _, _, _ in self.tasks.values():
                tg.create_task(self.cancel_task(coro, force=True))
        self.tasks.clear()

    def touch_heartbeat(self, task_name: str) -> None:
        if task_name in self.metadata:
            self.metadata[task_name]["last_heartbeat"] = asyncio.get_event_loop().time()

    def is_task_hung(self, task_name: str, hang_timeout_s: float) -> bool:
        if task_name not in self.tasks:
            return False
        meta = self.metadata.get(task_name, {})
        if not meta.get("watchdog_enabled", True):
            return False
        _, task, _, _ = self.tasks[task_name]
        if task.done():
            return False
        last_hb = meta.get("last_heartbeat", meta.get("last_start_time", 0.0))
        return (asyncio.get_event_loop().time() - last_hb) >= hang_timeout_s

    def register_watchdog(self, task_name: str, timeout_s: float, *, enabled: bool = True) -> None:
        if task_name not in self.metadata:
            self.metadata[task_name] = {}
        self.metadata[task_name]["hang_timeout_s"] = timeout_s
        self.metadata[task_name]["watchdog_enabled"] = enabled

    def _create_task(
        self,
        coro: Callable,
        *args,
        watchdog_enabled: bool = True,
        hang_timeout_s: float = DEFAULT_HANG_TIMEOUT_S,
        **kwargs,
    ) -> None:
        if coro.__name__ in self.tasks:
            _, old_task, _, _ = self.tasks[coro.__name__]
            if not old_task.done():
                old_task.cancel()
        task = asyncio.create_task(coro(*args, **kwargs), name=coro.__name__)
        self.tasks[coro.__name__] = (coro, task, list(args), dict(kwargs))
        now = asyncio.get_event_loop().time()
        if coro.__name__ not in self.metadata:
            self.metadata[coro.__name__] = {
                "recovery_counter": 0,
                "last_start_time": now,
                "successful_run": False,
                "last_heartbeat": now,
                "watchdog_enabled": watchdog_enabled,
                "hang_timeout_s": hang_timeout_s,
            }
        else:
            self.metadata[coro.__name__].update({
                "last_start_time": now,
                "last_heartbeat": now,
                "watchdog_enabled": watchdog_enabled,
                "hang_timeout_s": hang_timeout_s,
            })
        self.add_history_entry("created", coro.__name__)

    def add_history_entry(self, action: str, task_name: str) -> None:
        if self.history and action == self.history[-1]["action"] and task_name == self.history[-1]["task_name"]:
            self.history[-1]["timestamp"] = asyncio.get_event_loop().time()
            self.history[-1]["duplicate_count"] += 1
            return
        self.history.append({
            "task_name": task_name,
            "action": action,
            "timestamp": asyncio.get_event_loop().time(),
            "duplicate_count": 1,
        })

    def get_status(self) -> dict[str, Any]:
        status = {}
        for coro_name, (_, task, _, _) in self.tasks.items():
            metadata = self.metadata.get(coro_name, {})
            status[coro_name] = {
                "status": "running" if not task.done() else "cancelled",
                "recovery_counter": metadata.get("recovery_counter", 0),
                "last_start_time": metadata.get("last_start_time", 0),
                "last_heartbeat": metadata.get("last_heartbeat", 0),
                "successful_run": metadata.get("successful_run", False),
                "watchdog_enabled": metadata.get("watchdog_enabled", True),
                "hang_timeout_s": metadata.get("hang_timeout_s", self.DEFAULT_HANG_TIMEOUT_S),
            }
        return status


async def task_supervisor_looper(
    taskmanager: TaskManager,
    statemanager: StateManager,
    check_interval: float = 5.0,
    shutdown_event: Optional[asyncio.Event] = None,
    supervisor_config: Optional[TaskSupervisorConfig] = None,
) -> None:
    config = supervisor_config or TaskSupervisorConfig(check_interval_s=check_interval)
    if supervisor_config is None:
        config.check_interval_s = check_interval
    await lifecycle_task_supervisor(
        taskmanager,
        statemanager,
        config=config,
        shutdown_event=shutdown_event,
    )
