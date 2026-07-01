#!/usr/bin/env python3
"""
tools/update_manifest.py

Syncs module metadata from pyproject.toml into .module/module_data.yaml.

Fields written:
  name        ← [tool.poetry].name
  description ← [tool.poetry].description
  version     ← [tool.poetry].version
  template    ← [tool.vyra].module_template
  author      ← [tool.poetry].authors[0]

Fields preserved (never overwritten):
  uuid        ← instance-specific UUID set during deployment

Usage:
  python3 tools/update_manifest.py [pyproject.toml] [.module/module_data.yaml]

  Defaults:
    pyproject.toml       → <workspace>/pyproject.toml
    module_data.yaml     → <workspace>/.module/module_data.yaml

  The workspace root is the directory that contains this script's parent (tools/).
"""

from __future__ import annotations

import sys
import os
from pathlib import Path


# ---------------------------------------------------------------------------
# TOML loading  (tomllib ≥3.11 → tomli → minimal regex fallback)
# ---------------------------------------------------------------------------


def _load_toml(path: Path) -> dict:
    """Load a TOML file with a best-effort strategy."""
    # Python 3.11+ stdlib
    try:
        import tomllib  # type: ignore[import]

        with open(path, "rb") as f:
            return tomllib.load(f)
    except (ImportError, ModuleNotFoundError):
        pass

    # pip install tomli  (common on older Pythons)
    try:
        import tomli as tomllib  # type: ignore[import, no-redef]

        with open(path, "rb") as f:
            return tomllib.load(f)
    except (ImportError, ModuleNotFoundError):
        pass

    # ---- minimal regex-based fallback ----
    import re

    data: dict = {}
    current_section: list[str] = []

    def _set(root: dict, keys: list[str], value: object) -> None:
        d = root
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value

    def _get(root: dict, keys: list[str]) -> object:
        d: object = root
        for k in keys:
            if not isinstance(d, dict):
                return {}
            d = d.get(k, {})
        return d

    with open(path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip()

            # Section header  [tool.poetry]  or  [[array]]
            m = re.match(r"^\[{1,2}([^\]]+)\]{1,2}$", line)
            if m:
                current_section = [p.strip() for p in m.group(1).split(".")]
                existing = _get(data, current_section)
                if not isinstance(existing, dict):
                    _set(data, current_section, {})
                continue

            if not current_section:
                continue

            # key = value
            m = re.match(r"^(\w+)\s*=\s*(.+)$", line)
            if not m:
                continue

            key, raw_val = m.group(1), m.group(2).strip()

            # String
            if (raw_val.startswith('"') and raw_val.endswith('"')) or (
                raw_val.startswith("'") and raw_val.endswith("'")
            ):
                val: object = raw_val[1:-1]
            # Inline array of strings  ["a", "b"]
            elif raw_val.startswith("[") and raw_val.endswith("]"):
                val = [
                    item.strip().strip('"').strip("'")
                    for item in raw_val[1:-1].split(",")
                    if item.strip()
                ]
            else:
                val = raw_val

            _set(data, current_section + [key], val)

    return data


# ---------------------------------------------------------------------------
# YAML loading / writing  (PyYAML, always available in VYRA modules)
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    import yaml

    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(path: Path, data: dict) -> None:
    import yaml

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(pyproject_path: Path, module_data_path: Path) -> int:
    print("=== update_manifest.py ===")
    print(f"📄 Source: {pyproject_path}")
    print(f"📄 Target: {module_data_path}")

    if not pyproject_path.exists():
        print(f"⚠️  pyproject.toml not found at {pyproject_path} – skipping")
        return 0

    if not module_data_path.exists():
        print(f"⚠️  module_data.yaml not found at {module_data_path} – skipping")
        return 0

    toml = _load_toml(pyproject_path)
    poetry = toml.get("tool", {}).get("poetry", {})
    vyra = toml.get("tool", {}).get("vyra", {})

    name = str(poetry.get("name", ""))
    version = str(poetry.get("version", "0.0.0"))
    description = str(poetry.get("description", ""))
    authors = poetry.get("authors", [])
    author = authors[0] if isinstance(authors, list) and authors else str(authors or "")
    template = str(vyra.get("module_template", ""))

    # Preserve uuid, alias and blueprints (never overwritten by this script)
    existing = _load_yaml(module_data_path)
    uuid = existing.get("uuid", "")
    alias = existing.get("alias", "")
    blueprints = existing.get("blueprints", "")

    new_data = {
        "name": name,
        "description": description,
        "version": version,
        "template": template,
        "author": author,
        "uuid": uuid,
        "alias": alias,
        "blueprints": blueprints,
    }

    _write_yaml(module_data_path, new_data)

    print(f"✅ module_data.yaml updated:")
    print(f"   name={name}  version={version}  template={template}")
    print(f"   author={author}  blueprints={blueprints}")
    print(f"   uuid={uuid}  alias={alias}  (preserved)")
    print("=== update_manifest.py done ===")
    return 0


if __name__ == "__main__":
    _script_dir = Path(__file__).resolve().parent
    _workspace = _script_dir.parent

    _pyproject = Path(sys.argv[1]) if len(sys.argv) > 1 else _workspace / "pyproject.toml"
    _module_data = (
        Path(sys.argv[2]) if len(sys.argv) > 2 else _workspace / ".module" / "module_data.yaml"
    )

    sys.exit(run(_pyproject, _module_data))
