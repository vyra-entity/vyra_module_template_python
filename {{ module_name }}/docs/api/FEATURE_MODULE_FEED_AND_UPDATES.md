# Module Feed & Update System - Implementation Summary

## Overview

This document describes the implementation of two major features:
1. **Module Update System** - Check for updates across repositories and update modules
2. **Module Feed System** - Real-time streaming of module status/error/news/warning messages

## Architecture

## Architektur-Flow

```
┌─────────────────┐
│  ROS2 Modules   │
│  (StateFeeder,  │
│  ErrorFeeder,   │
│  NewsFeeder)    │
└────────┬────────┘
         │ ROS2 Topics: /{namespace}/feeder/{state|error|news}
         ↓
┌─────────────────────────┐
│  ModuleInfoReader       │
│  (listen_vyra_speaker)  │
└────────┬────────────────┘
         │ Callbacks
         ↓
┌─────────────────────────┐
│  application.py         │
│  (feed_callback)        │
└────────┬────────────────┘
         │ entity.redis_access.publish()
         ↓
┌─────────────────────────┐
│  Redis Pub/Sub          │
│  Channel:               │
│  "module_feed_channel"  │
└────────┬────────────────┘
         │ Direct Listener Callback
         ↓
┌─────────────────────────┐
│  WebSocket Router       │
│  (redis_module_feed_    │
│   subscriber)           │
└────────┬────────────────┘
         │ broadcast_module_feed()
         ↓
┌─────────────────────────┐
│  WebSocket Clients      │
│  /ws/module_feed        │
└────────┬────────────────┘
         │ WebSocket Message
         ↓
┌─────────────────────────┐
│  Frontend               │
│  useModuleFeed()        │
└────────┬────────────────┘
         │ Pinia Actions
         ↓
┌─────────────────────────┐
│  Pinia Store            │
│  moduleFeed Store       │
└─────────────────────────┘
         │
         ↓
    UI Components
    (Home, Monitoring, ModulesView)
```

### Key Architecture Decision

**Problem**: The ROS2 node (application.py) and REST API (rest_api/) run in separate processes and cannot share Python imports.

**Solution**: Use Redis pub/sub as the communication bridge:
- ROS2 node publishes to Redis channel: `module_feed_channel`
- REST API subscribes to Redis and forwards to WebSocket clients

## Feature 1: Module Update System

### Backend Implementation

#### Files Modified:
- `src/rest_api/module/service.py` - Added `check_updates()` method
- `src/rest_api/module/router.py` - Added `GET /modules/updates/check` endpoint
- `src/rest_api/module/schemas.py` - Added `UpdateInfo`, `UpdatesCheckResponse` schemas

#### Logic:
1. Scans all registered repositories for each module
2. Compares installed version with available versions in repositories
3. Returns updates available per instance with repository details

### Frontend Implementation

#### Files Created/Modified:
- `frontend/src/components/UpdateModal.vue` - Repository selection UI
- `frontend/src/features/modules/api/module.api.ts` - Added `checkUpdates()` method
- `frontend/src/features/modules/types/common.ts` - Added `available_updates` to interface
- `frontend/src/features/modules/ModulesView.vue` - Integrated update checking

#### Features:
- Auto-check for updates on mount
- Visual indicator (update icon) when updates available
- Repository selection modal with version display
- Option to update all instances simultaneously

## Feature 2: Module Feed System

### Backend Implementation

#### 1. ModuleInfoReader (ROS2 Layer)
**File**: `src/{{ module_name }}/{{ module_name }}/application/module_info_reader.py`

**Purpose**: Subscribe to ROS2 feeder topics from all registered modules

**Key Methods**:
- `start_reading()` - Query database for registered modules and subscribe
- `_subscribe_to_module_feeds()` - Create ROS2 subscriptions for 4 feed types
- `_handle_*_callback()` - Parse messages and publish to Redis

**ROS2 Topics Pattern**:
```python
status_topic = f"/{namespace}/feeder/status"
error_topic = f"/{namespace}/feeder/error"
news_topic = f"/{namespace}/feeder/news"
warning_topic = f"/{namespace}/feeder/warning"
```

**Namespace Resolution**:
```python
namespace = module.namespace or module_name
```
Falls back to module name if database `namespace` field is empty.

**Message Handling**:
```python
# Parse std_msgs/String with JSON payload
feed_data = json.loads(msg.data)

# Publish to Redis
self.entity.storage.publish(
    "module_feed_channel",
    json.dumps({
        "module_id": module_id,
        "module_name": module_name,
        "feed_type": "status",
        "message": feed_data,
        "timestamp": datetime.now().isoformat()
    })
)
```

#### 2. Application Integration
**File**: `src/{{ module_name }}/{{ module_name }}/application/application.py`

**Changes**:
- Initialize `ModuleInfoReader` on startup
- Register callback using Redis pub/sub (NOT direct rest_api import)
- Start reading feeds when component is ready

```python
# ✅ CORRECT: Use Redis for IPC
self._module_info_reader = ModuleInfoReader(
    entity=self.entity,
    db_manipulator=self.db_manipulator,
    on_feed_received=lambda feed_msg: asyncio.create_task(
        self._handle_feed_message(feed_msg)
    )
)

async def _handle_feed_message(self, feed_msg):
    """Publish to Redis instead of direct rest_api import"""
    await self.entity.storage.publish(
        "module_feed_channel",
        json.dumps(feed_msg.to_dict())
    )
```

#### 3. WebSocket Router (REST API Layer)
**File**: `src/rest_api/websocket/router.py`

**Key Components**:
- `redis_module_feed_subscriber()` - Background task subscribing to Redis
- `broadcast_module_feed()` - Send messages to all connected WebSocket clients
- `start_redis_subscriber()` / `stop_redis_subscriber()` - Lifecycle management

**Redis Subscription**:
```python
redis_client = await redis_service.get_client()
pubsub = redis_client._redis.pubsub()
await pubsub.subscribe("module_feed_channel")

while True:
    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
    if message and message["type"] == "message":
        feed_data = json.loads(message["data"].decode("utf-8"))
        await broadcast_module_feed(feed_data)
```

#### 4. Main Application Lifecycle
**File**: `src/rest_api/main_rest.py`

**Changes**: Start/stop Redis subscriber in lifespan context manager

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await start_redis_subscriber()
    yield
    # Shutdown
    await stop_redis_subscriber()
```

### Frontend Implementation

#### 1. WebSocket Composable
**File**: `frontend/src/composables/useModuleFeed.ts`

**Features**:
- Auto-connect to WebSocket endpoint `/ws/module_feed`
- Ping/pong keepalive mechanism
- Feed history tracking with reactive refs
- Filter methods: `getStatusFeeds()`, `getErrorFeeds()`, etc.

**Usage**:
```typescript
const moduleFeed = useModuleFeed()

onMounted(() => {
  moduleFeed.connect()
})

// Access feeds
const statusFeeds = moduleFeed.getStatusFeeds('module_id')
const errorFeeds = moduleFeed.getErrorFeeds()
```

#### 2. ModulesView Integration
**File**: `frontend/src/features/modules/ModulesView.vue`

**Changes**:
- Import and initialize `useModuleFeed()`
- Display feed indicators (future: add UI elements)

## Namespace Behavior

### How Namespace Works:

1. **Database Field**: `registered_modules.namespace` (nullable)
2. **Resolution Logic**: `namespace = module.namespace or module_name`
3. **Topic Construction**: `f"/{namespace}/feeder/status"`

### Example:

**Module in Database**:
```json
{
  "id": "abc123",
  "name": "v2_dashboard",
  "namespace": "dashboard_ns"  // or null
}
```

**Resulting Topics**:
- If namespace is set: `/dashboard_ns/feeder/status`
- If namespace is null: `/v2_dashboard/feeder/status`

### Verification in vyra_base:

VyraEntity creates ROS2 node with name:
```python
node_settings = NodeSettings(
    name=f"{self.module_entry.name}_{self.module_entry.uuid}"
)
```

ROS2 nodes don't have explicit namespace in VyraEntity initialization, so the namespace field in database is custom metadata used specifically for topic subscription.

## Message Format

### ROS2 Message (std_msgs/String):
```json
{
  "data": "{\"state\": \"running\", \"timestamp\": \"2024-01-23T10:00:00\"}"
}
```

### Redis Pub/Sub Message:
```json
{
  "module_id": "abc123",
  "module_name": "v2_dashboard",
  "feed_type": "status",
  "message": {
    "state": "running",
    "timestamp": "2024-01-23T10:00:00"
  },
  "timestamp": "2024-01-23T10:00:01"
}
```

### WebSocket Message:
```json
{
  "type": "module_feed",
  "data": {
    "module_id": "abc123",
    "module_name": "v2_dashboard",
    "feed_type": "status",
    "message": {...},
    "timestamp": "2024-01-23T10:00:01"
  }
}
```

## Testing Plan

### Unit Tests:
- [ ] ModuleInfoReader subscription logic
- [ ] Message parsing and Redis publishing
- [ ] WebSocket broadcast functionality

### Integration Tests:
- [ ] ROS2 topic subscription with real messages
- [ ] Redis pub/sub communication
- [ ] WebSocket connection and message delivery

### End-to-End Tests:
1. Deploy module in Docker Swarm
2. Verify ROS2 topics are created: `ros2 topic list | grep feeder`
3. Publish test message: `ros2 topic pub /{namespace}/feeder/status std_msgs/String "data: '{\"test\":\"message\"}'"`
4. Verify message appears in frontend

## Deployment

### Docker Swarm:
```bash
cd /home/holgder/VOS2_WORKSPACE/tools
./reload_docker_swarm.sh
```

### Service Logs:
```bash
docker service logs vos2_ws_{{ module_name }} -f
```

### Debugging:
```bash
# Check ROS2 topics
docker exec -it $(docker ps -qf "name={{ module_name }}") bash
ros2 topic list | grep feeder

# Check Redis pub/sub
docker exec -it $(docker ps -qf "name=redis") redis-cli
SUBSCRIBE module_feed_channel

# Check WebSocket connection
wscat -c ws://localhost/ws/module_feed
```

## Known Limitations

1. **Namespace Discovery**: Currently relies on database field, may need enhancement if modules don't set it
2. **Scalability**: All module feeds go through single Redis channel, may need sharding for large deployments
3. **Message Persistence**: Feeds are ephemeral, no history beyond in-memory frontend cache

## Future Enhancements

- [ ] Add UI components to display feeds in ModulesView
- [ ] Implement feed filtering by feed_type
- [ ] Add feed history persistence (Redis Streams or TimescaleDB)
- [ ] Add feed search and export functionality
- [ ] Implement feed-based alerting rules
- [ ] Add namespace auto-discovery from ROS2 node introspection

## References

- **API Documentation**: See `/api/docs` for OpenAPI schema
- **ROS2 Topics**: Standard VYRA feeder topic pattern
- **Redis Pub/Sub**: vyra_base RedisClient implementation
- **WebSocket Protocol**: FastAPI WebSocket with JSON messages
