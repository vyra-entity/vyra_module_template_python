# V2 Module Manager - Backend REST API Documentation

## Overview

**Purpose**: VYRA Module Manager provides centralized management of module instances, repositories, and hardware nodes

**Base URL**: `https://localhost:8443/` (internal container) or `https://localhost/<module_name>/api/` (via Traefik)

**Authentication**: None (internal API, protected by Traefik/network isolation)

**Version**: 0.1.0

**Auto-generated Documentation**:
- Swagger UI: `/api/docs`
- ReDoc: `/api/redoc`

**Architecture Documentation**:
- [Backend Webserver Architecture](./BACKEND_WEBSERVER_ARCHITECTURE.md) - Direct DI architecture and integration patterns

## Response Formats

### Success Response
```json
{
  "success": true,
  "data": {},
  "message": "Operation successful"
}
```

### Error Response
```json
{
  "detail": "Error message",
  "status_code": 400
}
```

### Async Operation Response
```json
{
  "operation_id": "uuid",
  "status": "queued",
  "status_url": "/modules/status/uuid",
  "message": "Operation queued"
}
```

## Endpoints

### Root & Health

#### GET `/`
Get API information and available endpoints

**Success Response** (200 OK):
```json
{
  "service": "VYRA Module Manager API",
  "version": "0.1.0",
  "endpoints": {
    "status": "/status",
    "health": "/health",
    "repository": "/repository",
    "modules": "/modules",
    "hardware": "/api/hardware",
    "docs": "/api/docs"
  }
}
```

---

#### GET `/health`
Health check endpoint

**Success Response** (200 OK):
```json
{
  "status": "healthy",
  "service": "<module_name>"
}
```

---

### Module Instance Management

#### GET `/modules/instances`
List all installed module instances

**Query Parameters**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| include_hidden | boolean | No | Include hidden modules (default: false) |

**Success Response** (200 OK):
```json
{
  "modules": {
    "v2_dashboard": [
      {
        "module_name": "v2_dashboard",
        "instance_id": "aef036f639d3486a985b65ee25df8fec",
        "status": "running",
        "version": "1.0.0",
        "is_primary": true,
        "container_name": "v2_dashboard_aef036f639d3486a985b65ee25df8fec",
        "created_at": "2026-01-15T10:30:00Z",
        "updated_at": "2026-01-27T08:15:00Z",
        "path": "/modules/v2_dashboard_aef036f639d3486a985b65ee25df8fec",
        "permissions": {
          "removable": true,
          "updatable": true,
          "visible": true,
          "multi_instance": false
        }
      }
    ]
  },
  "total_modules": 1,
  "total_instances": 1
}
```

**Example**:
```bash
curl -k https://localhost:8443/modules/instances
```

---

#### GET `/modules/instance/{module_name}/{instance_id}`
Get details for a specific module instance

**Parameters**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| module_name | string | Yes | Module name (e.g., v2_dashboard) |
| instance_id | string | Yes | Instance UUID |

**Success Response** (200 OK):
```json
{
  "module_name": "v2_dashboard",
  "instance_id": "aef036f639d3486a985b65ee25df8fec",
  "status": "running",
  "version": "1.0.0",
  "is_primary": true,
  "container_name": "v2_dashboard_aef036f639d3486a985b65ee25df8fec",
  "path": "/modules/v2_dashboard_aef036f639d3486a985b65ee25df8fec"
}
```

**Error Responses**:
- **404 Not Found**: Instance not found

**Example**:
```bash
curl -k https://localhost:8443/modules/instance/v2_dashboard/aef036f639d3486a985b65ee25df8fec
```

---

#### DELETE `/modules/instance/{module_name}/{instance_id}`
Delete a module instance

**Parameters**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| module_name | string | Yes | Module name |
| instance_id | string | Yes | Instance UUID |

**Query Parameters**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| delete_files | boolean | No | Delete filesystem (default: true) |

**Success Response** (200 OK):
```json
{
  "operation_id": "uuid",
  "status": "queued",
  "status_url": "/modules/status/uuid",
  "message": "Deletion queued"
}
```

**Example**:
```bash
curl -X DELETE -k https://localhost:8443/modules/instance/v2_dashboard/uuid?delete_files=true
```

---

### Primary Instance Management

#### GET `/modules/primary/{module_name}`
Get primary instance for a module

**Parameters**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| module_name | string | Yes | Module name |

**Success Response** (200 OK):
```json
{
  "module_name": "v2_dashboard",
  "primary_instance": {
    "instance_id": "aef036f639d3486a985b65ee25df8fec",
    "status": "running",
    "version": "1.0.0"
  },
  "message": "Primary instance found"
}
```

**Error Responses**:
- **404 Not Found**: No primary instance found

**Example**:
```bash
curl -k https://localhost:8443/modules/primary/v2_dashboard
```

---

#### POST `/modules/primary/{module_name}/{instance_id}`
Set an instance as primary

**Parameters**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| module_name | string | Yes | Module name |
| instance_id | string | Yes | Instance UUID to set as primary |

**Success Response** (200 OK):
```json
{
  "operation_id": "uuid",
  "status": "queued",
  "status_url": "/modules/status/uuid",
  "message": "Set primary operation queued"
}
```

**Example**:
```bash
curl -X POST -k https://localhost:8443/modules/primary/v2_dashboard/new-uuid
```

---

### Module Updates

#### POST `/modules/update`
Update a module to a new version

**Request Body**:
```json
{
  "module_name": "v2_dashboard",
  "instance_id": "aef036f639d3486a985b65ee25df8fec",
  "version": "1.1.0",
  "repository": {
    "type": "local",
    "url": null
  }
}
```

**Success Response** (200 OK):
```json
{
  "operation_id": "uuid",
  "status": "queued",
  "status_url": "/modules/status/uuid",
  "message": "Update queued"
}
```

**Example**:
```bash
curl -X POST -k https://localhost:8443/modules/update \
  -H "Content-Type: application/json" \
  -d '{"module_name":"v2_dashboard","instance_id":"uuid","version":"1.1.0","repository":{"type":"local"}}'
```

---

### Operation Status Tracking

#### GET `/modules/status/{operation_id}`
Check status of an asynchronous operation

**Parameters**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| operation_id | string | Yes | Operation UUID returned from async operations |

**Success Response** (200 OK):
```json
{
  "operation_id": "uuid",
  "operation_type": "install",
  "status": "running",
  "progress": 45,
  "message": "Copying module files...",
  "module_name": "v2_dashboard",
  "instance_id": "uuid",
  "started_at": "2026-01-27T10:00:00Z",
  "updated_at": "2026-01-27T10:01:30Z",
  "completed_at": null,
  "error": null,
  "prompts": []
}
```

**Status Values**:
- `queued`: Operation queued, not started
- `running`: Operation in progress
- `completed`: Operation successful
- `failed`: Operation failed
- `cancelled`: Operation was cancelled

**Example**:
```bash
curl -k https://localhost:8443/modules/status/operation-uuid
```

---

#### POST `/modules/operations/{operation_id}/cancel`
Cancel a running operation

**Parameters**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| operation_id | string | Yes | Operation UUID |

**Success Response** (200 OK):
```json
{
  "success": true,
  "operation_id": "uuid",
  "message": "Cancellation requested"
}
```

---

### Repository Management

#### GET `/repository/list`
List all configured repositories with statistics

**Success Response** (200 OK):
```json
{
  "repositories": [
    {
      "id": "local-module-repository",
      "name": "Local Repository",
      "type": "local",
      "path": "/local_repository",
      "enabled": true,
      "stats": {
        "total_modules": 5,
        "installed_modules": 2
      }
    }
  ]
}
```

**Example**:
```bash
curl -k https://localhost:8443/repository/list
```

---

#### GET `/repository/modules`
List available modules from repositories

**Query Parameters**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| template | string | No | Filter by template (Dashboard, Sensor, Actuator, etc.) |
| repository_id | string | No | Filter by repository ID |

**Success Response** (200 OK):
```json
{
  "modules": [
    {
      "name": "v2_dashboard",
      "hash": "aef036f6",
      "version": "1.0.0",
      "display_name": "Dashboard",
      "description": "System dashboard module",
      "author": "VYRA Team",
      "template": "Dashboard",
      "installed": false,
      "status": "available",
      "repository_id": "local-module-repository",
      "hardware_requirements": {
        "min_memory_mb": 512,
        "min_disk_gb": 1,
        "required_capabilities": []
      }
    }
  ],
  "total": 1,
  "categories": {
    "Dashboard": 1
  }
}
```

**Example**:
```bash
curl -k https://localhost:8443/repository/modules?template=Dashboard
```

---

#### POST `/repository/install-instance`
Install a new instance of a module from repository

**Request Body**:
```json
{
  "module_name": "v2_dashboard",
  "repository_id": "local-module-repository",
  "version": "1.0.0",
  "instance_id": null,
  "set_primary": false
}
```

**Success Response** (200 OK):
```json
{
  "operation_id": "uuid",
  "status": "queued",
  "status_url": "/modules/status/uuid",
  "message": "Installation queued"
}
```

**Example**:
```bash
curl -X POST -k https://localhost:8443/repository/install-instance \
  -H "Content-Type: application/json" \
  -d '{"module_name":"v2_dashboard","repository_id":"local-module-repository","version":"1.0.0"}'
```

---

### Hardware Management

#### POST `/hardware/nodes/register`
Register a hardware node in the cluster

**Request Body**:
```json
{
  "node_id": "node-01",
  "hostname": "server-01",
  "ip_address": "192.168.1.10",
  "role": "worker",
  "capabilities": ["x86_64", "gpu"],
  "labels": {
    "rack": "A1",
    "zone": "production"
  }
}
```

**Success Response** (200 OK):
```json
{
  "success": true,
  "node_id": "node-01",
  "message": "Node registered successfully"
}
```

**Example**:
```bash
curl -X POST -k https://localhost:8443/hardware/nodes/register \
  -H "Content-Type: application/json" \
  -d '{"node_id":"node-01","hostname":"server-01","ip_address":"192.168.1.10","role":"worker"}'
```

---

#### GET `/hardware/nodes`
List all registered hardware nodes

**Success Response** (200 OK):
```json
{
  "nodes": [
    {
      "node_id": "node-01",
      "hostname": "server-01",
      "role": "manager",
      "ip_address": "192.168.1.10",
      "status": "healthy",
      "capabilities": ["x86_64"],
      "resources": {
        "cpu_cores": 8,
        "memory_total_gb": 16,
        "disk_total_gb": 500
      }
    }
  ],
  "total": 1
}
```

**Example**:
```bash
curl -k https://localhost:8443/hardware/nodes
```

---

#### GET `/hardware/nodes/{node_id}`
Get details for a specific hardware node

**Parameters**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| node_id | string | Yes | Node ID |

**Success Response** (200 OK):
```json
{
  "node_id": "node-01",
  "hostname": "server-01",
  "role": "manager",
  "ip_address": "192.168.1.10",
  "status": "healthy",
  "capabilities": ["x86_64"],
  "resources": {
    "cpu_cores": 8,
    "memory_total_gb": 16,
    "memory_available_gb": 8,
    "disk_total_gb": 500,
    "disk_available_gb": 250
  },
  "labels": {},
  "last_seen": "2026-01-27T10:30:00Z"
}
```

---

#### GET `/hardware/nodes/{node_id}/stats`
Get live resource statistics for a node

**Success Response** (200 OK):
```json
{
  "node_id": "node-01",
  "cpu_percent": 45.2,
  "memory_percent": 65.5,
  "disk_percent": 50.0,
  "network_io": {
    "bytes_sent": 1000000,
    "bytes_recv": 2000000
  },
  "timestamp": "2026-01-27T10:30:00Z"
}
```

---

### WebSocket API

#### WS `/ws/operations`
Real-time operation status updates

**Connection**:
```javascript
const ws = new WebSocket('ws://localhost:8443/ws/operations');

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Operation update:', data);
};
```

**Server Messages**:
```json
{
  "type": "operation_update",
  "operation_id": "uuid",
  "status": "running",
  "progress": 45,
  "message": "Installing module...",
  "timestamp": "2026-01-27T10:30:00Z"
}
```

**Message Types**:
- `operation_update`: Status change for an operation
- `operation_completed`: Operation finished successfully
- `operation_failed`: Operation failed with error
- `operation_cancelled`: Operation was cancelled

---

## Data Models

### ModuleInstance
```json
{
  "module_name": "string",
  "instance_id": "string",
  "status": "running|stopped|error",
  "version": "string",
  "is_primary": boolean,
  "container_name": "string",
  "path": "string",
  "created_at": "timestamp",
  "updated_at": "timestamp",
  "permissions": {
    "removable": boolean,
    "updatable": boolean,
    "visible": boolean,
    "multi_instance": boolean
  }
}
```

### OperationStatus
```json
{
  "operation_id": "string",
  "operation_type": "install|update|delete|set_primary",
  "status": "queued|running|completed|failed|cancelled",
  "progress": number,
  "message": "string",
  "module_name": "string",
  "instance_id": "string",
  "started_at": "timestamp",
  "updated_at": "timestamp",
  "completed_at": "timestamp|null",
  "error": "string|null"
}
```

## Error Codes

| HTTP Status | Meaning |
|-------------|---------|
| 200 | Success |
| 400 | Bad Request - Invalid parameters |
| 404 | Not Found - Resource doesn't exist |
| 409 | Conflict - Resource already exists |
| 500 | Internal Server Error |

## Environment Variables

- `API_TITLE`: API title (default: "VYRA Module Manager API")
- `API_VERSION`: API version (default: "0.1.0")
- `DEVELOPMENT_MODE`: Enable development features (default: true)
- `CONTAINER_API_HOST`: Container manager hostname (default: "container-manager")
- `CONTAINER_API_PORT`: Container manager port (default: 8080)

## Testing Examples

### List Instances
```bash
# Inside container (direct access)
curl -k https://localhost:8443/modules/instances

# Via Traefik (after ROS2 initialization)
curl -ki https://localhost/<module_name>/api/modules/instances
```

### Install Module Instance
```bash
curl -X POST -k https://localhost:8443/repository/install-instance \
  -H "Content-Type: application/json" \
  -d '{
    "module_name": "v2_dashboard",
    "repository_id": "local-module-repository",
    "version": "1.0.0",
    "set_primary": true
  }'
```

### Check Operation Status
```bash
# Get operation_id from install response
OPERATION_ID="uuid-from-response"
curl -k https://localhost:8443/modules/status/$OPERATION_ID
```

### Set Primary Instance
```bash
curl -X POST -k https://localhost:8443/modules/primary/v2_dashboard/new-instance-uuid
```

## Integration with Container Manager

The Module Manager backend communicates with the Container Manager API for:
- Module installation (calls `/modules/install`)
- Module removal (calls `/modules/{name}/{id}`)
- Hardware node information (calls `/hardware/info`)
- Service management (calls `/services/{id}/action`)

**Container Manager Client**:  
See `src/rest_api/api/container_manager_client.py` for Python client implementation.

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 0.1.0 | 2026-01-27 | Initial release with module management, repository, and hardware APIs |
