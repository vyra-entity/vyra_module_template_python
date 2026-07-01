"""Settings router exposing module permission parameters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
import yaml

from ..auth.router import get_current_user

router = APIRouter(prefix="/settings", tags=["Settings"])


def _is_admin(user: dict[str, Any]) -> bool:
    """Return True when the provided user claims include the admin role."""
    if str(user.get("username", "")).lower() == "admin":
        return True
    role = user.get("role")
    if isinstance(role, str) and role.lower() == "admin":
        return True
    roles = user.get("roles")
    if isinstance(roles, list):
        return any(str(item).lower() == "admin" for item in roles)
    return False


def _resolve_module_params_path() -> Path:
    """Locate ``.module/module_params.yaml`` in runtime/workspace locations."""
    candidates = [
        Path("/workspace/.module/module_params.yaml"),
        Path.cwd() / ".module/module_params.yaml",
    ]
    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / ".module/module_params.yaml")

    for path in candidates:
        if path.exists() and path.is_file():
            return path

    raise HTTPException(status_code=404, detail="module_params.yaml not found")


def _load_module_params(path: Path) -> dict[str, Any]:
    """Read and validate module params YAML payload."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read module_params.yaml: {exc}")
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid YAML in module_params.yaml: {exc}")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="module_params.yaml root must be a mapping")
    return payload


def _write_module_params(path: Path, payload: dict[str, Any]) -> None:
    """Write updated module params YAML payload."""
    try:
        with path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=False)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write module_params.yaml: {exc}")


def _resolve_module_data_path() -> Path | None:
    """Locate ``.module/module_data.yaml`` in runtime/workspace locations."""
    candidates = [
        Path("/workspace/.module/module_data.yaml"),
        Path.cwd() / ".module/module_data.yaml",
    ]
    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / ".module/module_data.yaml")

    for path in candidates:
        if path.exists() and path.is_file():
            return path
    return None


def _load_about_info() -> dict[str, Any]:
    """Return module metadata from ``.module/module_data.yaml``."""
    info: dict[str, Any] = {}
    data_path = _resolve_module_data_path()
    if data_path is None:
        return info

    try:
        with data_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return info

    if isinstance(data, dict):
        for key in (
            "name",
            "display_name",
            "version",
            "description",
            "author",
            "blueprints",
            "uuid",
        ):
            if key in data:
                info[key] = data[key]
    return info


@router.get("/about")
async def get_module_about(
    _user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Return module metadata for the About page."""
    return {"success": True, "module": _load_about_info()}


@router.get("/permissions")
async def get_module_permissions(
    _user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the ``permissions`` mapping from ``.module/module_params.yaml``."""
    params_path = _resolve_module_params_path()
    params = _load_module_params(params_path)
    permissions = params.get("permissions") or {}
    if not isinstance(permissions, dict):
        raise HTTPException(status_code=500, detail="permissions section must be a mapping")
    return {"success": True, "permissions": permissions}


@router.put("/permissions")
async def update_module_permissions(
    body: dict[str, Any],
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Persist updated ``permissions`` mapping for admin users."""
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")

    permissions = body.get("permissions")
    if not isinstance(permissions, dict):
        raise HTTPException(status_code=400, detail="Body must include a 'permissions' object")

    params_path = _resolve_module_params_path()
    params = _load_module_params(params_path)
    params["permissions"] = permissions
    _write_module_params(params_path, params)
    return {"success": True, "permissions": permissions}
