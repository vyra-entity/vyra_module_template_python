#!/usr/bin/env python3

# Short description:
# This script sets up or updates ROS2 interface packages for V.Y.R.A. modules.
# It auto-detects the module name, manages interface files, updates CMakeLists.txt
# and package.xml, and checks for naming conflicts with reserved interface names

from __future__ import annotations

import argparse
import sys
import os
import re
import subprocess
import shutil
import json
import logging
from pathlib import Path
import xml.etree.ElementTree as ET
import yaml

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def get_module_name():
    """Auto-detect module name from .module/module_data.yaml or directory name."""
    script_dir = Path(__file__).parent.parent

    # Try to read from .module/module_data.yaml
    module_data_file = script_dir / ".module" / "module_data.yaml"
    if module_data_file.exists():
        try:
            import yaml

            with open(module_data_file, "r") as f:
                data = yaml.safe_load(f)
                if "name" in data:
                    return data["name"]
        except Exception as e:
            print(f"⚠️ Could not read module name from {module_data_file}: {e}")

    # Fallback: use directory name (strip hash suffix if present)
    dir_name = script_dir.name
    # Remove hash suffix like _733256b82d6b48a48bc52b5ec73ebfff
    if "_" in dir_name:
        parts = dir_name.split("_")
        # Check if last part looks like a hash (32+ hex chars)
        if len(parts[-1]) >= 32 and all(c in "0123456789abcdef" for c in parts[-1]):
            dir_name = "_".join(parts[:-1])

    return dir_name


def parse_args():
    module_name = get_module_name()
    default_interface_pkg = f"{module_name}_interfaces"

    parser = argparse.ArgumentParser(
        description="Script to set up ROS2 and Proto interfaces for V.Y.R.A. modules.",
        epilog="""
Examples:
  # Build all interfaces (ROS2 + Proto with Python + C++)
  python setup_interfaces.py --all
  
  # Build only ROS2 interfaces
  python setup_interfaces.py --ros2-only
  
  # Build only Proto interfaces (Python + C++)
  python setup_interfaces.py --proto-only
  
  # Build specific interface package
  python setup_interfaces.py --interface_pkg my_custom_interfaces
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--interface_pkg",
        type=str,
        default=default_interface_pkg,
        help=(
            f"Name of the ROS2 interface package to create or update "
            f"(default: {default_interface_pkg})"
        ),
    )

    # Build mode arguments
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Build both ROS2 and Proto interfaces (default if no mode specified)",
    )
    mode_group.add_argument(
        "--ros2-only", action="store_true", help="Build only ROS2 interfaces (.msg, .srv, .action)"
    )
    mode_group.add_argument(
        "--proto-only",
        action="store_true",
        help="Build only Proto interfaces (.proto) - Python and C++",
    )

    return parser.parse_args()


def update_package_xml(package_path: Path, package_name: str = None):
    """Fügt nötige Dependencies zur package.xml hinzu und updated den package name."""
    pkg_xml_path = package_path / "package.xml"

    exec_dependencies = ["rclpy", "rosidl_default_runtime"]
    build_dependencies = [
        "builtin_interfaces",
        "rosidl_default_generators",
    ]

    for interface_type in ["msg", "srv", "action"]:
        interface_dir = package_path / interface_type

        if interface_dir.exists():
            for file in interface_dir.glob(f"*.{interface_type}"):
                exec_dependencies.append(file.stem)
                build_dependencies.append(file.stem)

    if not pkg_xml_path.exists():
        print(f"⚠️ package.xml nicht gefunden in {pkg_xml_path}")
        return

    tree: ET.ElementTree[ET.Element[str]] = ET.parse(pkg_xml_path)
    root: ET.Element[str] = tree.getroot()

    # Update package name if provided
    if package_name:
        name_elem = root.find("name")
        if name_elem is not None and name_elem.text != package_name:
            print(f"📝 Updating package name from '{name_elem.text}' to '{package_name}'")
            name_elem.text = package_name

    # Helper, um Dopplungen zu vermeiden
    build_existing = {tag.text for tag in root.findall("./*") if tag.tag.endswith("build_depend")}

    exec_existing = {tag.text for tag in root.findall("./*") if tag.tag.endswith("exec_depend")}

    for dep in exec_dependencies:
        if dep not in exec_existing:
            elem = ET.Element("exec_depend")
            elem.text = dep
            root.append(elem)

    for dep in build_dependencies:
        if dep not in build_existing:
            elem = ET.Element("build_depend")
            elem.text = dep
            root.append(elem)

    # Save updated XML
    ET.indent(tree, space="\t")
    tree.write(pkg_xml_path, encoding="utf-8", xml_declaration=True)
    print("✓ package.xml aktualisiert.")


def replace_libname_in_file(filepath, old_word, new_word):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    if old_word in content:
        content_new = content.replace(old_word, new_word)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content_new)
        print(f"Ersetzt in {filepath}")


def collect_interface_files(package_path):
    interface_dirs = ["msg", "srv", "action"]
    interface_files = {}
    # ROS2 requires interface names to match ^[A-Z][A-Za-z0-9]*$ (no underscores)
    _ros2_name_re = re.compile(r"^[A-Z][A-Za-z0-9]*$")
    for dir_name in interface_dirs:
        dir_path = os.path.join(package_path, dir_name)
        collected_files = []
        if os.path.isdir(dir_path):
            # Walk through all subdirectories
            for root, _, files in os.walk(dir_path):
                for f in files:
                    if f.endswith(f".{dir_name}"):
                        stem = f[: -(len(dir_name) + 1)]  # filename without extension
                        if not _ros2_name_re.match(stem):
                            print(
                                f"⚠️  Skipping '{f}': not a valid ROS2 interface name (contains underscore or starts with lowercase)."
                            )
                            continue
                        # Get relative path from package_path
                        rel_path = os.path.relpath(os.path.join(root, f), package_path)
                        collected_files.append(rel_path)
            if collected_files:
                interface_files[dir_name] = collected_files
    return interface_files


def update_CMakefile(interface_package_path: Path, package_name: str):
    interface_files = collect_interface_files(interface_package_path)
    msg_files = interface_files.get("msg", [])
    srv_files = interface_files.get("srv", [])
    action_files = interface_files.get("action", [])

    cmake_path = os.path.join(interface_package_path, "CMakeLists.txt")
    cmake_tmp_path = os.path.join(interface_package_path, "CMakeLists.template.txt")

    # Check if CMakeLists.txt exists otherwise load from CMakeLists.template.txt
    if not os.path.isfile(cmake_path):
        if os.path.isfile(cmake_tmp_path):
            shutil.copyfile(cmake_tmp_path, cmake_path)
            print(f"✓ CMakeLists.txt erstellt aus Vorlage.")
            # Ensure file is owned by vyrauser (uid=1000) not root
            try:
                os.chown(cmake_path, 1000, 1000)
                print(f"✓ CMakeLists.txt Besitzer auf vyrauser (uid=1000) gesetzt.")
            except PermissionError:
                print(f"⚠️ Konnte Besitzer von CMakeLists.txt nicht ändern (kein root-Zugriff).")
        else:
            print("⚠️ CMakeLists.txt und Vorlage nicht gefunden.")
            return

    # 1. Sammle alle Interface-Dateien
    if not msg_files and not srv_files and not action_files:
        print("⚠️ Keine .msg, .srv oder .action Dateien im Verzeichnis gefunden.")
        return

    # 2. CMakeLists.txt anpassen
    with open(cmake_path, "r") as f:
        cmake_content = f.read()

    # Fix project name to match package.xml
    cmake_content = re.sub(r"project\([^)]*\)", f"project({package_name})", cmake_content)
    print(f"✓ CMakeLists.txt: project() set to '{package_name}'.")

    # Ersetze oder ergänze rosidl_generate_interfaces(...)
    rosidl_line = "rosidl_generate_interfaces(${PROJECT_NAME}\n"
    for msg_file in msg_files:
        rosidl_line += f'  "{msg_file}"\n'
    for srv_file in srv_files:
        rosidl_line += f'  "{srv_file}"\n'
    for action_file in action_files:
        rosidl_line += f'  "{action_file}"\n'
    rosidl_line += "\n  DEPENDENCIES std_msgs unique_identifier_msgs builtin_interfaces\n  # Add additional dependencies here\n)\n"

    # Regex: ersetze vorhandene rosidl_generate_interfaces oder füge neu ein
    if "rosidl_generate_interfaces" in cmake_content:
        cmake_content = re.sub(
            r"rosidl_generate_interfaces\([^)]*\)", rosidl_line, cmake_content, flags=re.DOTALL
        )
        print("✓ CMakeLists.txt: rosidl_generate_interfaces() aktualisiert.")
    else:
        # Einfach am Ende hinzufügen
        cmake_content += "\n" + rosidl_line + "\n"
        print("✓ CMakeLists.txt: rosidl_generate_interfaces() hinzugefügt.")

    # Remove all empty lines in cmake_content
    cmake_content = re.sub(r"\n\s*\n", "\n", cmake_content)

    with open(cmake_path, "w") as f:
        f.write(cmake_content)


def update_setup_py(package_path, package_name):
    setup_path = os.path.join(package_path, "setup.py")
    interface_files = collect_interface_files(package_path)

    with open(setup_path, "r") as f:
        content = f.read()

    data_files_pattern = re.compile(r"data_files\s*=\s*\[\n*", re.DOTALL)
    match = data_files_pattern.search(content)

    if not match:
        print("⚠️ Keine data_files-Struktur in setup.py gefunden.")
        raise ValueError("CMakeLists.txt and CMakeLists.template.txt not found.")

    data_files_str = match.group(0)

    new_data: dict = {}

    for interface_type, files in interface_files.items():
        key = f"share/{package_name}/{interface_type}"

        if key not in new_data:
            new_data[key] = []
        for f in files:
            if f not in new_data[key]:
                new_data[key].append(f)

    # Generiere den neuen data_files-String
    new_data_files_str = "data_files=[\n"
    for k, v in new_data.items():
        files_str = ",\n            ".join(f"'{file}'" for file in sorted(v))
        new_data_files_str += f"        ('{k}', [\n            {files_str}\n        ]),\n"

    # Ersetze im Originaltext
    new_content = content.replace(data_files_str, new_data_files_str)

    # Setup.py überschreiben
    with open(setup_path, "w") as f:
        f.write(new_content)

    print("✓ setup.py wurde aktualisiert. Originalversion wurde als setup.py.bak gespeichert.")


def load_reserved_list(vyra_base_path: Path = None) -> dict:
    """
    Load reserved interface names from vyra_base RESERVED.list.

    Returns an empty dict when vyra_base is not installed (e.g. in Rust/non-Python
    containers).  The Rust setup_interfaces.sh performs its own reserved-name check
    against vyra_base_rust/interfaces/config/RESERVED.list before calling this script.

    Returns:
        dict: {interface_filename: {'function_name': str, 'config_file': str}}
    """
    try:
        import vyra_base

        return vyra_base.get_reserved_list()
    except ImportError:
        logging.warning(
            "⚠️  vyra_base not available – skipping reserved-name check "
            "(expected in Rust/non-Python containers)."
        )
        return {}


def check_reserved_function_names(
    config_path: Path,
    reserved_interfaces: dict,
    vyra_base_files: set,
) -> list:
    """
    Check module-specific config JSON files for function names reserved by vyra_base.

    Only files that are NOT part of the vyra_base config set are considered
    module-specific and subject to this check.  Files copied from vyra_base are
    skipped because they intentionally define the reserved interfaces.

    Args:
        config_path: Path to the interfaces ``config/`` directory.
        reserved_interfaces: Mapping returned by ``load_reserved_list()``.
        vyra_base_files: Set of file *names* (basename only) from vyra_base's
            config directory, as returned by ``vyra_base.get_vyra_base_config_files()``.

    Returns:
        list: Error message strings for every violation found (empty = ok).
    """
    errors = []
    reserved_functions = {
        info["function_name"]: name
        for name, info in reserved_interfaces.items()
        if info["function_name"]
    }

    for json_file in list(config_path.glob("*.meta.json")) + list(config_path.glob("*_meta.json")):
        # Skip files that originally come from vyra_base
        if json_file.name in vyra_base_files:
            logging.debug(f"  ✓ {json_file.name} is from vyra_base – skipping reserved check")
            continue

        try:
            with open(json_file, "r") as f:
                data = json.load(f)
        except Exception as e:
            logging.warning(f"Could not parse {json_file.name}: {e}")
            continue

        if not isinstance(data, list):
            continue

        for item in data:
            func_name = item.get("functionname")
            if func_name and func_name in reserved_functions:
                error_msg = (
                    f"❌ RESERVED NAME: Function '{func_name}' in {json_file.name} "
                    f"is reserved by vyra_base "
                    f"(interface: {reserved_functions[func_name]}). "
                    f"Please rename your function."
                )
                errors.append(error_msg)
                logging.error(error_msg)

    return errors


def get_vyra_base_config_files() -> set:
    """Return the set of config file names shipped with vyra_base."""
    try:
        import vyra_base

        return vyra_base.get_vyra_base_config_files()
    except (ImportError, AttributeError):
        return set()


def load_default_interfaces(interface_package_name, interface_package_path):
    """Copy base interface files from the installed vyra_base package.

    Silently returns (no-op) when vyra_base is not installed.  Rust modules
    rely on setup_interfaces.sh to copy files from vyra_base_rust instead.
    """
    print(f"Load default interfaces: {interface_package_name}")
    try:
        import vyra_base
    except ImportError:
        logging.warning(
            "⚠️  vyra_base not available – skipping base interface extraction "
            "(expected in Rust/non-Python containers)."
        )
        return

    vyra_base.extract_interfaces(interface_package_path)

    # Fix resource marker file: remove old vyra_module_template_interfaces and create correct one
    resource_dir = interface_package_path / "resource"
    if resource_dir.exists():
        old_marker = resource_dir / "vyra_module_template_interfaces"
        if old_marker.exists():
            old_marker.unlink()
            print(f"✓ Removed old marker file: {old_marker}")

        # Create correct marker file
        correct_marker = resource_dir / interface_package_name
        if not correct_marker.exists():
            correct_marker.touch()
            print(f"✓ Created marker file: {correct_marker}")


# === Proto Interface Functions ===


def find_proto_files(interface_package_dir: Path) -> list[Path]:
    """Find all .proto files co-located in msg/, srv/, action/ directories."""
    proto_files: list[Path] = []
    for sub_dir in ["msg", "srv", "action"]:
        dir_path = interface_package_dir / sub_dir
        if dir_path.exists():
            proto_files.extend(dir_path.glob("*.proto"))
    logging.info(f"Found {len(proto_files)} .proto file(s) across msg/srv/action")
    for proto_file in proto_files:
        logging.info(f"   📄 {proto_file.parent.name}/{proto_file.name}")
    return proto_files


def generate_python_from_proto(proto_file: Path) -> bool:
    """Generate Python code from a .proto file using grpc_tools.protoc.
    Output files are placed next to the .proto file."""
    proto_dir = proto_file.parent
    output_dir = proto_file.parent / "_gen"

    output_dir.mkdir(parents=True, exist_ok=True)
    # Ensure _gen directory is owned by vyrauser (uid=1000) not root
    try:
        os.chown(output_dir, 1000, 1000)
    except PermissionError:
        logging.warning(f"⚠️ Could not chown {output_dir} to uid=1000 (no root access)")

    logging.info(f"🔧 Generating Python code for: {proto_file.parent.name}/{proto_file.name}")
    try:
        cmd = [
            "python3",
            "-m",
            "grpc_tools.protoc",
            f"--proto_path={proto_dir}",
            f"--python_out={output_dir}",
            f"--grpc_python_out={output_dir}",
            f"--pyi_out={output_dir}",
            str(proto_file),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        base_name = proto_file.stem
        pb2_file = output_dir / f"{base_name}_pb2.py"
        grpc_file = output_dir / f"{base_name}_pb2_grpc.py"
        if pb2_file.exists() and grpc_file.exists():
            logging.info(f"✅ Generated Python: {pb2_file.name}, {grpc_file.name}")
            return True
        else:
            logging.error("❌ Expected Python files not generated")
            return False
    except Exception as e:
        logging.error(f"❌ Error generating Python proto: {e}")
        return False


def generate_cpp_from_proto(proto_file: Path) -> bool:
    """Generate C++ code from a .proto file using protoc.
    Output files are placed next to the .proto file."""
    proto_dir = proto_file.parent
    output_dir = proto_file.parent / "_gen"

    output_dir.mkdir(parents=True, exist_ok=True)
    # Ensure _gen directory is owned by vyrauser (uid=1000) not root
    try:
        os.chown(output_dir, 1000, 1000)
    except PermissionError:
        logging.warning(f"⚠️ Could not chown {output_dir} to uid=1000 (no root access)")

    logging.info(f"🔧 Generating C++ code for: {proto_file.parent.name}/{proto_file.name}")
    try:
        protoc_check = subprocess.run(["which", "protoc"], capture_output=True, text=True)
        if protoc_check.returncode != 0:
            logging.warning("⚠️  protoc not found - skipping C++ generation")
            return False
        grpc_plugin_check = subprocess.run(
            ["which", "grpc_cpp_plugin"], capture_output=True, text=True
        )
        if grpc_plugin_check.returncode != 0:
            logging.debug("grpc_cpp_plugin not found - C++ generation skipped")
            return False
        cmd = [
            "python3",
            "-m",
            "grpc_tools.protoc",
            f"--proto_path={proto_dir}",
            f"--cpp_out={output_dir}",
            f"--grpc_out={output_dir}",
            f"--plugin=protoc-gen-grpc={grpc_plugin_check.stdout.strip()}",
            str(proto_file),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        base_name = proto_file.stem
        pb_h = output_dir / f"{base_name}.pb.h"
        pb_cc = output_dir / f"{base_name}.pb.cc"
        if pb_h.exists() and pb_cc.exists():
            logging.info(f"✅ Generated C++: {pb_h.name}, {pb_cc.name}")
            return True
        else:
            logging.warning("⚠️  C++ files not generated (optional)")
            return False
    except Exception as e:
        logging.error(f"❌ Error generating C++ proto: {e}")
        return False


def copy_proto_config_files(proto_interface_dir: Path, output_package_dir: Path):
    """Copy config files (metadata) to output package."""
    # Config files are in interfaces/config, not interfaces/proto/config
    interfaces_dir = proto_interface_dir.parent
    config_source = interfaces_dir / "config"
    config_dest = output_package_dir / "config"

    # Skip if source and dest are the same directory
    if config_source.resolve() == config_dest.resolve():
        logging.info("ℹ️  Config files already in place (same directory)")
        return

    if not config_source.exists():
        logging.info("ℹ️  No config directory found (optional)")
        return
    logging.info(f"📋 Copying config files from {config_source}...")
    config_dest.mkdir(parents=True, exist_ok=True)
    for config_file in config_source.glob("*.json"):
        dest_file = config_dest / config_file.name
        if dest_file.exists() and dest_file.samefile(config_file):
            continue  # Skip if source and dest are the same file
        shutil.copy2(config_file, dest_file)
        logging.info(f"   • {config_file.name}")


def create_proto_package_init(output_package_dir: Path, interface_pkg_name: str):
    """Create __init__.py for the proto interface package."""
    init_file = output_package_dir / "__init__.py"
    init_content = f'''"""\n{interface_pkg_name}\nAuto-generated Protocol Buffer interfaces for VYRA module communication.\nGenerated from .proto files using grpc_tools.protoc.\nCan be used with Redis, gRPC, or other transports.\n"""\n__all__ = []\n'''
    init_file.write_text(init_content)
    logging.info(f"✅ Created {init_file.name}")


def build_proto_interfaces(interface_package_path: Path, module_name: str):
    """Build proto interfaces for the module.
    .proto files are co-located in msg/, srv/, action/ and generated files
    are placed next to their corresponding .proto file.
    """
    try:
        proto_files = find_proto_files(interface_package_path)

        if not proto_files:
            logging.info(
                f"ℹ️  No .proto files found in {interface_package_path} - skipping proto generation"
            )
            return

        python_success_count = 0
        cpp_success_count = 0
        for proto_file in proto_files:
            logging.info(f"\n🔨 Processing: {proto_file.parent.name}/{proto_file.name}")
            if generate_python_from_proto(proto_file):
                python_success_count += 1
            if generate_cpp_from_proto(proto_file):
                cpp_success_count += 1

        if python_success_count == 0 and len(proto_files) > 0:
            logging.error("❌ No Python proto files were successfully generated")
        else:
            logging.info(f"\n✅ Proto interface setup complete!")
            logging.info(
                f"   Python: {python_success_count}/{len(proto_files)} interface(s) generated"
            )
            if cpp_success_count > 0:
                logging.info(
                    f"   C++:    {cpp_success_count}/{len(proto_files)} interface(s) generated"
                )
            else:
                logging.info(f"   C++:    Skipped (protoc not available or failed)")

        # Ensure action/_gen exists whenever msg/ or srv/ have a _gen directory
        action_dir = interface_package_path / "action"
        action_gen = action_dir / "_gen"
        if not action_gen.exists():
            action_dir.mkdir(exist_ok=True)
            action_gen.mkdir(exist_ok=True)
            try:
                os.chown(action_gen, 1000, 1000)
            except PermissionError:
                logging.warning("⚠️ Could not chown action/_gen to uid=1000")
            logging.info("✅ Created action/_gen (aligned with msg/srv _gen)")

    except Exception as e:
        logging.error(f"❌ Failed to build proto interfaces: {e}")
        logging.info("ℹ️  Continuing without proto interfaces (non-critical)")


# === Main Function ===


def generate_all_interfaces(interface_package_path: Path) -> bool:
    """
    Generate .msg / .srv / .action / .proto files from all JSON metadata configs.

    Loads ``InterfaceGenerator`` from vyra_base and runs it against the
    ``config/`` sub-directory of *interface_package_path*.  All ``*_meta.json``
    files found there are processed to produce the corresponding ROS2 and
    Protobuf interface files in the ``msg/``, ``srv/``, and ``action/``
    directories.

    Args:
        interface_package_path: Root of the ROS2 interface package (contains
            ``config/``, ``msg/``, ``srv/``, ``action/``).

    Returns:
        bool: True on success, False if the generator reported errors.
    """
    try:
        from vyra_base.interfaces.tools.generate_interfaces import InterfaceGenerator
    except ImportError:
        logging.warning(
            "⚠️  InterfaceGenerator not available (vyra_base missing) – "
            "skipping interface generation (expected in Rust/non-Python containers). "
            "The Rust setup_interfaces.sh runs generate_interfaces.py from vyra_base_rust."
        )
        return True  # non-fatal: treat as success

    logging.info(f"🔨 Generating interfaces from config JSONs in {interface_package_path}...")
    generator = InterfaceGenerator(interface_package_path)
    return_code = generator.run()

    if return_code != 0:
        logging.error("❌ Interface generation reported errors (see above)")
        return False

    logging.info("✅ Interface generation complete")
    return True


def main(interface_package_name: str):
    """
    Set up or update a ROS2 interface package for a VYRA module.

    Pipeline:
        1. Copy config + build files from the installed vyra_base library.
        2. Validate module-specific config JSONs against the RESERVED list.
        2.5 Validate all ``*_meta.json`` files against the JSON schema at
            ``vyra_base/assets/schemas/interface_config.json``.  Files that
            fail are excluded from interface generation with a WARNING log
            entry (non-fatal) and restored to their original name afterwards.
        3. Generate msg / srv / action / .proto files via InterfaceGenerator.
        4. Update CMakeLists.txt and package.xml.
        5. Compile proto → Python / C++ stubs.

    Args:
        interface_package_name: Name of the ROS2 interface package
            (e.g. ``v2_modulemanager_interfaces``).
    """
    # Workspace setup - detect if we're in container or local development
    script_dir = Path(__file__).parent.parent  # Go up from tools/ to module root
    if (script_dir / "src").exists() or (script_dir / ".module").exists():
        workspace_root = script_dir  # local development (any module)
    else:
        workspace_root = Path("/workspace")  # inside container

    interface_package_path: Path = workspace_root / "src" / interface_package_name

    # Validate / create package directory
    if not interface_package_path.exists():
        if not interface_package_name.endswith("_interfaces"):
            raise FileNotFoundError(
                f"Interface package '{interface_package_name}' not "
                f"found in {workspace_root}/src. "
                "Check the package name or create the directory manually."
            )
    interface_package_path.mkdir(exist_ok=True)

    # ── Step 1: Copy config + build files from vyra_base ────────────────────
    cmake_exists = (interface_package_path / "CMakeLists.txt").exists()
    if cmake_exists:
        logging.info("📥 Updating config files from vyra_base (existing package)...")
    else:
        logging.info("📥 Loading config files from vyra_base (new package)...")
    load_default_interfaces(interface_package_name, interface_package_path)

    # ── Step 1.5: Remove stale old-convention base files ─────────────────────
    # Old vyra_base used underscore naming (e.g. vyra_com_meta.json).
    # New vyra_base uses dot naming (e.g. vyra_com.meta.json).
    # After extract_interfaces(), clean up any stale underscore copies so the
    # reserved-name check does not flag them as module-specific violations.
    config_path = interface_package_path / "config"
    vyra_base_files = get_vyra_base_config_files()
    for _base_file in vyra_base_files:
        if _base_file.endswith(".meta.json"):
            _old_name = _base_file.replace(".meta.json", "_meta.json")
            _old_file = config_path / _old_name
            if _old_file.exists():
                _old_file.unlink()
                logging.info(f"🗑️  Removed stale old-named base file: {_old_name}")

    # ── Step 2: Validate module-specific configs against RESERVED list ───────
    logging.info("🔍 Checking module-specific config JSONs for reserved function names...")
    reserved_interfaces = load_reserved_list()
    violations = check_reserved_function_names(config_path, reserved_interfaces, vyra_base_files)
    if violations:
        logging.error("\n" + "=" * 70)
        logging.error("❌ RESERVED FUNCTION NAMES DETECTED IN MODULE CONFIG")
        logging.error("=" * 70)
        for msg in violations:
            logging.error(msg)
        logging.error("=" * 70)
        logging.error("Please rename the affected functions before continuing.")
        sys.exit(1)
    else:
        logging.info("✓ No reserved function name violations detected")

    # ── Step 2.5: Validate config JSONs against interface_config schema ──────
    logging.info("🔍 Validating config JSONs against interface_config schema...")
    _renamed_invalid: list[tuple[Path, Path]] = []  # [(renamed_path, original_path)]
    try:
        import vyra_base as _vyra_base_module

        _meta_jsons = list(config_path.glob("*.meta.json"))
        _valid_jsons, _invalid_jsons = _vyra_base_module.validate_config_schema(_meta_jsons)
        if _invalid_jsons:
            for _bad_file, _reason in _invalid_jsons:
                logging.warning(
                    "⚠️  Schema validation failed – '%s' will be skipped in "
                    "interface generation.\n    Reason: %s",
                    _bad_file.name,
                    _reason,
                )
            # Temporarily rename invalid files so InterfaceGenerator skips them
            for _bad_file, _ in _invalid_jsons:
                _renamed = _bad_file.with_name(_bad_file.name + ".invalid")
                _bad_file.rename(_renamed)
                _renamed_invalid.append((_renamed, _bad_file))
            logging.warning(
                "⚠️  %d config file(s) excluded from this build due to schema "
                "violations (files will be restored after generation).",
                len(_invalid_jsons),
            )
        else:
            logging.info("✓ All config JSONs are schema-valid")
    except Exception as _schema_exc:
        logging.warning(
            "⚠️  Schema validation step raised an unexpected error: %s – "
            "continuing without schema validation.",
            _schema_exc,
        )

    # ── Step 3: Generate msg / srv / action / .proto from config JSONs ───────
    vyra_slim = os.environ.get("VYRA_SLIM", "false").lower() == "true"
    if vyra_slim:
        logging.info(
            "⚡ SLIM mode: Skipping InterfaceGenerator (.msg/.srv/.action), only proto stubs needed"
        )
        for _renamed_path, _original_path in _renamed_invalid:
            if _renamed_path.exists():
                _renamed_path.rename(_original_path)
        if _renamed_invalid:
            logging.info(
                "♻️  Restored %d temporarily renamed file(s) after skipping interface generation.",
                len(_renamed_invalid),
            )
    else:
        try:
            if not generate_all_interfaces(interface_package_path):
                logging.error("❌ Interface generation failed – aborting.")
                sys.exit(1)
        finally:
            # Always restore files that were temporarily renamed due to schema violations
            for _renamed_path, _original_path in _renamed_invalid:
                if _renamed_path.exists():
                    _renamed_path.rename(_original_path)
            if _renamed_invalid:
                logging.info(
                    "♻️  Restored %d temporarily renamed file(s) after interface generation.",
                    len(_renamed_invalid),
                )

    # ── Step 4: Update CMakeLists.txt and package.xml (ROS2 only, skip in SLIM) ──
    if vyra_slim:
        logging.info(
            "⚡ SLIM mode: Skipping CMakeLists.txt / package.xml update (no ROS2 colcon build)"
        )
    else:
        print(f"\nUpdate package.xml for {interface_package_name}")
        update_package_xml(interface_package_path, interface_package_name)

        print(f"\nUpdate CMakefile for {interface_package_name}")
        update_CMakefile(interface_package_path, interface_package_name)

        interface_files = collect_interface_files(interface_package_path)
        for file_collection in interface_files.values():
            for file in file_collection:
                print(interface_package_path / file)
                replace_libname_in_file(
                    interface_package_path / file, "vyra_base", interface_package_name
                )

        print(f"✓ Package '{interface_package_name}' updated successfully!")

    # ── Step 5: Compile proto stubs (Python + optional C++) ──────────────────
    try:
        proto_files = find_proto_files(interface_package_path)

        if not proto_files:
            logging.info(f"ℹ️  No .proto files found – skipping proto stub compilation")
        else:
            logging.info("📁 Compiling proto stubs (msg/, srv/, action/ co-located)...")
            python_ok = 0
            cpp_ok = 0
            for proto_file in proto_files:
                logging.info(f"\n🔨 Processing: {proto_file.parent.name}/{proto_file.name}")
                if generate_python_from_proto(proto_file):
                    python_ok += 1
                if generate_cpp_from_proto(proto_file):
                    cpp_ok += 1

            if python_ok == 0:
                logging.error("❌ No Python proto stubs were successfully compiled")
            else:
                logging.info(f"\n✅ Proto stub compilation complete!")
                logging.info(f"   Python: {python_ok}/{len(proto_files)} compiled")
                if cpp_ok:
                    logging.info(f"   C++:    {cpp_ok}/{len(proto_files)} compiled")
                else:
                    logging.info("   C++:    Skipped (protoc not available)")

            # Ensure action/_gen exists to keep structure consistent
            action_gen = interface_package_path / "action" / "_gen"
            if not action_gen.exists():
                action_gen.parent.mkdir(exist_ok=True)
                action_gen.mkdir(exist_ok=True)
                try:
                    os.chown(action_gen, 1000, 1000)
                except PermissionError:
                    logging.warning("⚠️ Could not chown action/_gen to uid=1000")
                logging.info("✅ Created action/_gen")

    except Exception as e:
        logging.error(f"❌ Proto stub compilation failed: {e}")
        logging.info("ℹ️  Continuing without proto stubs (non-critical)")


if __name__ == "__main__":
    args = parse_args()

    # Note: ROS2 interface files (.msg/.srv/.action) and proto stubs are now
    # always generated together from the same JSON metadata via InterfaceGenerator.
    # The --ros2-only / --proto-only / --all flags are kept for CLI compatibility
    # but the unified pipeline always runs both.
    logging.info("\n" + "=" * 70)
    logging.info("📦 Building VYRA Interfaces")
    logging.info("=" * 70)

    main(args.interface_pkg)

    logging.info("\n" + "=" * 70)
    logging.info("✅ Interface setup complete!")
    logging.info("=" * 70)
    logging.info(f"   Package: {args.interface_pkg}")
    logging.info("=" * 70)
