"""
Plugin Router — Communication endpoints for the plugin system.

These endpoints are registered in {{ module_name }}.

Endpoints:
  GET    /plugin/assets/{plugin_id}/{version}/{path} — Asset proxy (JS/CSS/WASM/SVG)
  GET    /plugin/resolve_plugins                     — UI slot manifest (scope-based)
  POST   /plugin/{plugin_id}/call                   — Generic plugin function call (WASM)

"""

from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .models import (
    PluginCallRequest,
    PluginCallResponse,
    PluginListEntry,
    UiManifestEntry,
    UiManifestResponse,
)
from ...container_injection import provide_plugin_gateway, ContainerNotInitializedError
from vyra_base.plugin.runtime import PluginCallError

logger = logging.getLogger(__name__)

router = APIRouter()

_LOCAL_REPO = Path(os.getenv("LOCAL_REPOSITORY_PATH", "/local_repository"))
# Plugin pool is mounted at /host/plugin_pool in Docker Swarm (see docker-compose.modules.yml).
# Fall back to /plugin_pool for non-containerised environments.
_POOL_PATH = Path(os.getenv("PLUGIN_POOL_PATH", "/host/plugin_pool"))


# ---------------------------------------------------------------------------
# GET /assets/ — Asset proxy
# ---------------------------------------------------------------------------


@router.get(
    "/assets/{plugin_id}/{version}/{file_path:path}",
    summary="Asset proxy: plugin files (JS, CSS, WASM, SVG)",
)
async def serve_plugin_asset(plugin_id: str, version: str, file_path: str):
    """
    Streams plugin assets from the NFS pool (installed plugins) or the local
    repository (available but not yet installed).  Sets the correct MIME type.
    """
    pool_asset = _POOL_PATH / plugin_id / version / file_path
    repo_asset = _LOCAL_REPO / "plugins" / plugin_id / version / file_path

    if pool_asset.exists():
        asset_path = pool_asset
        base_for_check = _POOL_PATH
    elif repo_asset.exists():
        asset_path = repo_asset
        base_for_check = _LOCAL_REPO
    else:
        raise HTTPException(
            status_code=404,
            detail=f"Asset not found: plugins/{plugin_id}/{version}/{file_path}",
        )

    # Security: prevent path traversal
    try:
        asset_path.resolve().relative_to(base_for_check.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    mime_type, _ = mimetypes.guess_type(str(asset_path))
    ext = asset_path.suffix.lower()
    if ext == ".js":
        mime_type = "application/javascript"
    elif ext == ".wasm":
        mime_type = "application/wasm"
    elif ext == ".css":
        mime_type = "text/css"
    elif ext == ".svg":
        mime_type = "image/svg+xml"
    elif not mime_type:
        mime_type = "application/octet-stream"

    content = asset_path.read_bytes()

    # Versioned assets from the pool (released, immutable path) get long-term
    # caching.  Assets served from the local repository (dev/editable builds)
    # must never be cached so a rebuild is immediately visible without a hard
    # refresh.
    if asset_path.is_relative_to(_POOL_PATH):
        cache_header = "public, max-age=31536000, immutable"
    else:
        cache_header = "no-store"

    return Response(
        content=content,
        media_type=mime_type,
        headers={
            "Cache-Control": cache_header,
            "Content-Length": str(len(content)),
        },
    )


# ---------------------------------------------------------------------------
# GET /resolve_plugins — Slot manifest (scope-based, via PluginGateway)
# ---------------------------------------------------------------------------


@router.get(
    "/resolve_plugins",
    response_model=UiManifestResponse,
    summary="UI slot manifest (scope-based)",
)
async def resolve_plugins(
    scope_type: str = Query(default="MODULE", description="GLOBAL, TEMPLATE, MODULE, or INSTANCE"),
    scope_target: str | None = Query(default=None, description="Scope target"),
    module_name: str | None = Query(default=None, description="Requesting module name"),
    module_id: str | None = Query(default=None, description="Requesting module instance ID"),
    p_id: str | None = Query(default=None, description="Optional plugin pool ID filter"),
    gateway=Depends(provide_plugin_gateway),
):
    """
    Return the manifest of all active UI slot components for the given scope.

    Delegates to PluginGateway.resolve_plugins() which, when running inside
    v2_modulemanager, calls PluginManager._resolve_plugins_impl() directly
    (no Zenoh round-trip).  Consumer modules use the Zenoh transport path.
    """
    try:
        result = await gateway.resolve_plugins(
            scope_type=scope_type,
            scope_target=scope_target,
            module_name=module_name,
            module_id=module_id,
            p_id=p_id,
        )
        return result
    except ContainerNotInitializedError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("resolve_plugins error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Error: {exc}")


# ---------------------------------------------------------------------------
# POST /{plugin_id}/call — Generic WASM plugin call
# ---------------------------------------------------------------------------


@router.post(
    "/{plugin_id}/call",
    response_model=PluginCallResponse,
    summary="Generic plugin function call (WASM runtime via PluginGateway)",
)
async def call_plugin_function(
    plugin_id: str,
    body: PluginCallRequest,
    gateway=Depends(provide_plugin_gateway),
):
    """
    Call any exported function of an installed plugin.
    The NFS path is looked up internally by PluginGateway; the runtime is
    lazily initialised on first call.
    """
    try:
        result = await gateway.call_plugin(
            plugin_id=plugin_id,
            function_name=body.function_name,
            data=body.data,
        )
    except PluginCallError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.error("Plugin call failed [%s.%s]: %s", plugin_id, body.function_name, exc)
        raise HTTPException(status_code=500, detail=f"Plugin error: {exc}")

    return PluginCallResponse(
        plugin_id=plugin_id,
        function_name=body.function_name,
        result=result,
        success=True,
    )
