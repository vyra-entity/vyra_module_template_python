"""
Main REST API application for {{ module_name }} Backend Webserver

Modern FastAPI application with direct dependency injection (no gRPC).
Communicates with core application components via container_injection.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import asyncio

# Core configuration
from .core.config import settings

from ..logging_config import get_logger, log_exception, log_function_call, log_function_result

# Import structured routers
from .websocket.router import (
    router as websocket_router,
    operation_monitor,
)

# Plugin system
from .plugin import plugin_router

from .services.redis_service import redis_service

# Import authentication
from .auth import auth_router, set_auth_service, AuthenticationService
from .settings import settings_router


# Add additional imports for {{ module_name }}-specific routers, services, clients, etc. here

# ==> INSERT HERE <==


logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for Startup/Shutdown Events
    Replaces deprecated @app.on_event("startup") and @app.on_event("shutdown")
    """
    # Startup
    asyncio.create_task(operation_monitor())

    # Add any additional startup tasks here (e.g. initialize database connections, load models, etc.)

    # => INSERT HERE <==

    # Connect Redis and initialise authentication service
    try:
        redis_client = await redis_service.get_client()
        auth_service = AuthenticationService(
            redis_client=redis_client, module_id="{{ module_name }}"
        )
        set_auth_service(auth_service)
        logger.info("✅ Authentication service initialized")
    except Exception as exc:
        logger.error(
            "❌ Auth/Redis initialization failed — backend starts in degraded mode (auth unavailable)",
            error=str(exc),
        )

    yield

    # Shutdown
    await redis_service.cleanup()


# Create FastAPI application
app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    description=settings.API_DESCRIPTION,
    docs_url="/api/docs",  # Swagger UI
    redoc_url="/api/redoc",  # ReDoc
    lifespan=lifespan,
)

# Include routers with proper prefixes and tags
app.include_router(websocket_router, prefix="/ws", tags=["WebSocket"])

# Include authentication router
app.include_router(auth_router, tags=["Authentication"])

# Include plugin router
app.include_router(plugin_router, prefix="/plugin", tags=["Plugin System"])

app.include_router(settings_router, tags=["Settings"])


# ------------------------ API Endpoints -------------------------
# Add additional {{ module_name }}-specific API routers here

# ==> INSERT HERE <==


# ------------------------ Frontend & Static Files -------------------------

# Mount static files if available
if settings.frontend_assets_available:
    if (settings.FRONTEND_DIST_PATH / "static").exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(settings.FRONTEND_DIST_PATH / "static")),
            name="static",
        )

    if (settings.FRONTEND_DIST_PATH / "assets").exists():
        app.mount(
            "/assets",
            StaticFiles(directory=str(settings.FRONTEND_DIST_PATH / "assets")),
            name="assets",
        )


# API Root endpoints
@app.get("/")
async def root():
    """API Root - zeigt verfügbare Endpoints"""
    return {
        "service": settings.API_TITLE,
        "version": settings.API_VERSION,
        "endpoints": {
            "status": "/status",
            "health": "/health",
            "auth": "/auth",
            "plugin": "/plugin",
            "ws": "/ws",
            "docs": "/api/docs",
            "redoc": "/api/redoc",
        },
        "environment": {
            "development_mode": settings.DEVELOPMENT_MODE,
            "debug": settings.DEBUG,
            "has_ssl": settings.has_ssl_certificates,
            "frontend_available": settings.frontend_assets_available,
        },
    }


@app.get("/status")
async def api_status():
    """Health check und Status-Information"""
    return {
        "status": "running",
        "service": "{{ module_name }}",
        "version": settings.API_VERSION,
        "environment": {
            "development_mode": settings.DEVELOPMENT_MODE,
            "workspace_path": str(settings.WORKSPACE_ROOT),
            "modules_path": str(settings.MODULES_PATH),
            "docker_stack": settings.DOCKER_STACK_NAME,
        },
    }


@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
    try:
        # Check if essential directories exist
        checks = {
            "modules_directory": settings.MODULES_PATH.exists(),
            "storage_directory": settings.STORAGE_PATH.exists(),
            "certificates": settings.has_ssl_certificates,
            "frontend": settings.frontend_assets_available,
        }

        # Determine overall health
        healthy = all(checks.values())

        return {
            "status": "healthy" if healthy else "degraded",
            "service": "{{ module_name }}",
            "version": settings.API_VERSION,
            "checks": checks,
            "timestamp": "2025-10-28T13:30:00Z",  # Would use actual timestamp
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "service": "{{ module_name }}",
            "version": settings.API_VERSION,
            "error": str(e),
            "timestamp": "2025-10-28T13:30:00Z",
        }


# Frontend fallback routes
@app.get("/dashboard")
@app.get("/dashboard/{path:path}")
async def dashboard_fallback(path: str = ""):
    """Frontend-Weiterleitung - sollte nur in Notfällen erreicht werden"""
    return {
        "message": "Frontend wird durch Nginx oder Vue Dev Server bereitgestellt",
        "development_mode": settings.DEVELOPMENT_MODE,
        "frontend_path": str(settings.FRONTEND_PATH),
        "redirect": f"/{{ module_name }}/{path}" if path else "/{{ module_name }}/",
    }
