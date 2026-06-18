import importlib
import json
from .logging_config import get_logger
import os
import sys
from pathlib import Path

from typing import Any, Optional, TYPE_CHECKING
from typing import Callable


VYRA_SLIM = os.getenv('VYRA_SLIM', 'false').lower() == 'true'
if not VYRA_SLIM:
    from ament_index_python.packages import get_package_share_directory  # pyright: ignore[reportMissingImports]

from vyra_base.defaults.entries import FunctionConfigEntry
from vyra_base.defaults.entries import FunctionConfigDisplaystyle
from vyra_base.defaults.entries import FunctionConfigBaseTypes
from vyra_base.defaults.entries import FunctionConfigTags
from vyra_base.helper.error_handler import ErrorTraceback
from vyra_base.com.manifest import ManifestResolver

if TYPE_CHECKING:
    from vyra_base.core.entity import VyraEntity
else:
    VyraEntity = Any


logger = get_logger(__name__)


# async def load_interface_definitions(
#     module_name: str,
#     interfaces_pkg: str,
#     interfaces_base_path: Path,
#     schema_path: Optional[Path] = None,
# ) -> list[FunctionConfigEntry]:
#     """
#     Load and validate interface definitions from ``*.meta.json`` files.

#     This function replaces ``_create_base_interfaces`` in ``_base_.py``.
#     It is the canonical entry-point for building the initial list of
#     ``FunctionConfigEntry`` objects (definitions only, no callbacks).

#     The ``ManifestResolver`` singleton is used for validation so that any
#     already-registered paths are respected and the bundled schema is the
#     default.

#     Args:
#         module_name:          ``MODULE_NAME`` environment variable value.
#         interfaces_pkg:       Python package name, e.g. ``v2_modulemanager_interfaces``.
#         interfaces_base_path: Base directory of the interfaces package
#                               (the directory that contains ``config/``).
#         schema_path:          Optional override for the JSON Schema file used
#                               during validation (defaults to the bundled
#                               ``vyra_base/assets/schemas/interface_config.json``).

#     Returns:
#         List of ``FunctionConfigEntry`` objects (callbacks are all ``None``).
#     """
#     resolver = ManifestResolver.get_instance()

#     config_dir = interfaces_base_path / "config"
#     all_meta_files: list[Path] = sorted(config_dir.glob("*.meta.json"))

#     if not all_meta_files:
#         logger.warning(
#             "load_interface_definitions: no *.meta.json files found in %s",
#             config_dir,
#         )
#         return []

#     valid_files, invalid_files = resolver.validate_metadata(
#         all_meta_files, schema_path=schema_path
#     )

#     for bad_file, reason in invalid_files:
#         logger.warning(
#             "load_interface_definitions: skipping invalid meta file '%s': %s",
#             bad_file.name,
#             reason,
#         )

#     interface_functions: list[FunctionConfigEntry] = []

#     for meta_file in valid_files:
#         try:
#             with open(meta_file, encoding="utf-8") as fh:
#                 raw = json.load(fh)
#         except Exception as exc:
#             logger.error(
#                 "load_interface_definitions: cannot read %s: %s", meta_file, exc
#             )
#             continue

#         entries = raw if isinstance(raw, list) else [raw]

#         for metadata in entries:
#             try:
#                 itype = metadata.get("type", "")

#                 if itype == FunctionConfigBaseTypes.service.value:
#                     ifaces = _resolve_interface_types(metadata, module_name)
#                     metadata["interfacetypes"] = ifaces

#                 interface_functions.append(
#                     _build_function_config_entry(metadata, callback=None)
#                 )
#             except (KeyError, TypeError, ValueError) as exc:
#                 logger.error(
#                     "load_interface_definitions: cannot build entry for '%s': %s",
#                     metadata.get("functionname", "?"),
#                     exc,
#                 )

#     logger.info(
#         "load_interface_definitions: loaded %d interface definition(s) "
#         "from %s.",
#         len(interface_functions),
#         interfaces_pkg,
#     )
#     return interface_functions


# ---------------------------------------------------------------------------
# register_endpoint_callbacks — new canonical API
# ---------------------------------------------------------------------------

def register_endpoint_callbacks(
    entity: "VyraEntity",
    callback_parent: object,
) -> None:
    """Register all decorated callbacks from *callback_parent* with the entity.

    This is the replacement for ``auto_register_interfaces``.  It delegates
    directly to :meth:`~vyra_base.core.entity.VyraEntity.bind_endpoint_callbacks`
    which routes every ``@remote_service`` / ``@remote_actionServer`` decorated
    method through :class:`~vyra_base.com.endpoint.EndpointRegistry`.

    The :class:`~vyra_base.com.orchestrator.EndpointOrchestrator` then wires
    the transport provider as soon as the endpoint has a manifest definition,
    a resolved schema, *and* all required callbacks — no explicit
    ``set_interfaces()`` call is needed.

    Args:
        entity:          The :class:`~vyra_base.core.entity.VyraEntity` instance.
        callback_parent: Any object whose methods are decorated with VYRA
                         communication decorators.
    """
    entity.bind_endpoint_callbacks(callback_parent)
    logger.info(
        "register_endpoint_callbacks: callbacks bound for %s",
        type(callback_parent).__name__,
    )


# ---------------------------------------------------------------------------
# Internal helpers shared between load_interface_definitions and
# auto_register_interfaces
# ---------------------------------------------------------------------------

# def _resolve_interface_types(metadata: dict, module_name: str) -> list:
#     """Resolve filetype entries to ROS2 classes or raw strings."""
#     ifaces: list = []
#     for iface_type in metadata.get("filetype", []):
#         if "." not in iface_type:
#             ifaces.append(iface_type)
#             continue
#         filename, ext = iface_type.rsplit(".", 1)
#         if ext in ("msg", "srv", "action") and not VYRA_SLIM:
#             try:
#                 module_key = f"{module_name}_interfaces.{ext}"
#                 iface_module = (
#                     sys.modules.get(module_key)
#                     or importlib.import_module(module_key)
#                 )
#                 ifaces.append(getattr(iface_module, filename))
#             except (KeyError, AttributeError, ImportError) as exc:
#                 logger.warning(
#                     "_resolve_interface_types: could not load ROS2 type "
#                     "'%s': %s — keeping as string.",
#                     iface_type,
#                     exc,
#                 )
#                 ifaces.append(iface_type)
#         else:
#             ifaces.append(iface_type)
#     return ifaces


# def _build_function_config_entry(
#     metadata: dict, callback: Optional[Callable]
# ) -> FunctionConfigEntry:
#     """Build a FunctionConfigEntry from raw metadata dict."""
#     ds_raw = metadata.get("displaystyle", {})
#     displaystyle = FunctionConfigDisplaystyle(
#         visible=ds_raw.get("visible", False),
#         published=ds_raw.get("published", False),
#     )
#     tags = [
#         FunctionConfigTags(t)
#         for t in metadata.get("tags", [])
#         if t in FunctionConfigTags._value2member_map_
#     ]
#     itype = metadata.get("type", "")
#     callbacks_dict = None
#     if callback is not None:
#         if itype == FunctionConfigBaseTypes.service.value:
#             callbacks_dict = {"response": callback}

#     return FunctionConfigEntry(
#         tags=tags,
#         type=itype,
#         interfacetypes=metadata.get("interfacetypes", None),
#         functionname=metadata["functionname"],
#         displayname=metadata.get("displayname", metadata["functionname"]),
#         description=metadata.get("description", ""),
#         displaystyle=displaystyle,
#         params=metadata.get("params", []),
#         returns=metadata.get("returns", []),
#         namespace=metadata.get("namespace", None),
#         qosprofile=metadata.get("qosprofile", 10),
#         callbacks=callbacks_dict,
#         periodic=metadata.get("periodic", None),
#     )


# @ErrorTraceback.w_check_error_exist
# async def auto_register_interfaces(
#     entity: VyraEntity, 
#     callback_list: list[Callable]=[], 
#     callback_parent: object=None,
#     publisher_list: list=[]) -> None:
#     """
#     .. deprecated::
#         Use :func:`register_endpoint_callbacks` instead.

#         ``auto_register_interfaces`` performed three steps that are now separated:

#         1. **Callback discovery** — done by ``bind_endpoint_callbacks(component)``
#            (inside each ``_init_*`` method of ``VyraEntity``).
#         2. **Manifest loading** — done by ``ManifestResolver`` via
#            ``entity.add_manifest_paths()``.
#         3. **Transport creation** — done automatically by ``EndpointOrchestrator``.

#         Migration:

#         .. code-block:: python

#             # OLD
#             await auto_register_interfaces(entity, callback_parent=self)

#             # NEW
#             register_endpoint_callbacks(entity, callback_parent=self)

#         This function is preserved temporarily so callers can migrate at their own
#         pace.  It delegates to ``register_endpoint_callbacks`` when called with a
#         ``callback_parent``.
#     """
#     import warnings
#     warnings.warn(
#         "auto_register_interfaces() is deprecated. "
#         "Use register_endpoint_callbacks() instead.",
#         DeprecationWarning,
#         stacklevel=2,
#     )

#     # Fast-path: if only callback_parent is given, delegate to new API.
#     if callback_parent is not None and not callback_list:
#         register_endpoint_callbacks(entity, callback_parent)
#         return

#     # Legacy path retained for callers that pass an explicit callback_list.
#     if not callback_list and not callback_parent:
#         raise ValueError("Either callback_list or callback_parent must be provided.")
    
#     if not callback_list:
#         logger.debug(
#             "No callback_list provided, loading all remote callables from parent."
#         )
#         callback_list = _autoload_all_remote_service_from_parent(callback_parent)
#         logger.debug(
#             f"Loaded {len(callback_list)} remote callables from parent."
#         )

#     module_name = os.getenv("MODULE_NAME", "")

#     interface_metadata: list[dict[str, Any]] = _load_metadata(f'{module_name}_interfaces', Path('config'))

#     interface_functions: list[FunctionConfigEntry] = []

#     for callback in callback_list:
#         config_list: list = [m for m in interface_metadata 
#                     if m['functionname'] == callback.__name__]

#         if not config_list:
#             logger.warning(
#                 f"No config found for callback {callback.__name__}, skipping."
#             )
#             continue
#         elif len(config_list) > 1:
#             logger.warning(
#                 f"Multiple metadata entries found for callback {callback.__name__}, "
#                 f"using the first one from config: {config_list}. Please check your configuration!"
#             )
#             metadata: dict = config_list[0]
#         else:
#             metadata: dict = config_list[0]

#         # Handle filetype as either a string or a list
#         filetype_raw = metadata['filetype']
#         if isinstance(filetype_raw, list):
#             # New format: list of filetypes, e.g. ["VBASEGetInterfaceList.proto"]
#             # Use _base_.py style: load ROS2 types for .srv / .msg / .action, pass through others
#             ifaces = []
#             for iface_type in filetype_raw:
#                 filename, ext = iface_type.split('.') if '.' in iface_type else (iface_type, '')
#                 if ext in ['msg', 'srv', 'action']:
#                     try:
#                         module_key = f'{module_name}_interfaces.{ext}'
#                         iface_module = sys.modules.get(module_key) or importlib.import_module(module_key)
#                         iface_class = getattr(iface_module, filename)
#                         ifaces.append(iface_class)
#                     except (KeyError, AttributeError, ImportError) as e:
#                         logger.warning(f"Could not load ROS2 type {iface_type}: {e}")
#                         ifaces.append(iface_type)  # keep as string
#                 else:
#                     ifaces.append(iface_type)  # proto / custom type, keep as string
#             metadata['interfacetypes'] = ifaces
#         else:
#             # Old format: single string, e.g. "v2_modulemanager_interfaces/srv/Foo.srv"
#             logger.warning(f"Old format not supported for filetype: {filetype_raw}. Please update to list format in your config.")
#             raise ValueError(f"Old format not supported for filetype: {filetype_raw}. Please update to list format in your config.")

#         match metadata['type']:
#             case FunctionConfigBaseTypes.service.value:
#                 interface_functions.append(_register_service_interface(
#                     callback=callback,
#                     metadata=metadata
#                 ))

#             case FunctionConfigBaseTypes.message.value:
#                 interface_functions.append(_register_publisher_interface(
#                     metadata=metadata
#                 ))

#             case FunctionConfigBaseTypes.action.value:
#                 interface_functions.append(_register_action_interface(
#                     metadata=metadata,
#                     callbacks={}
#                 ))

#     # Scan for @remote_actionServer decorated methods and register their action interfaces.
#     # These methods are NOT discovered by _autoload_all_remote_service_from_parent because
#     # they carry _vyra_action_name instead of _vyra_remote_server.
#     if callback_parent:
#         registered_names = {entry.functionname for entry in interface_functions}
#         action_names_seen: set[str] = set()
#         for attr_name in dir(callback_parent):
#             try:
#                 attr = getattr(callback_parent, attr_name)
#                 if not (callable(attr) and hasattr(attr, "_vyra_action_name")):
#                     continue
#                 action_name: str = attr._vyra_action_name
#                 if action_name in registered_names or action_name in action_names_seen:
#                     continue
#                 action_names_seen.add(action_name)
#                 config_list = [m for m in interface_metadata if m['functionname'] == action_name]
#                 if not config_list:
#                     logger.warning(
#                         f"No metadata found for ActionServer '{action_name}', skipping."
#                     )
#                     continue
#                 action_meta: dict = config_list[0]
#                 if action_meta.get('type') != FunctionConfigBaseTypes.action.value:
#                     continue
#                 # Resolve interfacetypes from filetype list
#                 filetype_raw = action_meta.get('filetype', [])
#                 if isinstance(filetype_raw, list):
#                     ifaces = []
#                     for iface_type in filetype_raw:
#                         filename, ext = (
#                             iface_type.split('.') if '.' in iface_type else (iface_type, '')
#                         )
#                         if ext in ['msg', 'srv', 'action']:
#                             try:
#                                 module_key = f'{module_name}_interfaces.{ext}'
#                                 iface_module = sys.modules.get(module_key) or importlib.import_module(module_key)
#                                 iface_class = getattr(iface_module, filename)
#                                 ifaces.append(iface_class)
#                             except (KeyError, AttributeError, ImportError) as e:
#                                 logger.warning(f"Could not load ROS2 type {iface_type}: {e}")
#                                 ifaces.append(iface_type)
#                         else:
#                             ifaces.append(iface_type)
#                     action_meta['interfacetypes'] = ifaces
#                 interface_functions.append(
#                     _register_action_interface(metadata=action_meta, callbacks={})
#                 )
#                 logger.info(
#                     f"Auto-registered action interface from @remote_actionServer: {action_name}"
#                 )
#             except Exception as e:
#                 logger.debug(f"Error scanning '{attr_name}' for action server decorator: {e}")

#     logger.info(f"Registering {len(interface_functions)} interfaces for entity")

#     # Bind decorated callbacks from callback_parent (for multi-callback ActionServers)
#     if callback_parent:
#         logger.debug("Binding decorated callbacks from component...")
#         binding_results = entity.bind_interface_callbacks(
#             component=callback_parent,
#             settings=interface_functions
#         )
#         logger.info(
#             f"Callback binding complete: "
#             f"{sum(binding_results.values())}/{len(binding_results)} successful"
#         )
    
#     await entity.set_interfaces(interface_functions)
#     return

# def _autoload_all_remote_service_from_parent(callback_parent: object) -> list:
#     callable_list = []
    
#     logger.debug(f"Scanning {callback_parent.__class__.__name__} for remote callables...")
#     logger.debug(f"  callback_parent type: {type(callback_parent)}")
#     logger.debug(f"  callback_parent class: {callback_parent.__class__}")
    
#     # Check both instance and class for remote callables
#     # This is necessary because decorator attributes might be on the class method
#     for attr_name in dir(callback_parent):
#         if attr_name.startswith("_"):
#             continue
            
#         try:
#             # Get attribute from instance (bound method)
#             attr = getattr(callback_parent, attr_name)
            
#             # Debug specific method
#             if attr_name == "get_interface_list":
#                 logger.debug(f"  Checking get_interface_list:")
#                 logger.debug(f"    attr: {attr}")
#                 logger.debug(f"    type(attr): {type(attr)}")
#                 logger.debug(f"    callable: {callable(attr)}")
#                 logger.debug(f"    has _vyra_remote_server: {hasattr(attr, '_vyra_remote_server')}")
#                 logger.debug(f"    _vyra_remote_server value: {getattr(attr, '_vyra_remote_server', 'NOT FOUND')}")
                
#                 # Try __func__ if it's a bound method
#                 if hasattr(attr, "__func__"):
#                     logger.debug(f"    __func__._vyra_remote_server: {getattr(attr.__func__, '_vyra_remote_server', 'NOT FOUND')}")
                
#                 # Try class method
#                 class_method = getattr(callback_parent.__class__, "get_interface_list", None)
#                 if class_method:
#                     logger.debug(f"    class method._vyra_remote_server: {getattr(class_method, '_vyra_remote_server', 'NOT FOUND')}")
            
#             # Check if it's callable and has _vyra_remote_server marker (set by @remote_service decorator)
#             if callable(attr) and hasattr(attr, "_vyra_remote_server"):
#                 logger.debug(f"  Found remote_service on instance: {attr_name}")
#                 callable_list.append(attr)
#                 continue
            
#             # If not found on instance, try the class
#             # This handles cases where decorator is on class method
#             if hasattr(callback_parent.__class__, attr_name):
#                 class_attr = getattr(callback_parent.__class__, attr_name)
#                 if callable(class_attr) and hasattr(class_attr, "_vyra_remote_server"):
#                     logger.debug(f"  Found remote_service on class: {attr_name}")
#                     # Get the bound method from instance
#                     callable_list.append(attr)
#         except AttributeError as e:
#             if attr_name == "get_interface_list":
#                 logger.debug(f"  AttributeError for get_interface_list: {e}")
#             continue
#         except Exception as e:
#             logger.debug(f"  Unexpected error for {attr_name}: {e}")
#             continue
    
#     logger.debug(f"Total remote callables found: {len(callable_list)}")
#     return callable_list

# def _load_metadata(package_name: str, resource_folder: Path) -> list[dict[str, Any]]:
#     """Loads metadata from a specified package and resource.

#     In SLIM mode the Python source tree is used; in FULL mode the ROS2 ament
#     package share directory is queried.
#     """
#     if VYRA_SLIM:
#         resource_path = Path(__file__).parent.parent.parent.parent / "src" / package_name / resource_folder
#     else:
#         resource_path = Path(get_package_share_directory(package_name)) / resource_folder
#     meta_paths: list[Path] = list(resource_path.rglob("*.json"))

#     metadata: list[dict] = []

#     logger.debug(f"Meta paths: {meta_paths}")

#     for meta_path in meta_paths:
#         logger.debug(f"Loading custom interface resource from {meta_path}")

#         with open(meta_path, 'r', encoding='utf-8') as f:
#             metadata.extend(json.load(f))
#     return metadata

# def _register_publisher_interface(
#         metadata: dict) -> FunctionConfigEntry:
#     displaystyle = FunctionConfigDisplaystyle(
#         visible=metadata.get('displaystyle', {}).get('visible', False),
#         published=metadata.get('displaystyle', {}).get('published', False)
#     )
#     return FunctionConfigEntry(
#         tags=metadata['tags'],
#         type=metadata['type'],
#         interfacetypes=metadata.get('interfacetypes', None),
#         functionname=metadata['functionname'],
#         displayname=metadata['displayname'],
#         description=metadata['description'],
#         displaystyle=displaystyle,
#         returns=metadata['returns'],
#         namespace=metadata.get('namespace', None),
#         qosprofile=metadata.get('qosprofile', 10),
#         periodic=metadata.get('periodic', None)
#     )


# def _register_service_interface( 
#         callback: Callable, 
#         metadata: dict) -> FunctionConfigEntry:
#     """Registers a callable interface for the entity."""
#     displaystyle = FunctionConfigDisplaystyle(
#         visible=metadata.get('displaystyle', {}).get('visible', False),
#         published=metadata.get('displaystyle', {}).get('published', False)
#     )
#     return FunctionConfigEntry(
#         tags=metadata['tags'],
#         type=metadata['type'],
#         interfacetypes=metadata.get('interfacetypes', None),
#         functionname=metadata['functionname'],
#         displayname=metadata['displayname'],
#         description=metadata['description'],
#         displaystyle=displaystyle,
#         params=metadata['params'],
#         returns=metadata['returns'],
#         namespace=metadata.get('namespace', None),
#         qosprofile=metadata.get('qosprofile', 10),
#         callbacks={'response': callback} if callback is not None else None
#     )

# def _register_action_interface(
#         metadata: dict,
#         callbacks: dict[str, Callable]) -> FunctionConfigEntry:
#     """Registers a job interface for the entity."""
#     displaystyle = FunctionConfigDisplaystyle(
#         visible=metadata.get('displaystyle', {}).get('visible', False),
#         published=metadata.get('displaystyle', {}).get('published', False)
#     )
#     return FunctionConfigEntry(
#         tags=metadata['tags'],
#         type=metadata['type'],
#         interfacetypes=metadata.get('interfacetypes', None),
#         functionname=metadata['functionname'],
#         displayname=metadata['displayname'],
#         description=metadata['description'],
#         displaystyle=displaystyle,
#         params=metadata['params'],
#         returns=metadata['returns'],
#         namespace=metadata.get('namespace', None),
#         qosprofile=metadata.get('qosprofile', 10)
#     )