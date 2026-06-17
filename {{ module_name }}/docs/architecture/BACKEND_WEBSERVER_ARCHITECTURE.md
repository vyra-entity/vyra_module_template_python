# Backend Webserver Architecture

## Overview

The {module_name} backend webserver uses a modern, monolithic architecture with direct dependency injection, providing efficient access to core application components without inter-process communication overhead.

**Key Principles**:

1. **Direct Dependency Injection**: Backend accesses Component instances via `container_injection`
2. **Monolithic Process**: Uvicorn/FastAPI runs in the same process as ROS2 core application
3. **Type Safety**: Static typing throughout with Pydantic models
4. **Industrial Standards**: Connection pooling for external APIs, comprehensive logging
5. **Clear Separation**: Direct DI for internal access, HTTP for external Container Manager APIs

## Architecture Change (February 2026)

**Previous Architecture** (Deprecated):
```
FastAPI Endpoint → gRPC Client → Unix Domain Socket → gRPC Server → Component
                    (IPC overhead)
```

**Current Architecture**:
```
FastAPI Endpoint → container_injection.get_component() → Component (direct method call)
                    (zero IPC overhead)
```

**Benefits**:
- ⚡ **Performance**: ~5-10ms saved per request (no socket/serialization overhead)
- 🎯 **Simplicity**: Direct method calls vs socket management
- 🔧 **Maintainability**: No proto file generation, no gRPC server lifecycle
- 🏗️ **Architecture**: Single process with shared memory access

## Directory Structure

```
src/{module_name}/{module_name}/
├── backend_webserver/         # Backend API module (previously rest_api/)
│   ├── __init__.py
│   ├── asgi.py                # ASGI application entry point
│   ├── main_rest.py           # FastAPI app with routers
│   ├── auth/                  # Authentication endpoints
│   │   ├── __init__.py
│   │   ├── models.py          # Pydantic models
│   │   ├── router.py          # FastAPI router
│   │   └── auth_service.py    # Auth logic (uses container_injection)
│   ├── module/                # Module management endpoints
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── router.py
│   │   └── service.py         # Module logic (uses container_injection)
│   ├── core/                  # Configuration and utilities
│   │   ├── config.py          # Settings management
│   │   └── logging.py         # Logging configuration
│   ├── clients/               # External HTTP clients
│   │   └── http/              # HTTP clients for Container Manager
│   │       ├── base_client.py
│   │       ├── hardware.py
│   │       └── module_http.py
│   └── websocket/             # WebSocket endpoints
├── application/               # Core business logic
│   └── application.py         # Component class with UserManager, ModuleRegistry
├── container_injection.py     # Global DI container
└── main.py                    # Application entry point
```

## Dependency Injection Architecture

### container_injection.py

**Purpose**: Global dependency injection container providing singleton instances to backend webserver.

**Available Components**:
- `entity`: VyraEntity (ROS2 node wrapper)
- `component`: Component (core application logic with UserManager, ModuleRegistry)
- `task_manager`: TaskManager (async task lifecycle)
- `status_manager`: StatusManager (system health monitoring)
- `user_manager`: UserManager (user management service)

**Usage in FastAPI Endpoints**:
```python
from ....container_injection import get_component

async def my_endpoint():
    # Get Component instance with all services
    component = get_component()
    
    # Direct method calls - no IPC overhead
    result = await component.registry.register_new_module_impl(...)
    users = await component.internal_usermanager.list_users()
    
    return {"status": "success", "data": result}
```

**Key Functions**:
- `set_entity(entity)`, `get_entity()`: VyraEntity access
- `set_component(component)`, `get_component()`: Component access
- `set_task_manager(tm)`, `get_task_manager()`: TaskManager access
- `set_status_manager(sm)`, `get_status_manager()`: StatusManager access
- `set_user_manager(um)`, `get_user_manager()`: UserManager access
- `is_initialized()`: Check if container is ready

## Backend Webserver Components

### 1. Authentication Service

**File**: `backend_webserver/auth/auth_service.py`

**Purpose**: User authentication and session management

**Architecture**:
```python
from ....container_injection import get_component

class AuthService:
    async def authenticate_user(self, username: str, password: str) -> tuple[bool, str]:
        # Direct access to InternalUserManager
        component = get_component()
        
        # Direct method call - no gRPC
        success = await component.internal_usermanager.authenticate(username, password)
        
        if success:
            # Generate JWT token
            token = self._create_access_token(username)
            return True, token
        
        return False, ""
```

**Methods**:
- `authenticate_user()`: Verify credentials and generate JWT
- `validate_token()`: Verify JWT token validity
- `refresh_token()`: Generate new token from valid refresh token

### 2. Module Management Service

**File**: `backend_webserver/module/service.py`

**Purpose**: Module installation, deletion, and lifecycle management

**Architecture**:
```python
from ....container_injection import get_component
from ..clients.http.module_http import module_http_client

class ModuleService:
    async def install_module(self, module_name: str, version: str, repo_info: dict):
        component = get_component()
        
        # Step 1: Register module permissions with SROS2 (direct call)
        await component.registry.register_new_module_impl(
            module_name=module_name,
            instance_id=instance_id,
            base_node_name=base_node_name,
            function_scope=function_scope
        )
        
        # Step 2: Install via Container Manager (HTTP client)
        result = await module_http_client.install_module(
            module_name=module_name,
            instance_id=instance_id,
            version=version,
            repository_info=repo_info
        )
        
        return result
```

**Methods**:
- `install_module()`: Register permissions + install container
- `uninstall_module()`: Remove container + deregister permissions
- `update_module()`: Update module version
- `get_module_status()`: Query module health and state

**Integration Pattern**:
1. **Internal Operations**: Use `get_component().registry.*` (direct DI)
2. **External Operations**: Use `module_http_client.*` (HTTP to Container Manager)

### 3. External HTTP Clients

**Purpose**: Communication with external Container Manager service (module/container operations)

**Files**:
- `backend_webserver/clients/http/base_client.py`: Singleton HTTP base class
- `backend_webserver/clients/http/module_http.py`: Module lifecycle operations
- `backend_webserver/clients/http/hardware.py`: Hardware node management

**Architecture** (unchanged from previous):
```python
from .base_client import BaseHttpClient

class ModuleHttpClient(BaseHttpClient):
    def __init__(self, base_url: str = "http://container-manager:8080"):
        super().__init__(base_url=base_url)
    
    async def install_module(self, module_name: str, instance_id: str, ...) -> Dict:
        return await self.post("/api/modules/install", json={...})

# Singleton instance
module_http_client = ModuleHttpClient()
```

**Features**:
- Connection pooling (100 max connections)
- Exponential backoff retry (3 attempts)
- Health check monitoring
- Request/response logging

## FastAPI Integration

### ASGI Entry Point

**File**: `backend_webserver/asgi.py`

```python
from .main_rest import app as application

# Direct import - Uvicorn loads this
# Loaded by: "{module_name}.{module_name}.backend_webserver.asgi:application"
```

### Main Application

**File**: `backend_webserver/main_rest.py`

```python
from fastapi import FastAPI
from .auth import auth_router
from .module import module_router

app = FastAPI(title="{module_name} API", version="2.0.0")

# Include routers
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(module_router, prefix="/api/modules", tags=["modules"])

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
```

### Router Example

**File**: `backend_webserver/module/router.py`

```python
from fastapi import APIRouter, Depends, HTTPException
from .service import ModuleService
from .models import InstallModuleRequest, InstallModuleResponse
from ..core.dependencies import get_current_user

router = APIRouter()
module_service = ModuleService()

@router.post("/install", response_model=InstallModuleResponse)
async def install_module(
    request: InstallModuleRequest,
    current_user: str = Depends(get_current_user)
):
    try:
        result = await module_service.install_module(
            module_name=request.module_name,
            version=request.version,
            repo_info=request.repository_info
        )
        return InstallModuleResponse(success=True, data=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

## Component Initialization Flow

**Sequence**:

1. **main.py** starts `runner()` coroutine
2. **runner()** creates VyraEntity and Component
3. **Component.initialize()** creates UserManager and sets in container_injection:
   ```python
   self.user_manager = UserManager(self.entity)
   await self.user_manager.initialize()
   container_injection.set_user_manager(self.user_manager)
   ```
4. **main.py** starts Uvicorn server task
5. **Uvicorn** loads `backend_webserver.asgi:application`
6. **FastAPI endpoints** use `get_component()` to access initialized services

**Initialization Diagram**:
```
┌──────────────────────────────────────────────────────────────────┐
│ main.py: runner()                                                │
│  ├─ entity = VyraEntity()                                        │
│  ├─ component = Component(entity)                                │
│  ├─ await component.initialize()                                 │
│  │   ├─ user_manager = UserManager(entity)                       │
│  │   ├─ await user_manager.initialize()                          │
│  │   └─ container_injection.set_user_manager(user_manager)       │
│  ├─ container_injection.set_component(component)                 │
│  └─ taskmanager.add_task(uvicorn_runner, ...)                    │
│       └─ Uvicorn loads backend_webserver.asgi:application        │
│                                                                  │
│ FastAPI Endpoint Execution:                                      │
│  ├─ Request arrives                                              │
│  ├─ component = get_component()  # ← Direct DI                   │
│  ├─ await component.registry.register_new_module_impl(...)       │
│  └─ Return response                                              │
└──────────────────────────────────────────────────────────────────┘
```

## Migration from gRPC (Legacy)

### Previous gRPC Pattern (Deprecated)

**Old Code** (auth_service.py):
```python
from ..clients.grpc.user_ipc import user_manager_grpc_client

class AuthService:
    async def authenticate_user(self, username: str, password: str):
        # gRPC call over Unix Domain Socket
        response = await user_manager_grpc_client.authenticate_user(
            username=username,
            password=password
        )
        return response.success, response.token
```

**Old Code** (module/service.py):
```python
from ..clients.grpc.module_ipc import ModuleRegistryGrpcClient

class ModuleService:
    def __init__(self):
        self.ipc_client = ModuleRegistryGrpcClient()
    
    async def install_module(self, ...):
        await self.ipc_client.register_module_permission(...)
```

### Current Direct DI Pattern

**New Code** (auth_service.py):
```python
from ....container_injection import get_component

class AuthService:
    async def authenticate_user(self, username: str, password: str):
        # Direct method call - no IPC
        component = get_component()
        success = await component.internal_usermanager.authenticate(username, password)
        
        if success:
            token = self._create_access_token(username)
            return True, token
        return False, ""
```

**New Code** (module/service.py):
```python
from ....container_injection import get_component

class ModuleService:
    async def install_module(self, ...):
        component = get_component()
        
        # Direct method call to registry
        await component.registry.register_new_module_impl(
            module_name=module_name,
            instance_id=instance_id,
            base_node_name=base_node_name,
            function_scope=function_scope
        )
```

**Migration Checklist**:
1. ✅ Replace `from ..clients.grpc.*` imports with `from ....container_injection import get_component`
2. ✅ Replace `await grpc_client.method()` with `component = get_component(); await component.service.method()`
3. ✅ Remove gRPC client instantiation (`self.ipc_client = ...`)
4. ✅ Update proto file references (now deprecated)
5. ✅ Remove Unix Domain Socket path configurations

## Performance Characteristics

### Direct Dependency Injection

**Latency**:
- Direct method call: **~0.01-0.1ms** (function call overhead only)
- Previous gRPC over UDS: ~1-5ms (socket + serialization)
- **Performance gain**: ~10-50x faster for internal operations

**Memory**:
- Shared memory access: No data serialization
- Previous gRPC: Protobuf serialization overhead
- **Memory savings**: ~50-80% reduction in serialization overhead

**Comparison**:
```
Operation: component.registry.register_new_module_impl(...)

Direct DI:          0.05ms
───────────────────────────────────────────────────▶ result

Previous gRPC:      3.2ms
─ serialize ─ socket ─ deserialize ─ process ─ serialize ─ socket ─ deserialize ──▶ result
```

### External HTTP Clients

**Unchanged**: Container Manager communication still uses HTTP (external service)

**Latency** (Connection pooled):
- First request: ~50-100ms (DNS + TCP + TLS)
- Subsequent requests: ~5-20ms (application logic only)

## Best Practices

### 1. Always Use container_injection for Internal Access

```python
# ✅ Correct: Direct DI
from ....container_injection import get_component

async def my_endpoint():
    component = get_component()
    result = await component.registry.register_new_module_impl(...)
    return result

# ❌ Incorrect: Don't create new gRPC clients (deprecated)
from ..clients.grpc.module_ipc import ModuleRegistryGrpcClient
client = ModuleRegistryGrpcClient()  # Creates unnecessary socket
```

### 2. Use HTTP Clients Only for External Services

```python
# ✅ Correct: HTTP for Container Manager (external service)
from ..clients.http.module_http import module_http_client
result = await module_http_client.install_module(...)

# ❌ Incorrect: Don't use HTTP for internal component access
# (Component is in same process - use DI instead)
```

### 3. Handle Initialization Checks

```python
from ....container_injection import get_component, is_initialized

async def my_endpoint():
    if not is_initialized():
        raise HTTPException(
            status_code=503,
            detail="Services not initialized yet"
        )
    
    component = get_component()
    # Safe to use component now
```

### 4. Error Handling

```python
async def my_endpoint():
    try:
        component = get_component()
        result = await component.registry.register_new_module_impl(...)
        return {"success": True, "data": result}
    except AttributeError as e:
        # Component not initialized or method doesn't exist
        raise HTTPException(status_code=503, detail="Service unavailable")
    except Exception as e:
        # Business logic error
        raise HTTPException(status_code=500, detail=str(e))
```

### 5. Type Safety

```python
from ....container_injection import get_component
from ...application.application import Component

async def my_endpoint():
    component: Component = get_component()
    
    # IDE now provides autocomplete for:
    # - component.registry
    # - component.user_manager
    # - component.internal_usermanager
    # - component.entity
```

## Troubleshooting

### Issue: HTTP 503 "Services not initialized yet"

**Cause**: FastAPI endpoint called before Component initialization completed

**Solution**: Wait for ROS2 initialization (~2-3 minutes after container start)

**Debug**:
```python
from ....container_injection import is_initialized, get_component

print(f"Container initialized: {is_initialized()}")
if is_initialized():
    component = get_component()
    print(f"Component: {component}")
    print(f"Registry: {component.registry}")
```

### Issue: AttributeError: 'NoneType' object has no attribute 'registry'

**Cause**: `get_component()` returned None (not set yet)

**Solution**: Ensure `container_injection.set_component(component)` was called in main.py

**Debug**:
```bash
# Inside container
grep -n "set_component" src/{module_name}/{module_name}/main.py
```

### Issue: Method not found on Component

**Cause**: Trying to call method that doesn't exist on Component

**Solution**: Check Component class definition:
```bash
grep -n "class Component" src/{module_name}/{module_name}/application/application.py
```

**Available Services**:
- `component.entity`: VyraEntity (ROS2 node)
- `component.registry`: ModuleRegistry (module permissions)
- `component.user_manager`: UserManager (user management)
- `component.internal_usermanager`: InternalUserManager (authentication)

### Issue: External Container Manager HTTP errors

**Cause**: HTTP client configuration or Container Manager unavailable

**Solution**: Check service availability:
```bash
docker service ps vos2_ws_container_manager
curl http://container-manager:8080/health
```

**Debug HTTP client**:
```python
from ..clients.http.module_http import module_http_client

# Test health check
try:
    health = await module_http_client.health_check()
    print(f"Container Manager health: {health}")
except Exception as e:
    print(f"Connection failed: {e}")
```

## Testing

### Unit Tests (Mock DI Container)

```python
import pytest
from unittest.mock import AsyncMock, patch
from {module_name}.{module_name}.backend_webserver.auth.auth_service import AuthService

@pytest.mark.asyncio
async def test_authenticate_user():
    # Mock get_component()
    mock_component = AsyncMock()
    mock_component.internal_usermanager.authenticate = AsyncMock(return_value=True)
    
    with patch('{module_name}.{module_name}.container_injection.get_component', return_value=mock_component):
        auth_service = AuthService()
        success, token = await auth_service.authenticate_user("admin", "password")
        
        assert success is True
        assert token != ""
        mock_component.internal_usermanager.authenticate.assert_called_once_with("admin", "password")
```

### Integration Tests (Real Container)

```python
import pytest
from fastapi.testclient import TestClient
from {module_name}.{module_name}.backend_webserver.main_rest import app

@pytest.mark.integration
def test_login_endpoint():
    client = TestClient(app)
    
    response = client.post("/api/auth/login", json={
        "username": "admin",
        "password": "password123"
    })
    
    assert response.status_code == 200
    assert "access_token" in response.json()
```

## Deployment Configuration

### Uvicorn Command

**File**: `main.py`

```python
uvicorn_config = uvicorn.Config(
    app="{module_name}.{module_name}.backend_webserver.asgi:application",
    host="0.0.0.0",
    port=8443,
    ssl_certfile="/workspace/storage/certificates/uvicorn/server-cert.pem",
    ssl_keyfile="/workspace/storage/certificates/uvicorn/server-key.pem",
    reload=hot_reload,
    log_level="info"
)
```

### Traefik Routing

**docker-compose.yml**:
```yaml
labels:
  - "traefik.http.routers.modulemanager-api.rule=PathPrefix(`/api/{module_name}/`)"
  - "traefik.http.routers.modulemanager-api.service=modulemanager-api"
  - "traefik.http.services.modulemanager-api.loadbalancer.server.port=8443"
  - "traefik.http.services.modulemanager-api.loadbalancer.server.scheme=http"
```

### Development vs Production

**Development** (`VYRA_DEV_MODE=true`):
- Uvicorn with `--reload` enabled
- Hot reload watches for file changes
- Direct container access: `https://localhost:8443/`

**Production** (`VYRA_DEV_MODE=false`):
- Uvicorn without reload
- Nginx reverse proxy for static frontend
- Traefik routing: `https://localhost/api/{module_name}/`

## See Also

- [Container Injection Migration Guide](./CONTAINER_INJECTION_MIGRATION.md): Detailed DI pattern examples
- [Authentication Guide](./AUTHENTICATION.md): JWT token generation and validation
- [Internal UserManager](./INTERNAL_USERMANAGER.md): User authentication service API
- [Module Registry](./README.md): SROS2 permission management
- [gRPC Deprecation Notice](../storage/interfaces/DEPRECATED.md): Legacy architecture details
