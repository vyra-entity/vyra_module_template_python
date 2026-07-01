"""
Test Configuration and Fixtures for {module_name}

This conftest.py provides shared fixtures and configuration for all test suites.
Professional test structure with proper separation of concerns:
- Unit tests: Business logic without external dependencies
- Integration tests: Redis, database, and ROS2 communication
- E2E tests: Complete workflows including web interface
"""

import asyncio
import os
import sys
import pytest
import logging
from pathlib import Path
from typing import AsyncGenerator, Generator
from unittest.mock import Mock, MagicMock

# Configure test logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)-8s - %(name)s - %(message)s"
)

# Required env for module imports in unit tests
os.environ.setdefault("MODULE_NAME", "{{ module_name }}_test")
os.environ.setdefault("VYRA_SLIM", "true")
# Provide sensible defaults for path-dependent settings so tests can run
# outside a Docker container (where /workspace is not present).
os.environ.setdefault("WORKSPACE_ROOT", "/tmp")
os.environ.setdefault("MODULES_PATH", "/tmp/test_modules")


def _stub_module(name: str, **attrs):
    mod = MagicMock()
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules.setdefault(name, mod)
    return mod


# Optional dependencies not required for these unit tests
_stub_module("zenoh")
_stub_module("ament_index_python")
_stub_module(
    "ament_index_python.packages", get_package_share_directory=MagicMock(return_value="/mock/share")
)
_stub_module("lark")

# Ensure source import paths for tests
_TESTS_DIR = Path(__file__).resolve().parent
_MODULE_ROOT = _TESTS_DIR.parent
_MODULE_SRC = _MODULE_ROOT / "src"
_VYRA_BASE_SRC = Path("/home/holgder/VYRA/vyra_base_python/src")

for _path in (_MODULE_SRC, _VYRA_BASE_SRC):
    path_str = str(_path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

# Force vyra_base to load as a real package BEFORE any test-file module-level
# code can stub it.  Some test files call sys.modules.setdefault("vyra_base", MagicMock())
# at collection time; if vyra_base is already loaded here that setdefault is a no-op.
import vyra_base  # noqa: F401

# ============================================================================
# Session-scoped fixtures (run once per test session)
# ============================================================================


@pytest.fixture(scope="session")
def event_loop_policy():
    """Set event loop policy for the test session"""
    return asyncio.get_event_loop_policy()


@pytest.fixture(scope="session")
def test_config():
    """
    Test configuration dictionary.
    Contains all test-specific settings like paths, ports, credentials.
    """
    return {
        "redis": {
            "host": os.getenv("REDIS_HOST", "localhost"),
            "port": int(os.getenv("REDIS_PORT", 6379)),
            "password": os.getenv("REDIS_PASSWORD", ""),
            "db": 15,  # Use separate database for tests
            "tls_enabled": False,  # Disable TLS for tests
        },
        "database": {
            "type": "sqlite",
            "path": "/tmp/test_vyra_db/",
            "name": "test_{{ module_name }}.db",
        },
        "ros2": {
            "node_name": "test_{{ module_name }}",
            "namespace": "/test",
        },
        "api": {
            "base_url": "http://localhost:8443",
            "timeout": 5.0,
        },
        "test_data_dir": Path(__file__).parent / "test_data",
    }


# ============================================================================
# Function-scoped fixtures (run once per test function)
# ============================================================================


@pytest.fixture
def mock_redis_client():
    """
    Mock Redis client for unit tests.
    Simulates Redis operations without actual connection.
    """
    mock_client = MagicMock()
    mock_client.get = Mock(return_value=None)
    mock_client.set = Mock(return_value=True)
    mock_client.delete = Mock(return_value=1)
    mock_client.exists = Mock(return_value=False)
    mock_client.keys = Mock(return_value=[])
    return mock_client


@pytest.fixture
def mock_ros2_node():
    """
    Mock ROS2 node for unit tests.
    Provides a fake node without requiring ROS2 initialization.
    """
    mock_node = MagicMock()
    mock_node.get_name = Mock(return_value="test_node")
    mock_node.get_namespace = Mock(return_value="/test")
    mock_node.get_logger = Mock(return_value=logging.getLogger("test_ros2"))
    mock_node.create_publisher = Mock(return_value=MagicMock())
    mock_node.create_subscription = Mock(return_value=MagicMock())
    mock_node.create_service = Mock(return_value=MagicMock())
    mock_node.create_client = Mock(return_value=MagicMock())
    return mock_node


@pytest.fixture
def mock_vyra_entity(mock_ros2_node, mock_redis_client):
    """
    Mock VyraEntity for unit tests.
    Combines mocked ROS2 node and Redis client.
    """
    entity = MagicMock()
    entity.node = mock_ros2_node
    entity.storage = mock_redis_client
    entity.module_name = "test_{{ module_name }}"
    entity.module_id = "test_template_hash"
    return entity


# ============================================================================
# Test data helpers
# ============================================================================


@pytest.fixture
def test_data_path(test_config):
    """Returns path to test data directory"""
    return test_config["test_data_dir"]


# ============================================================================
# Async utilities
# ============================================================================


@pytest.fixture
def anyio_backend():
    """Use asyncio backend for pytest-asyncio"""
    return "asyncio"
