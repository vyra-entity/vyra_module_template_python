"""
Logging configuration for VYRA modules using standard Python logging.

This module provides a centralized logging configuration that:
- Uses standard Python logging
- Supports JSON and colored console output via core_logging.json
- Provides log rotation for production use
- Supports both ROS2 and Python-only (SLIM) modes
- Offers a backwards-compatible API (previously powered by structlog)
"""

import asyncio
import functools
import inspect
import json
import logging
import logging.config
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional, TypeVar, Union, cast, overload
from vyra_base.helper.env_handler import get_env_required

# Type variables for generic decorator (separate for sync and async)
F = TypeVar("F", bound=Callable[..., Any])
AsyncF = TypeVar("AsyncF", bound=Callable[..., Coroutine[Any, Any, Any]])


# Environment variables for configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.getenv("LOG_FORMAT", "colored")  # 'json' or 'colored'
VYRA_SLIM = os.getenv("VYRA_SLIM", "false").lower() == "true"
VYRA_USE_JSONL_LOGS = os.getenv("VYRA_USE_JSONL_LOGS", "false").lower() == "true"
MODULE_NAME = get_env_required("MODULE_NAME")  # Must be set in .env for proper logging context


class VyraLogger:
    """
    Thin wrapper around standard logging.Logger that accepts structlog-style
    keyword arguments, formatting them into the log message for backwards
    compatibility.

    Usage:
        logger = get_logger(__name__)
        logger.info("processing_started", task_id="12345", user="admin")
        # → logs: "processing_started [task_id='12345' user='admin']"
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    @staticmethod
    def _format_message(event: str, **kwargs: Any) -> str:
        """Format event string with optional keyword context."""
        if kwargs:
            parts = [f"{k}={v!r}" for k, v in kwargs.items()]
            return f"{event} [{' '.join(parts)}]"
        return event

    def debug(self, event: str, *args: Any, **kwargs: Any) -> None:
        if self._logger.isEnabledFor(logging.DEBUG):
            self._logger.debug(self._format_message(event, **kwargs))

    def info(self, event: str, *args: Any, **kwargs: Any) -> None:
        if self._logger.isEnabledFor(logging.INFO):
            self._logger.info(self._format_message(event, **kwargs))

    def warning(self, event: str, *args: Any, **kwargs: Any) -> None:
        if self._logger.isEnabledFor(logging.WARNING):
            self._logger.warning(self._format_message(event, **kwargs))

    def warn(self, event: str, *args: Any, **kwargs: Any) -> None:
        self.warning(event, *args, **kwargs)

    def error(self, event: str, *args: Any, **kwargs: Any) -> None:
        if self._logger.isEnabledFor(logging.ERROR):
            self._logger.error(self._format_message(event, **kwargs))

    def critical(self, event: str, *args: Any, **kwargs: Any) -> None:
        if self._logger.isEnabledFor(logging.CRITICAL):
            self._logger.critical(self._format_message(event, **kwargs))

    def exception(self, event: str, *args: Any, **kwargs: Any) -> None:
        exc_info = kwargs.pop("exc_info", True)
        self._logger.exception(self._format_message(event, **kwargs), exc_info=exc_info)

    # Structlog compatibility stubs – context binding is a no-op
    def bind(self, **kwargs: Any) -> "VyraLogger":
        return self

    def unbind(self, *keys: str) -> "VyraLogger":
        return self

    def new(self, **kwargs: Any) -> "VyraLogger":
        return self

    @property
    def name(self) -> str:
        return self._logger.name

    def isEnabledFor(self, level: int) -> bool:
        return self._logger.isEnabledFor(level)

    def setLevel(self, level: Any) -> None:
        self._logger.setLevel(level)


def load_logging_config(config_path: Path = None) -> Dict[str, Any]:
    """
    Load logging configuration from JSON file.

    Returns:
        Dict containing logging configuration

    Raises:
        FileNotFoundError: If config file doesn't exist
        json.JSONDecodeError: If config file is invalid
    """
    if config_path is None:
        config_path = Path("/workspace/config/core_logging.json")

    if not config_path.exists():
        raise FileNotFoundError(
            f"Logging configuration not found: {config_path}\n"
            "Please ensure core_logging.json exists in the config directory."
        )

    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        return config
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"Invalid JSON in logging configuration: {e.msg}", e.doc, e.pos)


def _patch_logging_config_for_format(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Patch the loaded logging config based on VYRA_USE_JSONL_LOGS.

    - VYRA_USE_JSONL_LOGS=true:  file handlers use 'json' formatter
    - VYRA_USE_JSONL_LOGS=false: file handlers use 'standard' formatter (plain text)
    """
    file_formatter = "json" if VYRA_USE_JSONL_LOGS else "standard"
    for handler_name in ("error_file",):
        if handler_name in config.get("handlers", {}):
            config["handlers"][handler_name]["formatter"] = file_formatter
    return config


def setup_standard_logging(log_dir: Path = None) -> None:
    """
    Configure standard Python logging from JSON config file.

    Provides:
    - File rotation
    - Multiple handlers (console, file, error file)
    - Proper log levels

    File format is controlled by VYRA_USE_JSONL_LOGS.
    """
    try:
        config = load_logging_config()

        # Ensure log directories exist
        if log_dir is None:
            log_dir = Path("/workspace/log/core")

        log_dir.mkdir(parents=True, exist_ok=True)

        # Patch formatter selection based on VYRA_USE_JSONL_LOGS
        config = _patch_logging_config_for_format(config)

        # Apply configuration
        logging.config.dictConfig(config)

        # Set root log level from environment
        logging.getLogger().setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    except (FileNotFoundError, json.JSONDecodeError) as e:
        # Fallback to basic configuration
        print(f"⚠️  Warning: Could not load logging config: {e}", file=sys.stderr)
        print("⚠️  Falling back to basic logging configuration", file=sys.stderr)

        logging.basicConfig(
            level=getattr(logging, LOG_LEVEL, logging.INFO),
            format="%(asctime)s - %(levelname)-8s - %(name)s - %(message)s",
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.StreamHandler(sys.stderr),
            ],
        )


def configure_logging() -> "VyraLogger":
    """
    Main entry point for logging configuration.

    Sets up standard Python logging and returns a configured logger.

    Returns:
        A VyraLogger instance ready for use

    Example:
        >>> from {{ module_name }}.logging_config import configure_logging
        >>> logger = configure_logging()
        >>> logger.info("application_started", version="1.0.0", mode="production")
    """
    setup_standard_logging()

    logger = get_logger(__name__)
    logger.info(
        "logging_configured",
        log_level=LOG_LEVEL,
        log_format=LOG_FORMAT,
        slim_mode=VYRA_SLIM,
        module=MODULE_NAME,
    )
    return logger


def get_logger(name: Optional[str] = None) -> VyraLogger:
    """
    Get a logger with the given name.

    Args:
        name: Logger name (typically __name__ from the calling module).
              If None, uses the caller's module name.

    Returns:
        A VyraLogger instance backed by the standard Python logger.

    Example:
        >>> from {{ module_name }}.logging_config import get_logger
        >>> logger = get_logger(__name__)
        >>> logger.info("processing_started", task_id="12345", user="admin")
    """
    return VyraLogger(logging.getLogger(name))


# ---------------------------------------------------------------------------
# Utility functions for common logging patterns
# ---------------------------------------------------------------------------


def log_function_call(logger: VyraLogger, **kwargs: Any) -> None:
    """
    Log a function call with structured context.

    Args:
        logger: The VyraLogger instance
        **kwargs: Additional context to log

    Example:
        >>> log_function_call(logger, function="process_module", module="{{ module_name }}")
    """
    logger.debug("function_called", **kwargs)


def log_function_result(
    logger: VyraLogger,
    result: Any = None,
    duration_ms: float = 0.0,
    **kwargs: Any,
) -> None:
    """
    Log a function result with structured context.

    Args:
        logger: The VyraLogger instance
        result: The function result
        duration_ms: Execution time in milliseconds
        **kwargs: Additional context to log
    """
    logger.info("function_completed", duration_ms=round(duration_ms, 2), **kwargs)


def log_exception(
    logger: VyraLogger,
    exception: Exception,
    context: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> None:
    """
    Log an exception with structured context.

    Args:
        logger: The VyraLogger instance
        exception: The exception to log
        context: Additional context dictionary
        **kwargs: Additional context to log

    Example:
        >>> try:
        ...     dangerous_operation()
        ... except Exception as e:
        ...     log_exception(logger, e, context={"user_id": 123}, operation="dangerous_operation")
    """
    log_context = {
        "exception_type": type(exception).__name__,
        "exception_message": str(exception),
        **(context or {}),
        **kwargs,
    }
    logger.exception("exception_occurred", **log_context)


@overload
def log_call(func: AsyncF) -> AsyncF:
    ...


@overload
def log_call(func: F) -> F:
    ...


@overload
def log_call(
    func: None = None, *, logger: Optional[VyraLogger] = None
) -> Callable[[Union[F, AsyncF]], Union[F, AsyncF]]:
    ...


def log_call(
    func: Optional[Union[F, AsyncF]] = None,
    *,
    logger: Optional[VyraLogger] = None,
) -> Union[F, AsyncF, Callable[[Union[F, AsyncF]], Union[F, AsyncF]]]:
    """
    Decorator to automatically log function calls, results, and exceptions.

    Logs:
    - Function entry with arguments (at DEBUG level)
    - Function exit with result/duration (at INFO level)
    - Exceptions with full context (at ERROR level)

    Works with both sync and async functions.

    Args:
        func: The function to decorate (automatically passed when used as @log_call)
        logger: Optional VyraLogger instance. If None, creates one from function's module.

    Example:
        >>> @log_call
        ... async def process_module(module_name: str, config: dict):
        ...     return await do_something()

        >>> @log_call(logger=custom_logger)
        ... def synchronous_function(x: int):
        ...     return x * 2
    """

    def decorator(f: Union[F, AsyncF]) -> Union[F, AsyncF]:
        func_logger = logger or get_logger(f.__module__)
        is_async = asyncio.iscoroutinefunction(f)

        def _build_call_context(
            sig: inspect.Signature, args: tuple, kwargs: dict
        ) -> Dict[str, Any]:
            bound_args = sig.bind_partial(*args, **kwargs)
            bound_args.apply_defaults()
            call_context: Dict[str, Any] = {}
            for arg_name, arg_value in bound_args.arguments.items():
                if arg_name == "self":
                    continue
                if isinstance(arg_value, (str, int, float, bool, type(None))):
                    call_context[arg_name] = arg_value
                else:
                    call_context[f"{arg_name}_type"] = type(arg_value).__name__
            return call_context

        if is_async:

            @functools.wraps(f)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                sig = inspect.signature(f)
                call_context = _build_call_context(sig, args, kwargs)
                func_logger.debug(
                    "function_called", function=f.__name__, module=f.__module__, **call_context
                )
                start_time = time.time()
                try:
                    result = await f(*args, **kwargs)
                    duration_ms = (time.time() - start_time) * 1000
                    func_logger.info(
                        "function_completed",
                        function=f.__name__,
                        module=f.__module__,
                        duration_ms=round(duration_ms, 2),
                        success=True,
                    )
                    return result
                except Exception as e:
                    duration_ms = (time.time() - start_time) * 1000
                    log_exception(
                        func_logger,
                        e,
                        context={
                            "function": f.__name__,
                            "module": f.__module__,
                            "duration_ms": round(duration_ms, 2),
                            **call_context,
                        },
                    )
                    raise

            return cast(Union[F, AsyncF], async_wrapper)  # type: ignore[return-value]
        else:

            @functools.wraps(f)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                sig = inspect.signature(f)
                call_context = _build_call_context(sig, args, kwargs)
                func_logger.debug(
                    "function_called", function=f.__name__, module=f.__module__, **call_context
                )
                start_time = time.time()
                try:
                    result = f(*args, **kwargs)
                    duration_ms = (time.time() - start_time) * 1000
                    func_logger.info(
                        "function_completed",
                        function=f.__name__,
                        module=f.__module__,
                        duration_ms=round(duration_ms, 2),
                        success=True,
                    )
                    return result
                except Exception as e:
                    duration_ms = (time.time() - start_time) * 1000
                    log_exception(
                        func_logger,
                        e,
                        context={
                            "function": f.__name__,
                            "module": f.__module__,
                            "duration_ms": round(duration_ms, 2),
                            **call_context,
                        },
                    )
                    raise

            return cast(Union[F, AsyncF], sync_wrapper)  # type: ignore[return-value]

    if func is None:
        return decorator
    return decorator(func)


# Module initialization – configuration happens in main.py / asgi.py
