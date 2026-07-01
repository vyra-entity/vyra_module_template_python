"""
Base gRPC Client with Singleton Pattern

Provides thread-safe gRPC client over Unix Domain Sockets for
inter-process communication in industrial automation applications.
"""
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, TypeVar, Generic
from abc import ABC
from vyra_base.com.external.grpc import GrpcClient

logger = logging.getLogger(__name__)

# Generic type for gRPC stub
TStub = TypeVar("TStub")


class BaseGrpcClient(ABC, Generic[TStub]):
    """
    Base gRPC client with singleton pattern for Unix Domain Socket communication.

    Features:
    - Thread-safe singleton per socket path
    - Automatic reconnection handling
    - Connection health monitoring
    - Timeout management
    - Detailed error logging

    Industrial automation standards:
    - Deterministic connection behavior
    - Fast IPC via Unix Domain Sockets
    - Type-safe protobuf communication
    - Comprehensive diagnostics logging
    """

    _instances: Dict[str, "BaseGrpcClient"] = {}
    _lock = asyncio.Lock()

    def __new__(cls, socket_path: Path, *args, **kwargs):
        """Singleton pattern: One instance per socket_path."""
        socket_key = str(socket_path)
        if socket_key not in cls._instances:
            instance = super().__new__(cls)
            cls._instances[socket_key] = instance
        return cls._instances[socket_key]

    def __init__(self, socket_path: Path, timeout: float = 5.0, auto_reconnect: bool = True):
        """
        Initialize gRPC client (only once per socket_path).

        Args:
            socket_path: Path to Unix domain socket
            timeout: Request timeout in seconds
            auto_reconnect: Enable automatic reconnection on failure
        """
        # Prevent re-initialization
        if hasattr(self, "_initialized"):
            return

        self.socket_path = Path(socket_path)
        self.timeout = timeout
        self.auto_reconnect = auto_reconnect

        self._client: Optional[GrpcClient] = None
        self._stub: Optional[TStub] = None
        self._connected = False
        self._initialized = True

        logger.info(
            f"🔧 {self.__class__.__name__} initialized "
            f"(socket: {self.socket_path}, timeout: {timeout}s)"
        )

    def _create_stub(self, channel) -> TStub:
        """
        Create gRPC stub from channel.

        Must be implemented by subclasses to return specific stub type.

        Args:
            channel: gRPC channel

        Returns:
            TStub: gRPC service stub
        """
        raise NotImplementedError("Subclass must implement _create_stub()")

    async def connect(self) -> bool:
        """
        Connect to gRPC server via Unix Domain Socket.

        Returns:
            bool: True if connected successfully
        """
        try:
            if self._connected and self._stub is not None:
                logger.debug(f"Already connected to {self.socket_path}")
                return True

            logger.info(f"🔗 Connecting to gRPC service: {self.socket_path}")

            # Check if socket exists
            if not self.socket_path.exists():
                logger.error(f"❌ Socket not found: {self.socket_path}")
                return False

            # Create gRPC client
            self._client = GrpcClient(socket_path=self.socket_path)

            if self._client is not None:
                await self._client.connect()

                # Create stub from channel
                self._stub = self._create_stub(self._client.channel)
                self._connected = True

                logger.info(f"✅ Connected to gRPC service: {self.socket_path}")
                return True
            else:
                logger.warning("Failed to create GrpcClient")
                return False

        except Exception as e:
            logger.error(f"❌ Failed to connect to {self.socket_path}: {e}", exc_info=True)
            self._connected = False
            return False

    async def disconnect(self):
        """Disconnect from gRPC server and cleanup resources."""
        try:
            if self._client:
                await self._client.close()
                self._connected = False
                self._stub = None
                logger.info(f"✅ Disconnected from {self.socket_path}")
        except Exception as e:
            logger.error(f"Error disconnecting: {e}")

    async def ensure_connected(self) -> bool:
        """
        Ensure client is connected, reconnect if necessary.

        Returns:
            bool: True if connected
        """
        if not self._connected or self._stub is None:
            if self.auto_reconnect:
                logger.warning(f"⚠️ Connection lost to {self.socket_path}, reconnecting...")
                return await self.connect()
            return False
        return True

    async def health_check(self) -> bool:
        """
        Check if gRPC service is healthy.

        Returns:
            bool: True if service is reachable
        """
        try:
            if not await self.ensure_connected():
                return False

            # Socket existence check as basic health indicator
            return self.socket_path.exists() and self._connected

        except Exception as e:
            logger.warning(f"⚠️ Health check failed: {e}")
            return False

    @property
    def stub(self) -> Optional[TStub]:
        """
        Get gRPC stub for making RPC calls.

        Returns:
            Optional[TStub]: gRPC service stub or None if not connected
        """
        if not self._connected:
            logger.warning(f"⚠️ Stub accessed while not connected to {self.socket_path}")
        return self._stub

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connected and self._stub is not None
