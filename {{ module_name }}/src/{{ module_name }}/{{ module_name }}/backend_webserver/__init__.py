"""
REST API Module
"""

from .main_rest import app  # noqa: F401
from .main_rest import (
    websocket_router,
)

__all__: list[str] = [
    "app",
    "websocket_router",
]
