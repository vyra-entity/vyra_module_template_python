"""
Alembic migration environment for {{ module_name }}.

This file is rendered by copier during module creation.

DB-URL resolution order
------------------------
1. ``DATABASE_URL`` environment variable
2. ``/workspace/storage/{{ module_name }}.sqlite`` (container default)
3. ``alembic.ini`` ``sqlalchemy.url`` value (fallback for local dev)
"""

from __future__ import annotations

import configparser
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

HERE = Path(__file__).resolve().parent.parent  # src/{{ module_name }}/
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# ---------------------------------------------------------------------------
# Import ALL module models so their tables are registered in Base.metadata.
# Add imports here as new tables are created.
# ---------------------------------------------------------------------------
from vyra_base.storage.tb_base import Base  # noqa: E402
# Example:
# from {{ module_name }}.application.tb_example import ExampleModel  # noqa: F401,E402

target_metadata = Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_db_url() -> str:
    env_url = os.environ.get("DATABASE_URL", "").strip()
    if env_url:
        return env_url.replace("sqlite+aiosqlite", "sqlite")

    # Try workspace-level config first (runtime path)
    workspace_config = Path("/workspace") / "config" / "storage_config.ini"
    if workspace_config.exists():
        cfg = configparser.ConfigParser()
        cfg.read(workspace_config)
        if "sqlite" in cfg:
            db_path = cfg["sqlite"].get("path", "/workspace/storage/")
            db_name = cfg["sqlite"].get("database", "{{ module_name }}.sqlite")
            db_name = db_name.replace("${module_name}", "{{ module_name }}")
            return f"sqlite:///{db_path}{db_name}"

    # Fall back to development resource path
    ini_path = HERE / "resource" / "storage_config.ini"
    if ini_path.exists():
        cfg = configparser.ConfigParser()
        cfg.read(ini_path)
        if "sqlite" in cfg:
            db_path = cfg["sqlite"].get("path", "/workspace/storage/")
            db_name = cfg["sqlite"].get("database", "{{ module_name }}.sqlite")
            db_name = db_name.replace("${module_name}", "{{ module_name }}")
            return f"sqlite:///{db_path}{db_name}"

    return "sqlite:////workspace/storage/{{ module_name }}.sqlite"


DB_URL = _resolve_db_url()
config.set_main_option("sqlalchemy.url", DB_URL)


def run_migrations_offline() -> None:
    context.configure(
        url=DB_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
