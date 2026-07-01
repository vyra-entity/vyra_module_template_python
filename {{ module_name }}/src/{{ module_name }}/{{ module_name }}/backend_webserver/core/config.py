"""
Core configuration for VYRA Module Manager API
"""
from pathlib import Path
from typing import Optional
import os


class Settings:
    """Application settings and configuration"""

    # API Configuration
    API_TITLE: str = "VYRA Module Manager API"
    API_VERSION: str = "0.1.0"
    API_DESCRIPTION: str = "Modern async API for VYRA Module Management"

    # Paths
    WORKSPACE_ROOT: Path = Path(os.getenv("WORKSPACE_ROOT", "/workspace"))
    MODULES_PATH: Path = Path(os.getenv("MODULES_PATH", str(WORKSPACE_ROOT / "modules")))
    STORAGE_PATH: Path = WORKSPACE_ROOT / "storage"
    LOG_PATH: Path = WORKSPACE_ROOT / "log"
    CONFIG_PATH: Path = WORKSPACE_ROOT / "config"

    # Frontend Configuration
    FRONTEND_PATH: Path = WORKSPACE_ROOT / "frontend"
    FRONTEND_DIST_PATH: Path = FRONTEND_PATH / "dist"

    # Docker Configuration
    DOCKER_STACK_NAME: str = os.getenv("STACK_NAME", "vos2_ws")
    DOCKER_NETWORK: str = f"{DOCKER_STACK_NAME}_vyra-network"

    # Container Manager Configuration
    CONTAINER_MANAGER_URL: str = os.getenv("CONTAINER_MANAGER_URL", "http://container-manager:8080")

    # Redis Configuration
    REDIS_HOST: str = os.getenv("REDIS_HOST", "redis")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))

    # Repository Configuration
    LOCAL_REPOSITORY_CONFIG_PATH: Path = Path(
        os.getenv("LOCAL_REPOSITORY_CONFIG_PATH", str(WORKSPACE_ROOT / "config"))
    )
    REPOSITORY_CONFIG_FILE: Path = LOCAL_REPOSITORY_CONFIG_PATH / "repository_config.json"

    # SSL/TLS Configuration
    CERTIFICATES_PATH: Path = STORAGE_PATH / "certificates"
    SSL_CERT_FILE: Path = CERTIFICATES_PATH / "webserver.crt"
    SSL_KEY_FILE: Path = CERTIFICATES_PATH / "webserver.key"

    # Development Configuration
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    DEVELOPMENT_MODE: bool = os.getenv("VYRA_DEV_MODE", "true").lower() == "true"

    # Logging Configuration
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    def __init__(self):
        """Initialize settings and ensure directories exist"""
        self.ensure_directories()

    def ensure_directories(self):
        """Ensure all required directories exist.

        Failure to create a directory is silently ignored so that importing
        this module outside of a Docker container (e.g. during unit tests)
        does not raise PermissionError / OSError when /workspace is absent.
        """
        directories = [
            self.MODULES_PATH,
            self.STORAGE_PATH,
            self.LOG_PATH,
            self.CERTIFICATES_PATH,
            self.LOCAL_REPOSITORY_CONFIG_PATH,
        ]

        for directory in directories:
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError):
                pass

    @property
    def has_ssl_certificates(self) -> bool:
        """Check if SSL certificates exist"""
        return self.SSL_CERT_FILE.exists() and self.SSL_KEY_FILE.exists()

    @property
    def frontend_assets_available(self) -> bool:
        """Check if frontend assets are available"""
        return self.FRONTEND_DIST_PATH.exists()


# Global settings instance
settings = Settings()
