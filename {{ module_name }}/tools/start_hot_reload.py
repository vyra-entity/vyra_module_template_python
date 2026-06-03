#!/usr/bin/env python3

"""
Hot Reload Watcher (Python modules):

start_hot_reload.py — Hot Reload Watcher (Python modules)
Unified for SLIM and Full (ROS2) modes. Started in background by startup_slim_core.sh.
Monitors source files and automatically rebuilds and restarts processes via Supervisord

Supports:
- FULL Mode (VYRA_SLIM=false): Monitors Python + ROS2 interface files, rebuilds with colcon
- SLIM Mode (VYRA_SLIM=true): Monitors Python files, restarts via supervisord only
"""
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)
logger.level = logging.DEBUG


class HotReloadHandler(FileSystemEventHandler):
    """Handles file system events and triggers rebuild/restart via Supervisord"""

    def __init__(
            self, workspace_path: str, package_name: str, node_name: str, 
            debounce_seconds: float = 5.0, supervisord_program: str = "core",
            slim_mode: bool = False):

        self.workspace_path = Path(workspace_path)
        self.package_name = package_name
        self.node_name = node_name
        self.debounce_seconds = debounce_seconds
        self.last_trigger_time = 0
        self.pending_rebuild = False
        self.is_building = False
        self.last_modified_file = None
        self.last_modified_time = 0
        self.last_build_time = 0  # Track when builds complete
        self.supervisord_program = supervisord_program
        # Wait up to 60 s for supervisord to start (hot_reload launches before supervisord)
        self.use_supervisord = self._check_supervisord_available(max_wait=60)
        self._supervisord_conf_path = getattr(self, "_supervisord_conf_path", "/etc/supervisor/conf.d/supervisord.conf")
        self.interface_files_changed = False  # Track if interface files changed
        self.slim_mode = slim_mode  # True = no colcon build, False = full ROS2 build

        mode_str = "SLIM (Python-only)" if slim_mode else "FULL (ROS2)"
        logger.info(f"🔥 Hot Reload initialized in {mode_str} mode")
        logger.info(f"   Package: {package_name}, Node: {node_name}")
        logger.info(f"   Supervisord Program: {supervisord_program}")
        logger.info(f"   Using Supervisord: {self.use_supervisord}")

    def on_modified(self, event):
        """Called when a file is modified"""
        # Ignore ALL events while building to prevent loops from setup_interfaces.py
        if self.is_building:
            return

        if event.is_directory:
            return

        # Watch Python files and ROS2 interface files (.srv, .msg, .action)
        file_path = Path(event.src_path)

        # Ignore install, build, log directories, and generated protobuf files
        path_str = str(file_path)

        logger.debug(f"File modified: {path_str}")

        if any(excluded in path_str for excluded in ['/install/', '/build/', '/log/', '/_gen/']):
            logger.debug(f"🚫 Ignoring file in excluded directory: {path_str}")
            return

        # Ignore generated protobuf/grpc stubs by filename suffix
        if file_path.suffix in ['.pyi'] or '_pb2' in file_path.stem or '_pb2_grpc' in file_path.stem:
            logger.debug(f"🚫 Ignoring generated protobuf file: {path_str}")
            return

        # Check if it's a Python file in the main package
        # Use exact path segment match (not substring) to avoid matching
        # v2_modulemanager_interfaces when package_name is v2_modulemanager
        is_python_file = (
            file_path.suffix == '.py' and
            f'/src/{self.package_name}/' in path_str
        )

        # Check if it's a ROS2 interface file (.srv, .msg, .action) in any package
        # Only monitor interface files in FULL mode
        is_interface_file = False
        is_config_file = False
        if not self.slim_mode:
            is_interface_file = (
                file_path.suffix in ['.srv', '.msg', '.action'] and 
                '/src/' in path_str and
                any(iface_dir in path_str for iface_dir in ['/srv/', '/msg/', '/action/'])
            )
            # Also watch JSON config files inside interface packages
            # (e.g. vyra_com.meta.json) which are installed via colcon
            is_config_file = (
                file_path.suffix == '.json' and
                '/src/' in path_str and
                '/config/' in path_str
            )

            # Ignore interface/config file events for 60 seconds after build completes
            # This prevents loops from setup_interfaces.py modifying interface files
            # 60s because a colcon build takes ~50s and file events can arrive late
            if (is_interface_file or is_config_file) and (time.time() - self.last_build_time < 60.0):
                return

        if is_python_file or is_interface_file or is_config_file:
            # Check for duplicate event (same file within 10 seconds)
            # This catches multiple save events from editors (save, auto-save, format-on-save)
            current_time = time.time()
            if (self.last_modified_file == path_str and 
                current_time - self.last_modified_time < 10.0):
                return

            self.last_modified_file = path_str
            self.last_modified_time = current_time

            file_type = "interface/config" if (is_interface_file or is_config_file) else "Python"
            logger.info(f"📝 {file_type} file changed: {file_path}")
            if is_interface_file or is_config_file:
                self.interface_files_changed = True
            self._schedule_rebuild()

    def on_created(self, event):
        """Called when a file is created"""
        # Ignore ALL events while building to prevent loops from setup_interfaces.py
        if self.is_building:
            return

        if event.is_directory:
            return

        file_path = Path(event.src_path)
        path_str = str(file_path)

        # Ignore install, build, log directories, and generated protobuf files
        if any(excluded in path_str for excluded in ['/install/', '/build/', '/log/', '/_gen/']):
            return

        # Ignore generated protobuf/grpc stubs by filename suffix
        if file_path.suffix in ['.pyi'] or '_pb2' in file_path.stem or '_pb2_grpc' in file_path.stem:
            return

        # Check if it's a Python file in the main package
        # Use exact path segment match (not substring) to avoid matching
        # v2_modulemanager_interfaces when package_name is v2_modulemanager
        is_python_file = (
            file_path.suffix == '.py' and
            f'/src/{self.package_name}/' in path_str
        )

        # Check if it's a ROS2 interface file (.srv, .msg, .action) in any package
        # Only monitor interface files in FULL mode
        is_interface_file = False
        is_config_file = False
        if not self.slim_mode:
            is_interface_file = (
                file_path.suffix in ['.srv', '.msg', '.action'] and 
                '/src/' in path_str and
                any(iface_dir in path_str for iface_dir in ['/srv/', '/msg/', '/action/'])
            )
            is_config_file = (
                file_path.suffix == '.json' and
                '/src/' in path_str and
                '/config/' in path_str
            )

            # Ignore interface/config file events for 60 seconds after build completes
            if (is_interface_file or is_config_file) and (time.time() - self.last_build_time < 60.0):
                return

        if is_python_file or is_interface_file or is_config_file:
            file_type = "interface/config" if (is_interface_file or is_config_file) else "Python"
            logger.info(f"➕ {file_type} file created: {file_path}")
            if is_interface_file or is_config_file:
                self.interface_files_changed = True

            self._schedule_rebuild()

    def _schedule_rebuild(self):
        """Schedule a rebuild with debouncing"""
        # Skip if already building
        if self.is_building:
            logger.debug("🔒 Build already in progress, skipping trigger")
            return

        current_time = time.time()

        # Debounce: Only trigger if enough time has passed
        if current_time - self.last_trigger_time < self.debounce_seconds:
            self.pending_rebuild = True
            return

        self.last_trigger_time = current_time
        self.pending_rebuild = False
        self._trigger_rebuild()

    def _trigger_rebuild(self):
        """Execute the rebuild and restart process via Supervisord"""
        self.is_building = True
        logger.info("🔨 Starting reload process...")

        # Step 1: Stop running process via Supervisord
        # Re-check availability in case supervisord was not yet ready at startup
        if not self.use_supervisord:
            self.use_supervisord = self._check_supervisord_available(max_wait=10)
        if self.use_supervisord:
            logger.info(f"⏹️ Stopping Supervisord program: {self.supervisord_program}")
            self._supervisorctl("stop", self.supervisord_program)
            time.sleep(1)  # Wait for graceful shutdown

            # Additional cleanup: Kill any remaining processes
            logger.info("🧹 Cleaning up remaining processes...")
            self._kill_remaining_processes()
            time.sleep(0.5)

        # Step 2: Build the package (only in FULL mode with ROS2)
        if not self.slim_mode:
            logger.info(f"🔧 Building package: {self.package_name}")
            build_result = self._build_package()

            if build_result != 0:
                logger.error(f"❌ Build failed with exit code {build_result}")
                if not self.use_supervisord:
                    self.use_supervisord = self._check_supervisord_available(max_wait=10)
                if self.use_supervisord:
                    # Restart even if build failed to restore service
                    logger.warning("⚠️ Restarting process despite build failure...")
                    self._supervisorctl("start", self.supervisord_program)
                self.is_building = False
                return

            logger.info("✅ Build successful")

            # Sync updated interfaces to NFS so other modules can discover them
            logger.info("🔗 Updating NFS interfaces...")
            self._update_nfs_interfaces()
        else:
            logger.info("⏸️ SLIM mode: Skipping colcon build")

        # Step 3: Clear Python module cache to force reload
        logger.info("🧹 Clearing Python module cache...")
        self._clear_python_cache()
        self._clear_logs()

        # Step 4: Restart the process via Supervisord
        # Re-check availability in case supervisord was not yet ready at startup
        if not self.use_supervisord:
            self.use_supervisord = self._check_supervisord_available(max_wait=10)
        # Reset timestamps BEFORE restarting the service so that any file-system
        # events generated during or right after the supervisor start (e.g. from
        # ROS2 sourcing install/setup.bash) fall inside the cooldown window and
        # are ignored.
        self.last_modified_time = time.time()
        self.last_build_time = time.time()  # Start cooldown period
        self.is_building = False

        if self.use_supervisord:
            logger.info(f"🚀 Restarting Supervisord program: {self.supervisord_program}")
            self._supervisorctl("start", self.supervisord_program)
            logger.info(f"✅ Process restarted via Supervisord")
        else:
            logger.warning("⚠️ Supervisord not available, manual restart required")

    def _build_package(self) -> int:
        """Build the ROS2 package (Full mode only)"""
        try:
            # Step 1: If interface files changed, run setup_interfaces.py first
            if self.interface_files_changed:
                logger.info("🔧 Interface files changed, running setup_interfaces.py...")
                self._run_setup_interfaces()
                self.interface_files_changed = False  # Reset flag after processing
                # Reset last_modified_time to ignore changes made by setup_interfaces.py
                self.last_modified_time = time.time()
                time.sleep(1)  # Wait for filesystem to settle

            # Step 2: Clean build directory and egg-info only (NOT the install
            # directory). Removing the entire install/v2_dashboard while a colcon
            # build is running creates a race-condition: if Docker Swarm restarts
            # the container mid-build, the entrypoint finds the install directory
            # empty and restores the stale image backup, discarding source fixes.
            # Deleting only the build dir + egg-info forces pip to reinstall from
            # source without ever leaving the install tree in a broken state.
            packages_to_clean = [
                self.package_name,
                f'{self.package_name}_interfaces'
            ]

            for pkg in packages_to_clean:
                # Remove stale distribution metadata (egg-info / dist-info) from
                # the install tree so pip treats the package as not yet installed
                # and performs a full file copy.  Colcon may generate either
                # format depending on the setuptools version.
                site_pkgs = (
                    self.workspace_path / "install" / pkg
                    / "lib"
                )
                if site_pkgs.exists():
                    for meta_dir in list(site_pkgs.rglob(f"{pkg}-*.egg-info")) + list(site_pkgs.rglob(f"{pkg}-*.dist-info")):
                        logger.info(f"🧹 Removing dist metadata: {meta_dir}")
                        shutil.rmtree(meta_dir)

                # Clean build directory to remove cached build info
                build_dir = self.workspace_path / "build" / pkg
                if build_dir.exists():
                    logger.info(f"🧹 Cleaning build directory: {build_dir}")
                    shutil.rmtree(build_dir)

            # Step 3: Build all packages to ensure dependencies are up-to-date
            # Source the ROS2 environment first so ament/cmake tools are available
            ros2_setup = "/opt/ros/kilted/setup.bash"
            build_cmd = (
                f"source {ros2_setup} && "
                "colcon --log-base log/ros2 build --cmake-args -DCMAKE_BUILD_TYPE=Release"
            )
            result = subprocess.run(
                ["bash", "-c", build_cmd],
                cwd=self.workspace_path,
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                logger.error(f"Build stderr: {result.stderr}")
            else:
                logger.debug(f"Build stdout: {result.stdout}")

            return result.returncode
        except Exception as e:
            logger.exception(f"Build exception: {e}")
            return 1

    def _update_nfs_interfaces(self) -> None:
        """
        Sync the colcon-built interface artifacts to NFS after a successful build.
        Mirrors the NFS push logic from vyra_entrypoint.sh so other modules
        immediately see updated ROS2 services / message types.
        """
        try:
            nfs_volume = os.environ.get("NFS_VOLUME_PATH", "/nfs/vyra_interfaces")
            if not Path(nfs_volume).is_dir():
                logger.debug(f"ℹ️ NFS volume not found at {nfs_volume} — skipping NFS update")
                return

            # Determine instance_id (uuid) from .module/module_data.yaml or hostname
            instance_id = ""
            module_data_file = self.workspace_path / ".module" / "module_data.yaml"
            if module_data_file.exists():
                for line in module_data_file.read_text().splitlines():
                    if line.startswith("uuid:"):
                        instance_id = line.split(":", 1)[1].strip()
                        break
            if not instance_id:
                import socket
                hostname = socket.gethostname()
                prefix = f"{self.package_name}_"
                instance_id = hostname[len(prefix):] if hostname.startswith(prefix) else hostname

            interface_dir_name = f"{self.package_name}_{instance_id}_interfaces"
            nfs_module_dir = Path(nfs_volume) / interface_dir_name
            nfs_ros_dir = nfs_module_dir / "ros2"
            nfs_config_dir = nfs_module_dir / "config"
            nfs_msg_dir = nfs_module_dir / "msg"
            nfs_srv_dir = nfs_module_dir / "srv"
            nfs_action_dir = nfs_module_dir / "action"

            for d in [nfs_ros_dir, nfs_config_dir, nfs_msg_dir, nfs_srv_dir, nfs_action_dir]:
                d.mkdir(parents=True, exist_ok=True)

            # 1. ROS2 colcon install tree → ros2/
            iface_install = self.workspace_path / "install" / f"{self.package_name}_interfaces"
            if iface_install.is_dir():
                try:
                    if shutil.which("rsync"):
                        subprocess.run(
                            ["rsync", "-a", "--delete",
                             f"{iface_install}/", f"{nfs_ros_dir}/"],
                            capture_output=True, timeout=60
                        )
                    else:
                        if nfs_ros_dir.exists():
                            shutil.rmtree(str(nfs_ros_dir))
                        shutil.copytree(str(iface_install), str(nfs_ros_dir))
                    logger.info(f"✅ NFS ros2/ updated for {interface_dir_name}")
                except Exception as e:
                    logger.warning(f"⚠️ Failed to sync ros2/ to NFS: {e}")

                # 2. JSON config → config/
                config_src = iface_install / "share" / f"{self.package_name}_interfaces" / "config"
                if config_src.is_dir():
                    try:
                        if shutil.which("rsync"):
                            subprocess.run(
                                ["rsync", "-a", "--delete",
                                 f"{config_src}/", f"{nfs_config_dir}/"],
                                capture_output=True, timeout=30
                            )
                        else:
                            for f in config_src.iterdir():
                                shutil.copy2(str(f), str(nfs_config_dir / f.name))
                        logger.info("✅ NFS config/ updated")
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to sync config/ to NFS: {e}")
            else:
                logger.warning(f"⚠️ Interface install source not found: {iface_install}")

            # 3. Raw interface definitions (msg / srv / action) → NFS sub-directories
            iface_src_dir = self.workspace_path / "src" / f"{self.package_name}_interfaces"
            for sub, nfs_sub in [
                ("msg", nfs_msg_dir), ("srv", nfs_srv_dir), ("action", nfs_action_dir)
            ]:
                src_sub = iface_src_dir / sub
                if src_sub.is_dir():
                    try:
                        if shutil.which("rsync"):
                            subprocess.run(
                                ["rsync", "-a", "--delete",
                                 f"{src_sub}/", f"{nfs_sub}/"],
                                capture_output=True, timeout=30
                            )
                        else:
                            if nfs_sub.exists():
                                shutil.rmtree(str(nfs_sub))
                            shutil.copytree(str(src_sub), str(nfs_sub))
                        logger.info(f"✅ NFS {sub}/ updated")
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to sync {sub}/ to NFS: {e}")

            logger.info(f"✅ NFS interfaces fully updated at {nfs_module_dir}")

        except Exception as exc:
            logger.error(f"❌ _update_nfs_interfaces failed: {exc}")

    def _check_supervisord_available(self, max_wait: float = 0) -> bool:
        """Check if supervisorctl is available.

        Args:
            max_wait: Maximum seconds to wait for supervisord to become available.
                      0 = single attempt without waiting, >0 = keep retrying until
                      supervisord responds or the timeout elapses.
        """
        deadline = time.time() + max_wait
        attempt = 0
        # Try both standard and workspace-local config paths
        conf_paths = [
            "/etc/supervisor/conf.d/supervisord.conf",
            "/workspace/supervisord.conf",
        ]
        while True:
            attempt += 1
            for conf_path in conf_paths:
                try:
                    result = subprocess.run(
                        ["supervisorctl", "-c", conf_path, "status"],
                        capture_output=True,
                        timeout=5
                    )
                    # Exit code 0 = all processes running
                    # Exit code 3 = some processes stopped (e.g., nginx) - still available!
                    if result.returncode in [0, 3]:
                        logger.info(f"✅ Supervisord is available (config: {conf_path})")
                        self._supervisord_conf_path = conf_path
                        return True
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass

            remaining = deadline - time.time()
            if remaining <= 0:
                if max_wait > 0:
                    logger.warning(
                        f"⚠️ Supervisord not responding after {max_wait:.0f}s "
                        f"({attempt} attempts)"
                    )
                else:
                    logger.warning("⚠️ Supervisord not responding")
                return False

            wait_time = min(3.0, remaining)
            logger.debug(
                f"⏳ Waiting for supervisord (attempt {attempt}, "
                f"{remaining:.0f}s remaining)..."
            )
            time.sleep(wait_time)

    def _supervisorctl(self, action: str, program: str) -> bool:
        """Execute supervisorctl command with explicit config file"""
        conf_path = getattr(self, "_supervisord_conf_path", "/etc/supervisor/conf.d/supervisord.conf")
        try:
            result = subprocess.run(
                ["supervisorctl", "-c", conf_path, action, program],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                logger.warning(f"supervisorctl {action} {program} failed: {result.stderr}")
                return False

            logger.info(f"✅ supervisorctl {action} {program} successful")
            return True
        except Exception as e:
            logger.error(f"supervisorctl command failed: {e}")
            return False

    def _run_setup_interfaces(self):
        """Run setup_interfaces.py to update CMakeLists.txt and package.xml"""
        try:
            setup_script = self.workspace_path / "tools" / "setup_interfaces.py"
            if not setup_script.exists():
                logger.warning(f"⚠️ setup_interfaces.py not found at {setup_script}")
                return

            # Run setup_interfaces.py for interface package
            interface_pkg = f"{self.package_name}_interfaces"
            result = subprocess.run(
                ["python3", str(setup_script), "--interface_pkg", interface_pkg],
                cwd=self.workspace_path,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                logger.info("✅ setup_interfaces.py completed successfully")
            else:
                logger.error(f"❌ setup_interfaces.py failed: {result.stderr}")

        except Exception as e:
            logger.error(f"Exception running setup_interfaces.py: {e}")

    def _kill_remaining_processes(self):
        """Kill any remaining processes from the package to prevent process leaks"""
        try:
            # Find all processes matching the package core executable
            result = subprocess.run(
                ["pgrep", "-f", f"{self.package_name}/core"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                logger.info(f"🔍 Found {len(pids)} remaining processes to clean up")

                for pid in pids:
                    try:
                        # Send SIGTERM first (graceful)
                        subprocess.run(
                            ["kill", "-TERM", pid],
                            capture_output=True,
                            timeout=2
                        )
                        logger.debug(f"Sent SIGTERM to PID {pid}")
                    except Exception as e:
                        logger.warning(f"Failed to kill PID {pid}: {e}")

                # Wait a bit for graceful shutdown
                time.sleep(1)

                # Check if any processes are still alive and force kill
                result = subprocess.run(
                    ["pgrep", "-f", f"{self.package_name}/core"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )

                if result.returncode == 0 and result.stdout.strip():
                    remaining_pids = result.stdout.strip().split('\n')
                    logger.warning(f"⚠️ {len(remaining_pids)} processes still alive, force killing...")

                    for pid in remaining_pids:
                        try:
                            subprocess.run(
                                ["kill", "-KILL", pid],
                                capture_output=True,
                                timeout=2
                            )
                            logger.debug(f"Sent SIGKILL to PID {pid}")
                        except Exception as e:
                            logger.warning(f"Failed to force kill PID {pid}: {e}")

                    logger.info("✅ All remaining processes terminated")
                else:
                    logger.info("✅ All processes terminated gracefully")
            else:
                logger.debug("No remaining processes found")

        except Exception as e:
            logger.warning(f"⚠️ Process cleanup failed: {e}")

    def _clear_python_cache(self):
        """Clear Python __pycache__ directories to force module reload"""
        try:
            # Find and remove all __pycache__ directories in package
            package_path = self.workspace_path / "src" / self.package_name

            result = subprocess.run(
                ["find", str(package_path), "-type", "d", "-name", "__pycache__", "-exec", "rm", "-rf", "{}", "+"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                logger.debug("✅ Python cache cleared")
            else:
                logger.warning(f"⚠️ Failed to clear cache: {result.stderr}")

        except Exception as e:
            logger.warning(f"⚠️ Cache clearing failed: {e}")

    def _clear_logs(self):
        """Clear ROS2 build log artifacts and append a restart separator to application logs."""
        try:
            # Remove only ROS2/colcon build artifacts — NOT application logs
            result = subprocess.run(
                f"rm -rf {self.workspace_path}/log/build_* "
                f"{self.workspace_path}/log/ros2/build_* "
                f"{self.workspace_path}/log/ros2/latest "
                f"{self.workspace_path}/log/ros2/latest_build",
                shell=True,
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                logger.debug("✅ ROS2 build logs cleared")
            else:
                logger.warning(f"⚠️ Failed to clear ROS2 build logs: {result.stderr}")

            # Append a restart separator to persistent application log files
            separator = (
                f"\n{'='*80}\n"
                f"=== HOT RELOAD RESTART: {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n"
                f"{'='*80}\n\n"
            )
            for log_file in [
                self.workspace_path / "log" / "core" / "core_stdout.log",
                self.workspace_path / "log" / "core" / "errors.log",
            ]:
                try:
                    with open(log_file, 'a') as lf:
                        lf.write(separator)
                except Exception as fe:
                    logger.debug(f"Could not write separator to {log_file}: {fe}")

        except Exception as e:
            logger.warning(f"⚠️ Log clearing failed: {e}")

    def _check_initial_sync(self) -> None:
        """
        At startup, compare the src interface tree with the compiled install tree.
        If they differ (e.g. a new .srv was added before the container started),
        schedule a rebuild so the new interfaces are compiled and pushed to NFS.
        """
        try:
            iface_src_dir = self.workspace_path / "src" / f"{self.package_name}_interfaces"
            iface_install = self.workspace_path / "install" / f"{self.package_name}_interfaces"
            python_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
            rebuild_needed = False

            for iface_type in ["srv", "msg", "action"]:
                src_dir = iface_src_dir / iface_type
                if not src_dir.is_dir():
                    continue
                # Valid ROS2 interface names start with an uppercase letter
                src_count = len([
                    f for f in src_dir.glob(f"*.{iface_type}")
                    if f.stem[:1].isupper()
                ])
                installed_dir = (
                    iface_install / "lib" / python_ver / "site-packages"
                    / f"{self.package_name}_interfaces" / iface_type
                )
                # Compiled Python modules are named _snake_case.py (exclude __init__.py)
                installed_count = (
                    len([f for f in installed_dir.glob("_*.py") if f.name != "__init__.py"])
                    if installed_dir.is_dir() else 0
                )
                if src_count != installed_count:
                    logger.info(
                        f"🔍 Startup sync: {iface_type}/ "
                        f"(src={src_count}, installed={installed_count}) "
                        f"— scheduling initial rebuild"
                    )
                    rebuild_needed = True
                    break

            if not rebuild_needed:
                src_config = iface_src_dir / "config"
                install_config = (
                    iface_install / "share" / f"{self.package_name}_interfaces" / "config"
                )
                src_json = len(list(src_config.glob("*.json"))) if src_config.is_dir() else 0
                inst_json = len(list(install_config.glob("*.json"))) if install_config.is_dir() else 0
                if src_json != inst_json:
                    logger.info(
                        f"🔍 Startup sync: config/ "
                        f"(src={src_json}, installed={inst_json}) "
                        f"— scheduling initial rebuild"
                    )
                    rebuild_needed = True

            if not rebuild_needed:
                # Verify that the package itself is installed in the install tree.
                # Colcon uses setup.py install (egg-based), which does NOT create
                # dist-info or egg-info in site-packages — the egg metadata lives in
                # the source tree (src/<pkg>/<pkg>.egg-info/) and is picked up via
                # PYTHONPATH.  Checking for dist-info here would ALWAYS trigger a
                # spurious rebuild because colcon never writes it to install/.
                # Instead, verify that the package directory exists in site-packages.
                site_pkgs = (
                    self.workspace_path / "install" / self.package_name
                    / "lib" / python_ver / "site-packages"
                )
                install_pkg_dir = site_pkgs / self.package_name
                if not install_pkg_dir.is_dir():
                    logger.info(
                        f"🔍 Startup sync: package not installed at {install_pkg_dir} "
                        f"— scheduling initial rebuild"
                    )
                    rebuild_needed = True

            if rebuild_needed:
                self.interface_files_changed = True
                import threading
                threading.Timer(10.0, self._schedule_rebuild).start()
                logger.info("⏳ Initial rebuild scheduled in 10 s (interface files out of sync)")
            else:
                logger.info("✅ Startup sync check: install tree up-to-date")

        except Exception as exc:
            logger.warning(f"⚠️ Startup sync check failed: {exc}")

    def check_pending_rebuild(self):
        """Check if there's a pending rebuild to execute"""
        if self.pending_rebuild:
            current_time = time.time()
            if current_time - self.last_trigger_time >= self.debounce_seconds:
                self.pending_rebuild = False
                self.last_trigger_time = current_time
                self._trigger_rebuild()


def main():
    """Main entry point"""
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Get configuration from environment or arguments
    workspace_path = os.getenv("WORKSPACE_PATH", "/workspace")
    package_name = os.getenv("VYRA_PACKAGE_NAME", "v2_modulemanager")
    node_name = os.getenv("VYRA_NODE_NAME", "core")
    watch_path = os.getenv("VYRA_WATCH_PATH", f"{workspace_path}/src")
    supervisord_program = os.getenv("VYRA_SUPERVISORD_PROGRAM", "core")
    slim_mode = os.getenv("VYRA_SLIM", "false").lower() == "true"

    # Override with command line arguments if provided
    if len(sys.argv) > 1:
        package_name = sys.argv[1]
    if len(sys.argv) > 2:
        node_name = sys.argv[2]
    if len(sys.argv) > 3:
        supervisord_program = sys.argv[3]
    if len(sys.argv) > 4:
        slim_mode = sys.argv[4].lower() in ["true", "1", "yes"]

    mode_str = "SLIM (Python-only)" if slim_mode else "FULL (ROS2)"

    logger.info("=" * 60)
    logger.info(f"🔥 Hot Reload Watcher Starting ({mode_str})")
    logger.info("=" * 60)
    logger.info(f"📦 Package: {package_name}")
    logger.info(f"🎯 Node: {node_name}")
    logger.info(f"👀 Watching: {watch_path}")
    logger.info(f"🏠 Workspace: {workspace_path}")
    logger.info(f"🎛️ Supervisord Program: {supervisord_program}")
    logger.info("=" * 60)

    # Create event handler and observer
    event_handler = HotReloadHandler(
        workspace_path, package_name, node_name, 
        supervisord_program=supervisord_program,
        slim_mode=slim_mode)

    observer = Observer()
    observer.schedule(event_handler, watch_path, recursive=True)

    # Don't start process here - Supervisord manages it
    logger.info("ℹ️ Process managed by Supervisord, not starting here")

    # Start watching
    observer.start()
    logger.info("👀 Watching for file changes... (Press Ctrl+C to stop)")

    # Check if the install tree is behind the src tree (files added before container start)
    if not slim_mode:
        event_handler._check_initial_sync()

    try:
        while True:
            time.sleep(1)
            event_handler.check_pending_rebuild()
    except KeyboardInterrupt:
        logger.info("\n🛑 Stopping hot reload watcher...")
        observer.stop()

    observer.join()
    logger.info("✅ Hot reload watcher stopped")


if __name__ == "__main__":
    main()
