import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

VYRA_SLIM = os.getenv("VYRA_SLIM", "false").lower() == "true"
if not VYRA_SLIM:
    from ament_index_python.packages import get_package_share_directory  # pyright: ignore[reportMissingImports]

from vyra_base.core.entity import VyraEntity
from vyra_base.defaults.entries import (
    FunctionConfigEntry,
    FunctionConfigDisplaystyle,
    FunctionConfigBaseTypes,
    FunctionConfigTags,
    ModuleEntry,
    StateEntry,
    NewsEntry,
    ErrorEntry,
)
from vyra_base.helper.file_reader import FileReader
from vyra_base.helper.file_writer import FileWriter

# Import logger
from .logging_config import get_logger, log_exception, log_function_call, log_function_result

if __package__:
    PACKAGE_NAME = __package__.split(".")[0]
else:
    sys.exit("Package name not found. Please run this script as part of a package.")


logger = get_logger(__name__)


def _get_package_dir(package_name: str) -> Path:
    """Return the share/resource directory for a package.

    In SLIM mode the Python source tree is used directly (no ROS2 install).
    In FULL mode the standard ROS2 ament index is queried.
    """
    if VYRA_SLIM:
        # Try source tree first (development / direct run)
        candidate = Path(__file__).parent.parent.parent.parent / "src" / package_name
        if candidate.exists():
            return candidate
        # Fall back to src/ relative to workspace root (colcon install)
        return _get_workspace_root() / "src" / package_name
    return Path(get_package_share_directory(package_name))


def _get_workspace_root() -> Path:
    """Return the workspace root directory.

    Used in both SLIM and FULL modes to locate workspace-level files
    such as pyproject.toml and .module/module_data.yaml.
    """
    if VYRA_SLIM:
        # When running from source (e.g. src/<pkg>/<pkg>/_base_.py), the
        # four-ancestor walk points directly at the workspace root.
        # When running from the colcon install tree the walk resolves to
        # install/<pkg>/lib/ which is wrong — fall back to cwd() which
        # startup_slim_core.sh guarantees is /workspace.
        candidate = Path(__file__).parent.parent.parent.parent
        if (candidate / "pyproject.toml").exists():
            return candidate
        return Path.cwd()
    return Path(get_package_share_directory(PACKAGE_NAME)).parents[3]


async def load_resource(package_name: str, resource_name: Path) -> Any:
    """
    Load a resource file from a VYRA package.

    Supports .ini, .json, .yaml, and .yml files.
    In SLIM mode the Python source tree is searched; in FULL mode the ROS2
    ament package share directory is used.

    Args:
        package_name: Name of the package
        resource_name: Path to the resource relative to the package directory

    Returns:
        Parsed resource content

    Raises:
        FileNotFoundError: If resource file doesn't exist
        ValueError: If file type is not supported
    """
    log_function_call(
        logger,
        function="load_resource",
        package=package_name,
        resource=str(resource_name),
        file_type=resource_name.suffix,
    )

    try:
        package_path = _get_package_dir(package_name)
        resource_path: Path = package_path / resource_name

        if not resource_path.exists():
            logger.error(
                "resource_not_found",
                package=package_name,
                resource=str(resource_name),
                full_path=str(resource_path),
            )
            raise FileNotFoundError(f"Resource {resource_name} not found in package {package_name}")

        logger.debug(
            "loading_resource",
            package=package_name,
            path=str(resource_path),
            file_type=resource_name.suffix,
        )

        result = None
        match resource_name.suffix:
            case ".ini":
                result = await FileReader.open_ini_file(resource_path)
            case ".json":
                result = await FileReader.open_json_file(resource_path)
            case ".yaml" | ".yml":
                result = await FileReader.open_yaml_file(resource_path)
            case _:
                logger.error(
                    "unsupported_file_type",
                    file_type=resource_name.suffix,
                    supported_types=[".ini", ".json", ".yaml", ".yml"],
                )
                raise ValueError(
                    f"Unsupported file type: {resource_name.suffix}. "
                    "Supported types are .ini, .json, .yaml/.yml"
                )

        log_function_result(
            logger,
            success=True,
            function="load_resource",
            package=package_name,
            resource=str(resource_name),
            data_size=len(str(result)) if result else 0,
        )

        return result

    except Exception as e:
        log_exception(
            logger,
            e,
            context={
                "function": "load_resource",
                "package": package_name,
                "resource": str(resource_name),
            },
        )
        raise


async def _create_base_interfaces() -> list[FunctionConfigEntry]:
    """
    Create base interface configurations from metadata files.

    Loads interface metadata from JSON files and creates FunctionConfigEntry objects
    for ROS2 services, topics, and actions.

    Returns:
        List of configured interface entries

    Raises:
        KeyError: If required field is missing in interface metadata
        TypeError: If interface data has type errors
    """
    log_function_call(logger, function="_create_base_interfaces")

    interface_metadata: list = []

    import vyra_base as _vyra_base_module

    module_name = os.getenv("MODULE_NAME", "")
    interfaces_pkg = f"{module_name}_interfaces"

    # Dynamically discover all *.meta.json files from the installed config directory
    try:
        config_dir = _get_package_dir(interfaces_pkg) / "config"
        all_meta_files = sorted(config_dir.glob("*.meta.json"))
    except Exception as e:
        log_exception(
            logger, e, context={"operation": "discover_meta_files", "package": interfaces_pkg}
        )
        raise

    # Validate against schema — warn and skip invalid files
    valid_files, invalid_files = _vyra_base_module.validate_config_schema(all_meta_files)

    for bad_file, reason in invalid_files:
        logger.warning(
            "invalid_meta_json_skipped", file=bad_file.name, reason=reason, package=interfaces_pkg
        )

    logger.debug(
        "loading_base_interfaces",
        module_name=module_name,
        discovered=len(all_meta_files),
        valid=len(valid_files),
        invalid=len(invalid_files),
    )

    for meta_file in valid_files:
        try:
            logger.debug("loading_interface_metadata", module=module_name, file=meta_file.name)
            metadata = await load_resource(interfaces_pkg, Path("config", meta_file.name))
            interface_metadata.extend(metadata)
            logger.debug(
                "interface_metadata_loaded", file=meta_file.name, entries_count=len(metadata)
            )
        except Exception as e:
            log_exception(
                logger, e, context={"operation": "load_interface_metadata", "file": meta_file.name}
            )
            raise

    interface_functions: list[FunctionConfigEntry] = []

    logger.debug("processing_interface_metadata", total_entries=len(interface_metadata))

    for idx, metadata in enumerate(interface_metadata):
        try:
            if metadata["type"] == FunctionConfigBaseTypes.service.value:
                ifaces = []
                logger.debug(
                    "processing_callable_interface",
                    index=idx,
                    function_name=metadata.get("functionname", "unknown"),
                    filetypes=metadata.get("filetype", []),
                )

                for iface_type in metadata["filetype"]:
                    filename, filetype = iface_type.split(".")

                    if filetype in ["msg", "srv", "action"]:
                        try:
                            iface_module = sys.modules[f"{module_name}_interfaces.{filetype}"]
                            iface_class = getattr(iface_module, filename)
                            ifaces.append(iface_class)
                            logger.debug(
                                "interface_type_loaded",
                                filename=filename,
                                filetype=filetype,
                                class_name=iface_class.__name__,
                            )
                        except (KeyError, AttributeError) as e:
                            logger.error(
                                "interface_type_load_failed",
                                filename=filename,
                                filetype=filetype,
                                module=f"{module_name}_interfaces.{filetype}",
                                error=str(e),
                            )
                            raise
                    else:
                        ifaces.append(iface_type)
                        logger.debug("custom_interface_type", iface_type=iface_type)

                metadata["interfacetypes"] = ifaces

                displaystyle = FunctionConfigDisplaystyle(
                    visible=metadata.get("displaystyle", {}).get("visible", False),
                    published=metadata.get("displaystyle", {}).get("published", False),
                )

                interface_entry = FunctionConfigEntry(
                    tags=[
                        FunctionConfigTags(t)
                        for t in metadata.get("tags", [])
                        if t in FunctionConfigTags._value2member_map_
                    ],
                    type=metadata["type"],
                    interfacetypes=metadata["interfacetypes"],
                    functionname=metadata["functionname"],
                    displayname=metadata["displayname"],
                    description=metadata["description"],
                    displaystyle=displaystyle,
                    params=metadata["params"],
                    returns=metadata["returns"],
                    namespace=metadata.get("namespace", None),
                    qosprofile=metadata.get("qosprofile", 10),
                    callbacks=None,
                    periodic=None,
                )
                interface_functions.append(interface_entry)

                logger.debug(
                    "interface_entry_created",
                    function_name=metadata["functionname"],
                    interface_types_count=len(ifaces),
                )

            elif metadata["type"] in (
                FunctionConfigBaseTypes.message.value,
                FunctionConfigBaseTypes.action.value,
            ):
                # Publisher (message) and Action types — no service callbacks,
                # registered as-is so the entity can bind them when ready.
                logger.debug(
                    "processing_non_service_interface",
                    index=idx,
                    type=metadata["type"],
                    function_name=metadata.get("functionname", "unknown"),
                )
                displaystyle = FunctionConfigDisplaystyle(
                    visible=metadata.get("displaystyle", {}).get("visible", False),
                    published=metadata.get("displaystyle", {}).get("published", False),
                )
                interface_entry = FunctionConfigEntry(
                    tags=[
                        FunctionConfigTags(t)
                        for t in metadata.get("tags", [])
                        if t in FunctionConfigTags._value2member_map_
                    ],
                    type=metadata["type"],
                    interfacetypes=metadata.get("interfacetypes", None),
                    functionname=metadata["functionname"],
                    displayname=metadata["displayname"],
                    description=metadata["description"],
                    displaystyle=displaystyle,
                    params=metadata.get("params", []),
                    returns=metadata.get("returns", []),
                    namespace=metadata.get("namespace", None),
                    qosprofile=metadata.get("qosprofile", 10),
                    callbacks=None,
                    periodic=None,
                )
                interface_functions.append(interface_entry)
                logger.debug(
                    "interface_entry_created",
                    function_name=metadata["functionname"],
                    type=metadata["type"],
                )

            else:
                logger.warning(
                    "unknown_interface_type_skipped",
                    index=idx,
                    type=metadata.get("type"),
                    function_name=metadata.get("functionname", "unknown"),
                )

        except KeyError as e:
            logger.error(
                "missing_interface_field",
                missing_key=str(e),
                metadata_snippet=str(metadata)[:200],
                index=idx,
            )
            log_exception(
                logger,
                e,
                context={"operation": "create_interface_entry", "index": idx, "metadata": metadata},
            )
            raise
        except TypeError as e:
            logger.error(
                "interface_type_error",
                error=str(e),
                metadata_snippet=str(metadata)[:200],
                index=idx,
            )
            log_exception(
                logger,
                e,
                context={"operation": "create_interface_entry", "index": idx, "metadata": metadata},
            )
            raise

    log_function_result(
        logger,
        success=True,
        function="_create_base_interfaces",
        interface_count=len(interface_functions),
        interface_names=[f.functionname for f in interface_functions],
    )

    return interface_functions


async def _load_storage_config() -> dict[str, Any]:
    """
    Load the storage configuration from /workspace/config/storage_config.ini file.

    Returns:
        Storage configuration dictionary

    Raises:
        FileNotFoundError: If storage_config.ini file is not found
        ValueError: If file is not valid or missing required sections
    """
    log_function_call(logger, function="_load_storage_config", package=PACKAGE_NAME)

    try:
        config_path = _get_workspace_root() / "config" / "storage_config.ini"

        if not config_path.exists():
            logger.error("storage_config_not_found", path=str(config_path))
            raise FileNotFoundError(f"Storage configuration file not found at {config_path}")

        config = await FileReader.open_ini_file(config_path)
        log_function_result(
            logger,
            success=True,
            function="_load_storage_config",
            config_keys=list(config.keys()) if isinstance(config, dict) else "not_dict",
        )
        return config
    except Exception as e:
        log_exception(logger, e, context={"function": "_load_storage_config"})
        raise


async def _load_module_config() -> dict[str, Any]:
    """
    Load the module configuration from .module/module_data.yaml and
    .module/module_params.yaml (security, simulation sections).

    Returns:
        Module configuration dictionary

    Raises:
        FileNotFoundError: If module_data.yaml file is not found
        ValueError: If module_data.yaml is empty or invalid
    """
    log_function_call(logger, function="_load_module_config", package=PACKAGE_NAME)

    try:
        workspace_root = _get_workspace_root()
        data_path = workspace_root / ".module" / "module_data.yaml"
        params_path = workspace_root / ".module" / "module_params.yaml"

        if not data_path.exists():
            logger.error("module_data_not_found", path=str(data_path))
            raise FileNotFoundError(f"Module data file not found at {data_path}")

        module_data = await FileReader.open_yaml_file(data_path)
        if not module_data:
            raise ValueError("module_data.yaml is empty or invalid")

        config: dict[str, Any] = dict(module_data)
        config["package_name"] = str(module_data.get("name", PACKAGE_NAME))

        if params_path.exists():
            module_params = await FileReader.open_yaml_file(params_path) or {}
            if isinstance(module_params, dict):
                if "security" in module_params:
                    config["security"] = module_params["security"]
                if "simulation" in module_params:
                    config["simulation"] = module_params["simulation"]
        else:
            logger.warning("module_params_not_found", path=str(params_path))

        log_function_result(
            logger,
            success=True,
            function="_load_module_config",
            config_keys=list(config.keys()) if isinstance(config, dict) else "not_dict",
        )
        return config
    except Exception as e:
        log_exception(logger, e, context={"function": "_load_module_config"})
        raise


async def _load_module_data() -> Optional[dict[str, Any]]:
    """
    Load the module data from .module/module_data.yaml file.

    Returns:
        Module data dictionary or None if not found

    Raises:
        ValueError: If file is not valid or missing required sections
    """
    log_function_call(logger, function="_load_module_data", package=PACKAGE_NAME)

    data_path: Path = _get_workspace_root() / ".module" / "module_data.yaml"

    logger.debug("checking_module_data_file", path=str(data_path))

    try:
        module_data: Optional[dict[str, Any]] = await FileReader.open_yaml_file(data_path)
        logger.info(
            "module_data_loaded",
            path=str(data_path),
            data_keys=list(module_data.keys()) if module_data else [],
        )
        log_function_result(logger, success=True, function="_load_module_data")
        return module_data

    except FileNotFoundError as e:
        logger.warning("module_data_not_found", path=str(data_path), will_create=True)
        # Create .module directory if it does not exist
        if not data_path.parent.exists():
            logger.debug("creating_module_directory", path=str(data_path.parent))
            data_path.parent.mkdir(parents=True)
        return None
    except Exception as e:
        log_exception(logger, e, context={"function": "_load_module_data", "path": str(data_path)})
        raise


async def _write_module_data(data: dict[str, Any]) -> None:
    """
    Write module data to .module/module_data.yaml file.

    Args:
        data: Module data dictionary to write

    Raises:
        FileNotFoundError: If resource directory does not exist
        ValueError: If data is not valid or missing required sections
    """
    log_function_call(logger, function="_write_module_data", data_keys=list(data.keys()))

    try:
        data_path: Path = _get_workspace_root() / ".module" / "module_data.yaml"

        logger.debug("writing_module_data", path=str(data_path))
        await FileWriter.write_yaml_file(data_path, data)

        logger.info("module_data_written", path=str(data_path), data_keys=list(data.keys()))
        log_function_result(logger, success=True, function="_write_module_data")

    except Exception as e:
        log_exception(logger, e, context={"function": "_write_module_data", "path": str(data_path)})
        raise


async def _load_project_settings() -> dict[str, Any]:
    """
    Load project settings from pyproject.toml file.

    Returns:
        Project settings dictionary with version information

    Raises:
        FileNotFoundError: If pyproject.toml not found
        ValueError: If settings are missing or invalid
    """
    log_function_call(logger, function="_load_project_settings", package=PACKAGE_NAME)

    try:
        pyproject_path: Path = _get_workspace_root() / ".module" / "module_data.yaml"

        logger.debug("loading_pyproject", path=str(pyproject_path))
        module_settings: Optional[dict[str, Any]] = await FileReader.open_yaml_file(pyproject_path)

        if not module_settings:
            logger.error("module_settings_empty", path=str(pyproject_path))
            raise ValueError("Module settings not found in module_data.yaml")

        logger.info(
            "project_settings_loaded",
            module_name=module_settings.get("name", "unknown"),
            version=module_settings.get("version", "unknown"),
            blueprints=module_settings.get("blueprints", "unknown"),
        )
        log_function_result(logger, success=True, function="_load_project_settings")

        return module_settings

    except Exception as e:
        log_exception(
            logger, e, context={"function": "_load_project_settings", "path": str(pyproject_path)}
        )
        raise


def _resolve_module_blueprints(
    project_settings: dict[str, Any],
    module_data: Optional[dict[str, Any]] = None,
) -> str:
    """Resolve the module blueprint value from canonical blueprint settings.

    Handles both string and list values from YAML. A YAML list like
    ``[test, basic]`` is joined to ``"test, basic"`` rather than being
    converted to the Python repr ``"['test', 'basic']"``.
    """

    def _normalise(value: Any) -> Optional[str]:
        if value in (None, "", "null"):
            return None
        if isinstance(value, list):
            joined = ", ".join(str(v).strip() for v in value if str(v).strip())
            return joined if joined else None
        return str(value)

    if isinstance(module_data, dict):
        result = _normalise(module_data.get("blueprints"))
        if result is not None:
            return result

    for key in ("module_blueprints", "blueprints"):
        result = _normalise(project_settings.get(key))
        if result is not None:
            return result

    return "unknown"


def _build_module_data_payload(
    project_settings: dict[str, Any],
    module_data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build a module_data payload that persists all canonical fields.

    Preserves ``author``, ``alias`` and ``display_name`` from the existing
    ``module_data`` so that these fields are never stripped when the file is
    rewritten on startup.
    """
    source_data = module_data if isinstance(module_data, dict) else {}
    blueprints = _resolve_module_blueprints(project_settings, source_data)

    payload: dict[str, Any] = {
        "uuid": str(source_data.get("uuid") or ModuleEntry.gen_uuid()),
        "name": str(source_data.get("name") or project_settings.get("module_name", "")),
        "blueprints": blueprints,
        "description": str(
            source_data.get("description") or project_settings.get("module_description", "")
        ),
        "version": str(source_data.get("version") or project_settings.get("version", "0.0.0")),
        "author": str(source_data.get("author") or project_settings.get("author", "")),
        "alias": str(source_data.get("alias") or ""),
    }

    display_name = str(source_data.get("display_name") or project_settings.get("display_name", ""))
    if display_name:
        payload["display_name"] = display_name

    return payload


async def build_entity(project_settings) -> VyraEntity:
    """
    Build a VyraEntity from project settings and module data.

    Creates or recovers module entry, state entry, news entry, and error entry.
    Sets up storage and transient data types.

    Args:
        project_settings: Project configuration from pyproject.toml

    Returns:
        Configured VyraEntity instance

    Raises:
        ValueError: If module data is invalid or incomplete
    """
    log_function_call(
        logger, function="build_entity", module_name=project_settings.get("module_name", "unknown")
    )

    try:
        logger.debug("loading_module_data")
        module_data: Optional[dict] = await _load_module_data()
        normalized_module_data = _build_module_data_payload(project_settings, module_data)
        needed_fields: list[str] = ["uuid", "name", "description", "version"]
        has_blueprints_field = isinstance(module_data, dict) and "blueprints" in module_data

        if not module_data or module_data == {}:
            logger.info(
                "creating_new_module_entry", reason="module_data_empty", source="project_settings"
            )

            try:
                me = ModuleEntry(
                    uuid=normalized_module_data["uuid"],
                    name=normalized_module_data["name"],
                    blueprints=normalized_module_data["blueprints"],
                    description=normalized_module_data["description"],
                    version=normalized_module_data["version"],
                )
            except TypeError as exc:
                if "blueprints" not in str(exc):
                    raise
                logger.warning(
                    "module_entry_without_blueprints_fallback",
                    reason="base_moduleentry_signature_legacy",
                )
                me = ModuleEntry(
                    uuid=normalized_module_data["uuid"],
                    name=normalized_module_data["name"],
                    blueprints=normalized_module_data["blueprints"],
                    description=normalized_module_data["description"],
                    version=normalized_module_data["version"],
                )
            logger.debug("new_module_entry_created", uuid=me.uuid, name=me.name)

        elif not all(field in module_data for field in needed_fields) or not has_blueprints_field:
            missing_field: list[str] = [
                field for field in needed_fields if field not in module_data
            ]
            if not has_blueprints_field:
                missing_field.append("blueprints")

            logger.warning(
                "module_data_incomplete", missing_fields=missing_field, will_recover=True
            )

            try:
                me = ModuleEntry(
                    uuid=normalized_module_data["uuid"],
                    name=normalized_module_data["name"],
                    blueprints=normalized_module_data["blueprints"],
                    description=normalized_module_data["description"],
                    version=normalized_module_data["version"],
                )
            except TypeError as exc:
                if "blueprints" not in str(exc):
                    raise
                logger.warning(
                    "module_entry_without_blueprints_fallback",
                    reason="base_moduleentry_signature_legacy",
                )
                me = ModuleEntry(
                    uuid=normalized_module_data["uuid"],
                    name=normalized_module_data["name"],
                    blueprints=normalized_module_data["blueprints"],
                    description=normalized_module_data["description"],
                    version=normalized_module_data["version"],
                )

            logger.info(
                "module_data_recovered", uuid=me.uuid, name=me.name, recovered_fields=missing_field
            )
        else:
            logger.debug(
                "module_data_complete", uuid=module_data.get("uuid"), name=module_data.get("name")
            )

            if module_data["uuid"] in [None, "", "null"]:
                logger.warning("module_uuid_empty", generating_new=True)
                module_data["uuid"] = ModuleEntry.gen_uuid()

            normalized_module_data = _build_module_data_payload(project_settings, module_data)

            try:
                me = ModuleEntry(
                    uuid=normalized_module_data["uuid"],
                    name=normalized_module_data["name"],
                    blueprints=normalized_module_data["blueprints"],
                    description=normalized_module_data["description"],
                    version=normalized_module_data["version"],
                )
            except TypeError as exc:
                if "blueprints" not in str(exc):
                    raise
                logger.warning(
                    "module_entry_without_blueprints_fallback",
                    reason="base_moduleentry_signature_legacy",
                )
                me = ModuleEntry(
                    uuid=normalized_module_data["uuid"],
                    name=normalized_module_data["name"],
                    blueprints=normalized_module_data["blueprints"],
                    description=normalized_module_data["description"],
                    version=normalized_module_data["version"],
                )
            logger.debug("module_entry_loaded", uuid=me.uuid, name=me.name)

        # Persist module data
        logger.debug("persisting_module_data")
        await _write_module_data(normalized_module_data)

        logger.info(
            "module_entry_ready",
            uuid=me.uuid,
            name=me.name,
            blueprints=normalized_module_data["blueprints"],
            version=me.version,
        )

        # Create state, news, and error entries
        logger.debug("creating_state_entries")
        se = StateEntry(
            previous="initial",
            trigger="",
            current="running",
            module_id=me.uuid,
            module_name=me.name,
            timestamp=datetime.now(),
        )

        ne = NewsEntry(
            module_id=me.uuid,
            module_name=me.name,
        )

        ee = ErrorEntry(
            module_id=me.uuid,
            module_name=me.name,
        )

        logger.debug("state_entries_created")

        # Load configurations
        logger.debug("loading_configurations")
        module_config = await _load_module_config()
        storage_config = await _load_storage_config()
        logger.debug("configurations_loaded")

        # Create entity
        logger.info("creating_vyra_entity", module_name=me.name, uuid=me.uuid)
        entity = VyraEntity(
            state_entry=se,
            module_entry=me,
            news_entry=ne,
            error_entry=ee,
            module_config=module_config,
        )

        # Transient and parameter type dicts are left empty;
        # the VYRA interface layer resolves message types internally.
        transient_base_types: dict[str, Any] = {}

        parameter_types: dict[str, Any] = {}

        logger.debug("starting_entity_startup")
        await entity.startup_entity()
        logger.debug("entity_startup_complete")

        logger.debug("setting_up_entity_storage")
        await entity.setup_storage(storage_config, transient_base_types, parameter_types)

        logger.info("entity_built_successfully", module=me.name, uuid=me.uuid, version=me.version)
        log_function_result(logger, success=True, function="build_entity", module=me.name)

        return entity

    except Exception as e:
        log_exception(
            logger,
            e,
            context={
                "function": "build_entity",
                "module_name": project_settings.get("module_name", "unknown"),
            },
        )
        raise


async def create_db_storage(entity: VyraEntity) -> None:
    """
    Create database storage for the entity.

    Configuration is loaded from this ROS2 package's storage_config.ini file.

    Args:
        entity: VyraEntity for which database storage should be created

    Raises:
        FileNotFoundError: If storage_config.ini file is not found
        ValueError: If config is invalid or missing required sections
    """
    log_function_call(logger, function="create_db_storage", module_name=entity.module_entry.name)

    try:
        from vyra_base.com.clients.sql import DbAccess

        logger.debug("loading_storage_config")
        storage_config: dict[str, Any] = await _load_storage_config()
        logger.debug("storage_config_loaded", config_keys=list(storage_config.keys()))

        logger.info(
            "creating_db_access",
            module=entity.module_entry.name,
            db_type=storage_config.get("type", "unknown"),
        )

        db_access = DbAccess(module_name=entity.module_entry.name, db_config=storage_config)

        logger.debug("creating_database_tables")
        await db_access.create_all_tables()
        logger.info("database_tables_created")

        logger.debug("registering_storage_in_entity")
        entity.register_storage(db_access)

        logger.info("db_storage_ready", module=entity.module_entry.name)
        log_function_result(logger, success=True, function="create_db_storage")

    except Exception as e:
        log_exception(
            logger, e, context={"function": "create_db_storage", "module": entity.module_entry.name}
        )
        raise


async def build_base():
    """
    Build base entity configuration.

    Main initialization function that:
    1. Loads project settings
    2. Builds VyraEntity
    3. Creates base interfaces
    4. Sets up ROS2 services
    5. Creates database storage

    Returns:
        Fully configured VyraEntity

    Raises:
        Various exceptions from underlying functions
    """
    log_function_call(logger, function="build_base")

    try:
        logger.info("build_base_started")

        # Load project settings
        logger.debug("loading_project_settings")
        project_settings: dict[str, Any] = await _load_project_settings()
        logger.info(
            "project_settings_loaded", module_name=project_settings.get("module_name", "unknown")
        )

        # Build entity
        logger.debug("building_entity")
        entity: VyraEntity = await build_entity(project_settings)
        logger.info("entity_built", module=entity.module_entry.name, uuid=entity.module_entry.uuid)

        # VyraEntity.__init__() calls register_callables_callbacks(self) which adds
        # entity methods to DataSpace but doesn't create ROS2 services.
        # We need to create the actual ROS2 services by loading interfaces with callbacks.

        # Register interface package paths with ManifestResolver and SchemaResolver.
        # The installed source/package lives under /workspace/src/{module}_interfaces.
        interfaces_install_path = Path(f"/workspace/src/{entity.module_entry.name}_interfaces")
        module_name = entity.module_entry.name
        interfaces_pkg = f"{module_name}_interfaces"

        if interfaces_install_path.exists():
            # New API: registers with ManifestResolver + keeps legacy registry in sync
            entity.add_manifest_paths([interfaces_install_path])
            entity.add_schema_paths([interfaces_install_path])
            logger.info("interface_paths_set", paths=[str(interfaces_install_path)])
        else:
            logger.warning("interface_paths_not_found", path=str(interfaces_install_path))

        # Load interface definitions via the new load_interface_definitions function
        # (replaces _create_base_interfaces).  Returns FunctionConfigEntry list for
        # backward-compatible orchestration during the transition period.
        # from .interface import load_interface_definitions as _load_iface_defs
        # logger.debug("loading_interface_definitions")
        # base_interfaces: list[Any] = await _load_iface_defs(
        #     module_name=module_name,
        #     interfaces_pkg=interfaces_pkg,
        #     interfaces_base_path=interfaces_install_path
        #     if interfaces_install_path.exists()
        #     else Path(f"/workspace/src/{interfaces_pkg}"),
        # )
        # interface_names = [i.functionname for i in base_interfaces]
        # logger.info(
        #     "interface_definitions_loaded",
        #     count=len(base_interfaces),
        #     interfaces=interface_names,
        # )

        # Phase 2: bind callbacks for module-specific components.
        #
        # Entity-internal components (param_manager, volatile, skill_manager,
        # security_manager) now register their own callbacks directly inside
        # entity._init_params / _init_volatiles / _init_skills /
        # _init_security_manager via entity._bind_endpoint_callbacks().
        # The entity itself registers its own @remote_service methods in __init__.
        #
        # StateManager is module-specific: create it here so its @remote_actionServer
        # callbacks are bound before set_interfaces() activates the transport.
        from .state.state_manager import StateManager

        state_manager = StateManager(entity)
        logger.debug("state_manager_created")

        # Bind StateManager callbacks into the EndpointRegistry.
        entity.bind_endpoint_callbacks(state_manager)
        state_manager._endpoints_registered = True
        logger.debug("state_manager_callbacks_bound")

        # ── Removed (now handled inside entity._init_* methods) ──────────────
        # _callback_sources: list[Any] = [entity]
        # if hasattr(entity, 'parameter') and entity.parameter is not None:
        #     _callback_sources.append(entity.parameter)
        # if hasattr(entity, 'security') and entity.security is not None:
        #     _callback_sources.append(entity.security)
        # if hasattr(entity, 'volatile') and entity.volatile is not None:
        #     _callback_sources.append(entity.volatile)
        # if hasattr(entity, 'skill') and entity.skill is not None:
        #     _callback_sources.append(entity.skill)
        # if hasattr(entity, 'state_machine') and entity.state_machine is not None:
        #     _callback_sources.append(entity.state_machine)
        #
        # for component in _callback_sources:
        #     from vyra_base.com.core.decorators import get_decorated_methods
        #     decorated = get_decorated_methods(component)
        #     for method_info in decorated:
        #         fn_name = method_info.get("name") or getattr(method_info.get("method"), "__name__", None)
        #         method = method_info.get("method") or method_info
        #         cb_type = method_info.get("callback_type", "default")
        #         if fn_name and callable(method):
        #             try:
        #                 entity.endpoint_registry.bind_callback(fn_name, method, cb_type)
        #             except Exception as exc:
        #                 logger.debug(
        #                     "blueprint_callback_bind_warning",
        #                     component=type(component).__name__,
        #                     name=fn_name,
        #                     error=str(exc),
        #                 )
        #
        # ── Backward-compat bind_interface_callbacks loop also removed ────────
        # for component in _callback_sources:
        #     bound = entity.bind_interface_callbacks(component, base_interfaces)
        #     bound_count = sum(1 for v in bound.values() if v)
        #     if bound_count:
        #         logger.info(
        #             "blueprint_callbacks_bound",
        #             component=type(component).__name__,
        #             bound=bound_count,
        #             total=len(bound),
        #         )

        # ── Removed: bind_interface_callbacks / set_interfaces ───────────────────
        # The EndpointOrchestrator handles transport activation automatically once
        # the EndpointRegistry has all callbacks, the ManifestResolver has loaded
        # the *.meta.json definitions, and the SchemaResolver has resolved schemas.
        #
        # bound = entity.bind_interface_callbacks(state_manager, base_interfaces)
        # await entity.set_interfaces(base_interfaces)

        logger.debug("endpoint_orchestrator_will_activate_transports")

        # Create database storage
        logger.debug("creating_db_storage")
        await create_db_storage(entity)
        logger.info("db_storage_created")

        logger.info(
            "build_base complete",
            endpoint_count=len(entity.endpoint_registry.list_all()),
            module=entity.module_entry.name,
            uuid=entity.module_entry.uuid,
        )
        log_function_result(
            logger, success=True, function="build_base", module=entity.module_entry.name
        )

        return entity, state_manager

    except Exception as e:
        log_exception(logger, e, context={"function": "build_base"})
        raise
