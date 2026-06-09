#!/bin/bash
# filepath: /home/holgder/VOS2_WORKSPACE/$MODULE_NAME/vyra_entrypoint.sh
set -euo pipefail

echo "=== VYRA ENTRYPOINT STARTING ==="

# =============================================================================
# Redis Availability Check
# =============================================================================
echo "=== CHECKING REDIS AVAILABILITY ==="

# Wait for Redis to be ready (Docker Swarm compatible)
if [ -f "/workspace/tools/wait-for-redis.sh" ]; then
    echo "🔄 Checking Redis availability..."
    /workspace/tools/wait-for-redis.sh
elif [ -f "/host/tools/wait-for-redis.sh" ]; then
    echo "🔄 Checking Redis availability..."
    /host/tools/wait-for-redis.sh
else
    echo "⚠️ wait-for-redis.sh not found, skipping Redis check"
fi

# Warte kurz für vollständige Installation
# sleep 2

[ -f .env ] && chmod 777 .env

# =============================================================================

# =============================================================================
# Environment Variable Setup
# =============================================================================
echo "=== SETTING UP ENVIRONMENT VARIABLES ==="

# Read MODULE_NAME from module_data.yaml BEFORE loading .env
# This allows module name to be available for ENV variable setup
MODULE_DATA_FILE=".module/module_data.yaml"
if [ -f "$MODULE_DATA_FILE" ]; then
    MODULE_NAME=$(grep "^name:" "$MODULE_DATA_FILE" | sed 's/^name:[[:space:]]*//' | tr -d '"' | tr -d "'")
    if [ -n "$MODULE_NAME" ]; then
        echo "✅ Using name from module_data.yaml: $MODULE_NAME"
        export MODULE_NAME  # Export as environment variable for ROS2 processes
        
        # Set ROS2 rcl_logging filename based on module name
        export RCL_LOGGING_LOG_FILE_NAME="${MODULE_NAME}_ros2_core.log"
        
        # Disable RCL/RCUTILS file logging to prevent per-thread log files
        # Log to console instead (stdout/stderr which supervisord captures)
        export RCUTILS_CONSOLE_OUTPUT_FORMAT="[{severity}] [{name}]: {message}"
        export RCUTILS_COLORIZED_OUTPUT=0
        export RCL_LOGGING_USE_CONSOLE=1
        export RCL_LOGGING_TO_FILES=0
        
        echo "✅ RCL logging configured to console only (no per-thread files)"
    else
        echo "⚠️ Could not read name from $MODULE_DATA_FILE"
        echo "⚠️ ! Check the structure of the module_data.yaml file !"
        exit 1
    fi
else
    echo "⚠️ Module data file $MODULE_DATA_FILE not found. Cannot start module"
    exit 1
fi

# Add module name to .env before loading
if [ -f ".env" ]; then
    if ! grep -q '^MODULE_NAME=' ".env"; then
        echo "MODULE_NAME=$MODULE_NAME" >> ".env"
    else
        # Update the existing line
        sed -i "s/^MODULE_NAME=.*/MODULE_NAME=$MODULE_NAME/" ".env"
    fi
    
    echo "=== UPDATING RCL_LOGGING_LOG_FILE_NAME IN .env ==="

    # -> Not working yet, ignore for now. Could be regarded as a TODO. <-

    # RCL_LOG_FILENAME="${MODULE_NAME}_ros2_core.log"
    # if ! grep -q '^RCL_LOGGING_LOG_FILE_NAME=' ".env"; then
    #     echo "RCL_LOGGING_LOG_FILE_NAME=$RCL_LOG_FILENAME" >> ".env"
    #     echo "✅ Added RCL_LOGGING_LOG_FILE_NAME=$RCL_LOG_FILENAME to .env"
    # else
    #     sed -i "s/^RCL_LOGGING_LOG_FILE_NAME=.*/RCL_LOGGING_LOG_FILE_NAME=$RCL_LOG_FILENAME/" ".env"
    #     echo "✅ Updated RCL_LOGGING_LOG_FILE_NAME=$RCL_LOG_FILENAME in .env"
    # fi
fi

# Load environment variables from .env (filter comments and empty lines)
# MODULE_NAME is now already exported, but this will reload it from .env
if [ -f .env ]; then
    export $(grep -v '^#' .env | sed 's/#.*$//' | grep -v '^$' | xargs)
fi

# Debug: Show loaded environment variables
echo "=== Loaded Environment Variables ==="
env | grep -E "ENABLE_|VYRA_|MODULE_NAME" || echo "No ENABLE_/VYRA variables found"
echo "===================================="

# =============================================================================
# Source Vyra Base and Package Setup (skip in SLIM mode)
# =============================================================================
if [ "${VYRA_SLIM:-false}" != "true" ]; then

    # Set empty variables to prevent unbound variable errors
    : "${AMENT_TRACE_SETUP_FILES:=""}"
    : "${COLCON_TRACE:=""}"
    : "${AMENT_PYTHON_EXECUTABLE:="/usr/bin/python3"}"
    : "${COLCON_PYTHON_EXECUTABLE:="/usr/bin/python3"}"
    : "${CMAKE_PREFIX_PATH:="/opt/ros/kilted"}"

    echo "=== SOURCING VYRA BASE SETUP ==="
    # source /workspace/tools/setup_ros_global.sh
    source /opt/ros/kilted/setup.bash
    if [ $? -eq 0 ]; then
        echo "✅ Source ROS global setup successful"
    else
        echo "❌ Source ROS global setup failed"
        exit 1
    fi
else
    echo "=== SLIM MODE: Skipping ROS2 sourcing (no ROS2 required) ==="
fi

# =============================================================================
# gRPC Interface Generation
# =============================================================================
echo "=== GRPC INTERFACE GENERATION CHECK ==="

if [ -d "/workspace/storage/interfaces" ] && [ -n "$(find /workspace/storage/interfaces -maxdepth 1 -name '*.proto' 2>/dev/null)" ]; then
    PROTO_COUNT=$(find /workspace/storage/interfaces -maxdepth 1 -name '*.proto' 2>/dev/null | wc -l)
    echo "📦 Found $PROTO_COUNT proto file(s), generating gRPC code..."
    
    # Create grpc_generated directory
    mkdir -p /workspace/storage/interfaces/grpc_generated
    
    # Always use grpc_tools.protoc directly (setup_interfaces.py doesn't support --generate-grpc)
    echo "🛠️ Generating gRPC code directly with grpc_tools.protoc..."
    
    # Generate Python code from proto files
    cd /workspace/storage/interfaces
    for proto_file in *.proto; do
        echo "   Generating from: $proto_file"
        python3 -m grpc_tools.protoc \
            --proto_path=. \
            --python_out=grpc_generated \
            --grpc_python_out=grpc_generated \
            "$proto_file"
    done
    
    # Fix imports in generated gRPC files (convert absolute to relative imports)
    echo "🔧 Fixing gRPC imports to use relative imports..."
    for grpc_file in grpc_generated/*_pb2_grpc.py; do
        if [ -f "$grpc_file" ]; then
            # Replace "import X_pb2 as" with "from . import X_pb2 as"
            sed -i 's/^import \(.*_pb2\) as /from . import \1 as /g' "$grpc_file"
            echo "   ✓ Fixed imports in $(basename $grpc_file)"
        fi
    done
    
    cd /workspace
    
    # Create __init__.py for the grpc_generated package
    touch /workspace/storage/interfaces/grpc_generated/__init__.py
    
    echo "✅ gRPC code generated successfully with relative imports"
else
    echo "ℹ️  No proto files found in storage/interfaces/, skipping gRPC generation"
fi

# =============================================================================
# Log Directory Setup
# =============================================================================
echo "=== SETTING UP LOG DIRECTORIES ==="

mkdir -p /workspace/log/nginx
mkdir -p /workspace/log/ros2
mkdir -p /workspace/log/core

# Set write permissions for all users (ROS2 logging needs this)
chmod -R 777 /workspace/log/ 2>/dev/null || true

# Ensure nginx temp directories are writable by vyrauser
# (nginx creates /var/lib/nginx/body etc. at runtime; if root-owned it fails)
mkdir -p /var/lib/nginx/body /var/lib/nginx/fastcgi /var/lib/nginx/proxy /var/lib/nginx/scgi /var/lib/nginx/uwsgi 2>/dev/null || true
chown -R vyrauser:vyrauser /var/lib/nginx 2>/dev/null || true
mkdir -p /var/log/nginx 2>/dev/null || true
chown -R vyrauser:vyrauser /var/log/nginx 2>/dev/null || true
mkdir -p /run/nginx 2>/dev/null || true
chown -R vyrauser:vyrauser /run/nginx 2>/dev/null || true

# Clean up old thread log files if cleanup script exists
if [ -f "/workspace/tools/cleanup_thread_logs.sh" ]; then
    /workspace/tools/cleanup_thread_logs.sh
fi

rm -rf /workspace/log/ros2/*.log

# =============================================================================
# Install Directory Restoration (skip in SLIM mode - no colcon build artifacts)
# =============================================================================
if [ "${VYRA_SLIM:-false}" != "true" ]; then
    echo "=== CHECKING install/ DIRECTORY ==="

    # Get module name for checking install integrity
    if [ -f ".module/module_data.yaml" ]; then
        MODULE_NAME=$(grep "^name:" .module/module_data.yaml | cut -d: -f2 | tr -d ' ' | tr -d "'" | tr -d '"')
    else
        echo "⚠️  Warning: module_data.yaml not found, cannot verify module package installation"
        MODULE_NAME=""
    fi

    # This happens when using full workspace mount for development
    if [ -d "/opt/vyra/install_backup" ]; then
        # Check if install directory is complete (has setup.bash AND module package with executable)
        INSTALL_COMPLETE=true
        
        if [ ! -f "/workspace/install/setup.bash" ]; then
            echo "❌ install/setup.bash missing"
            INSTALL_COMPLETE=false
        elif [ -n "$MODULE_NAME" ] && [ ! -d "/workspace/install/$MODULE_NAME" ]; then
            echo "❌ install/$MODULE_NAME package missing"
            INSTALL_COMPLETE=false
        elif [ -n "$MODULE_NAME" ] && [ ! -f "/workspace/install/$MODULE_NAME/lib/$MODULE_NAME/core" ]; then
            echo "❌ install/$MODULE_NAME/lib/$MODULE_NAME/core executable missing"
            INSTALL_COMPLETE=false
        elif [ -n "$MODULE_NAME" ] && [ ! -d "/workspace/install/${MODULE_NAME}_interfaces" ]; then
            echo "❌ install/${MODULE_NAME}_interfaces package missing"
            INSTALL_COMPLETE=false
        fi

        # Check build ID: detect when image was rebuilt with new interfaces/wheels
        # /opt/vyra/build_id is written during docker build (from CACHE_BUST)
        # install/.build_id is stamped into install/ before the backup is created
        # If they differ, the current install/ is from an old image → force restore
        if [ "$INSTALL_COMPLETE" = true ] && [ -f "/opt/vyra/build_id" ]; then
            if [ ! -f "/workspace/install/.build_id" ] || \
               [ "$(cat /opt/vyra/build_id)" != "$(cat /workspace/install/.build_id)" ]; then
                echo "🔄 Image has been rebuilt (build ID changed) - forcing install/ restore"
                echo "   Image build ID : $(cat /opt/vyra/build_id)"
                echo "   Install build ID: $(cat /workspace/install/.build_id 2>/dev/null || echo 'missing')"
                INSTALL_COMPLETE=false
            else
                echo "✅ Build ID matches - install/ is current ($(cat /opt/vyra/build_id))"
            fi
        fi

        if [ "$INSTALL_COMPLETE" = false ]; then
            echo "📦 Restoring complete install/ directory from image backup..."
            rm -rf /workspace/install
            cp -r /opt/vyra/install_backup /workspace/install
            chown -R vyrauser:vyrauser /workspace/install
            echo "✅ install/ directory restored (including $MODULE_NAME package)"
        else
            echo "✅ install/ directory complete (setup.bash + $MODULE_NAME + interfaces + build ID verified)"
            echo "🔧 Fixing ownership of install/ directory..."
            chown -R vyrauser:vyrauser /workspace/install
            echo "✅ Ownership fixed to vyrauser:vyrauser"
        fi
    else
        if [ ! -f "/workspace/install/setup.bash" ]; then
            echo "❌ ERROR: install/setup.bash not found and no backup available"
            echo "💡 Image may not have been built correctly"
            exit 1
        fi
    fi

    # =============================================================================
    # Source Package Setup
    # =============================================================================
    echo "=== SOURCING PACKAGE SETUP ==="

    # Source package setup (install folder already built in image or restored)
    source install/setup.bash

    if [ $? -eq 0 ]; then
        echo "✅ Source package setup successful"
    else
        echo "❌ Source package setup failed"
        exit 1
    fi
else
    echo "=== SLIM MODE: No ROS2 install/ needed, skipping colcon package setup ==="
fi

# =============================================================================
# Dynamic Wheel Installation
# =============================================================================
echo "=== CHECKING FOR NEW/UPDATED WHEELS ==="

if [ -d "wheels" ] && [ "$(ls -A wheels/*.whl 2>/dev/null)" ]; then
    echo "📦 Found wheels directory with .whl files"
    
    # Create temporary directory for latest wheels
    tmpdir=$(mktemp -d)
    
    # For each unique package, find the latest version
    for pkg in $(ls wheels/*.whl | sed -E 's#.*/([^/-]+)-.*#\1#' | sort -u); do
        latest=$(ls wheels/"$pkg"-*.whl | sort -V | tail -n 1)
        cp "$latest" "$tmpdir/"
        echo "  📦 Selected: $(basename "$latest")"
    done
    
    # Install all latest wheels
    echo "🔧 Installing/updating wheels..."
    if pip install "$tmpdir"/*.whl \
        --break-system-packages \
        --force-reinstall \
        --no-deps \
        --ignore-installed cryptography 2>&1 | grep -v "WARNING.*pip"; then
        echo "✅ Wheels installed successfully"
    else
        echo "⚠️  Some wheels may have failed to install (check logs)"
    fi
    
    # Cleanup
    rm -rf "$tmpdir"
else
    echo "ℹ️  No wheels directory or .whl files found - skipping wheel installation"
fi

echo "===================================="

# =============================================================================
# Setup interfaces (always runs - proto files and config are needed in all modes)
# Supports both Python|Rust modules (setup_interfaces.py)
# =============================================================================
if [ -f "tools/setup_interfaces.py" ]; then
    python3 tools/setup_interfaces.py
else
    echo "❌ No setup_interfaces.py script found in tools/, stopping"
    exit 1
fi

# =============================================================================
# NFS Interface Management
# New NFS layout per module:
#   NFS_MODULE_DIR/
#     config/        ← JSON metadata files (vyra_*_meta.json)
#     msg/           ← .msg files + _gen/ (pb2 generated files)
#     srv/           ← .srv files + _gen/ (pb2 generated files)
#     action/        ← .action files
#     ros2/          ← colcon install artifacts (setup.bash, share/, ...) — full mode only
#
# Source selection (VYRA_SLIM unchanged):
#   SLIM=true  → src/${MODULE_NAME}_interfaces  (no colcon install tree)
#   SLIM=false → install/${MODULE_NAME}_interfaces (colcon build output)
# =============================================================================

# Copy directory contents safely (set -e safe: no-op on missing/empty source)
copy_tree_contents() {
    local src="$1" dst="$2"
    [ -d "$src" ] || return 0
    [ -n "$(ls -A "$src" 2>/dev/null)" ] || return 0
    mkdir -p "$dst"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a "$src"/ "$dst"/ 2>/dev/null || cp -r "$src"/. "$dst"/
    else
        cp -r "$src"/. "$dst"/
    fi
}

echo "=== NFS INTERFACE MANAGEMENT ==="

# Read UUID from .module/module_data.yaml
MODULE_DATA_FILE="/workspace/.module/module_data.yaml"
if [ -f "$MODULE_DATA_FILE" ]; then
    INSTANCE_ID=$(grep '^uuid:' "$MODULE_DATA_FILE" | awk '{print $2}' | tr -d '"' | tr -d "'")
    echo "ℹ️  Module: $MODULE_NAME, Instance: $INSTANCE_ID (from module_data.yaml)"
else
    INSTANCE_ID="${HOSTNAME#${MODULE_NAME}_}"
    if [ "$INSTANCE_ID" = "$HOSTNAME" ]; then
        INSTANCE_ID="$HOSTNAME"
    fi
    echo "⚠️  Warning: module_data.yaml not found, Instance: $INSTANCE_ID (from HOSTNAME)"
fi

NFS_VOLUME_PATH="${NFS_VOLUME_PATH:-/nfs/vyra_interfaces}"
INTERFACE_DIR="${MODULE_NAME}_${INSTANCE_ID}_interfaces"
INTERFACE_SRC_DIR="/workspace/src/${MODULE_NAME}_interfaces"
INTERFACE_INSTALL_DIR="/workspace/install/${MODULE_NAME}_interfaces"
INTERFACE_STAGING="/tmp/module_interfaces_staging/${MODULE_NAME}_interfaces"

if [ "${VYRA_SLIM:-false}" = "true" ]; then
    echo "ℹ️  SLIM mode: interface source = src tree ($INTERFACE_SRC_DIR)"
else
    echo "ℹ️  Full mode: interface source = colcon install ($INTERFACE_INSTALL_DIR)"
fi

if [ -d "$NFS_VOLUME_PATH" ]; then
    echo "✅ NFS volume found at $NFS_VOLUME_PATH"

    # Full mode only: restore colcon install tree from image staging when workspace mount hides it
    if [ "${VYRA_SLIM:-false}" != "true" ]; then
        if [ ! -d "$INTERFACE_INSTALL_DIR" ] && [ -d "$INTERFACE_STAGING" ]; then
            echo "📦 Copying interfaces from staging to install..."
            mkdir -p "/workspace/install"
            cp -r "$INTERFACE_STAGING" "$INTERFACE_INSTALL_DIR"
            echo "✅ Interfaces copied from staging to install"
        fi
    fi

    NFS_MODULE_DIR="$NFS_VOLUME_PATH/$INTERFACE_DIR"
    NFS_ROS_DIR="$NFS_MODULE_DIR/ros2"
    NFS_CONFIG_DIR="$NFS_MODULE_DIR/config"
    NFS_MSG_DIR="$NFS_MODULE_DIR/msg"
    NFS_SRV_DIR="$NFS_MODULE_DIR/srv"
    NFS_ACTION_DIR="$NFS_MODULE_DIR/action"

    mkdir -p "$NFS_ROS_DIR" "$NFS_CONFIG_DIR" "$NFS_MSG_DIR" "$NFS_SRV_DIR" "$NFS_ACTION_DIR" 2>/dev/null || {
        echo "⚠️  Cannot write to NFS at $NFS_MODULE_DIR (read-only or wrong path), skipping NFS interface management"
        NFS_WRITE_OK=false
    }
    NFS_WRITE_OK="${NFS_WRITE_OK:-true}"

    if [ "$NFS_WRITE_OK" = "true" ]; then
    # ------------------------------------------------------------------
    # Determine whether an update is needed (comprehensive check)
    # Checks: first-time, config file count, interface file counts, checksums
    # ------------------------------------------------------------------
    FORCE_UPDATE=false
    SOURCE_CONFIG_DIR="$INTERFACE_SRC_DIR/config"

    if [ "${VYRA_SLIM:-false}" = "true" ]; then
        if [ ! -d "$NFS_CONFIG_DIR" ] || [ -z "$(ls -A "$NFS_CONFIG_DIR" 2>/dev/null)" ]; then
            echo "ℹ️  First-time setup (SLIM): will deploy interfaces from src tree to NFS..."
            FORCE_UPDATE=true
        fi
    elif [ ! -f "$NFS_ROS_DIR/setup.bash" ]; then
        echo "ℹ️  First-time setup: will copy all interfaces to NFS..."
        FORCE_UPDATE=true
    fi

    if [ "$FORCE_UPDATE" = false ]; then
        # Use src tree as source of truth for config count (always has latest,
        # even before colcon rebuild; install tree may lag behind)

        # 1. Compare config file count (detects new *.meta.json files)
        SOURCE_CONFIG_COUNT=$(find "$SOURCE_CONFIG_DIR" -maxdepth 1 -name "*.meta.json" 2>/dev/null | wc -l)
        NFS_CONFIG_COUNT=$(find "$NFS_CONFIG_DIR" -maxdepth 1 -name "*.json" 2>/dev/null | wc -l)
        if [ "$SOURCE_CONFIG_COUNT" != "$NFS_CONFIG_COUNT" ]; then
            echo "⚠️  Config file count changed ($NFS_CONFIG_COUNT → $SOURCE_CONFIG_COUNT) — forcing NFS update"
            FORCE_UPDATE=true
        fi

        # 2. Compare interface file counts (.srv, .msg, .action)
        if [ "$FORCE_UPDATE" = false ]; then
            for iface_type in srv msg action; do
                SRC_COUNT=$(find "$INTERFACE_SRC_DIR/$iface_type" -maxdepth 1 -name "*.${iface_type}" 2>/dev/null | wc -l)
                NFS_COUNT=$(find "$NFS_MODULE_DIR/$iface_type" -maxdepth 1 -name "*.${iface_type}" 2>/dev/null | wc -l)
                if [ "$SRC_COUNT" != "$NFS_COUNT" ]; then
                    echo "⚠️  ${iface_type}/ file count changed ($NFS_COUNT → $SRC_COUNT) — forcing NFS update"
                    FORCE_UPDATE=true
                    break
                fi
            done
        fi

        # 3. Check checksums of ALL config JSON files
        if [ "$FORCE_UPDATE" = false ] && [ -d "$SOURCE_CONFIG_DIR" ]; then
            while IFS= read -r config_file; do
                name=$(basename "$config_file")
                nfs_file="$NFS_CONFIG_DIR/$name"
                if [ ! -f "$nfs_file" ]; then
                    echo "⚠️  New config file: $name — forcing NFS update"
                    FORCE_UPDATE=true
                    break
                fi
                SOURCE_MD5=$(md5sum "$config_file" | awk '{print $1}')
                NFS_MD5=$(md5sum "$nfs_file" | awk '{print $1}')
                if [ "$SOURCE_MD5" != "$NFS_MD5" ]; then
                    echo "⚠️  Config update detected: $name differs — forcing NFS update"
                    FORCE_UPDATE=true
                    break
                fi
            done < <(find "$SOURCE_CONFIG_DIR" -maxdepth 1 -name "*.meta.json" 2>/dev/null)
        fi

        [ "$FORCE_UPDATE" = false ] && echo "✅ NFS interfaces up-to-date (checksums match)"
    fi

    # ------------------------------------------------------------------
    # Push interfaces to NFS
    # ------------------------------------------------------------------
    if [ "$FORCE_UPDATE" = true ]; then
        echo "🔄 Updating NFS interfaces..."

        if [ "${VYRA_SLIM:-false}" = "true" ]; then
            # SLIM: deploy everything from src/${MODULE_NAME}_interfaces (no colcon install/)
            copy_tree_contents "$INTERFACE_SRC_DIR/config" "$NFS_CONFIG_DIR"
            [ -d "$INTERFACE_SRC_DIR/config" ] && echo "✅ config/ deployed (from src tree)"

            copy_tree_contents "$INTERFACE_SRC_DIR/msg" "$NFS_MSG_DIR"
            [ -d "$INTERFACE_SRC_DIR/msg" ] && echo "✅ msg/ deployed (from src tree, incl. _gen/)"

            copy_tree_contents "$INTERFACE_SRC_DIR/srv" "$NFS_SRV_DIR"
            [ -d "$INTERFACE_SRC_DIR/srv" ] && echo "✅ srv/ deployed (from src tree, incl. _gen/)"

            copy_tree_contents "$INTERFACE_SRC_DIR/action" "$NFS_ACTION_DIR"
            [ -d "$INTERFACE_SRC_DIR/action" ] && echo "✅ action/ deployed (from src tree)"

            echo "ℹ️  SLIM mode: skipping ros2/ colcon install copy (not built)"
        else
            # Full mode: colcon install tree → ros2/, config from install, definitions from src
            if [ -d "$INTERFACE_INSTALL_DIR" ] && [ -n "$(ls -A "$INTERFACE_INSTALL_DIR" 2>/dev/null)" ]; then
                copy_tree_contents "$INTERFACE_INSTALL_DIR" "$NFS_ROS_DIR"

                cat > "$NFS_ROS_DIR/setup.bash" <<'SETUPEOF'
#!/usr/bin/env bash
# Auto-generated by vyra_entrypoint.sh — do not edit manually
COLCON_CURRENT_PREFIX="$(cd "$(dirname "${BASH_SOURCE[0]}")" > /dev/null && pwd)"
export COLCON_CURRENT_PREFIX
if [ -z "${AMENT_PREFIX_PATH:-}" ]; then
    export AMENT_PREFIX_PATH="$COLCON_CURRENT_PREFIX"
elif [[ ":${AMENT_PREFIX_PATH:-}:" != *":$COLCON_CURRENT_PREFIX:"* ]]; then
    export AMENT_PREFIX_PATH="$COLCON_CURRENT_PREFIX:${AMENT_PREFIX_PATH:-}"
fi
if [ -f "$COLCON_CURRENT_PREFIX/local_setup.bash" ]; then
    . "$COLCON_CURRENT_PREFIX/local_setup.bash"
fi
SETUPEOF
                chmod +x "$NFS_ROS_DIR/setup.bash"
                echo "✅ ros2/ install tree deployed (from install/)"
            else
                echo "⚠️  No colcon install at $INTERFACE_INSTALL_DIR — skipping ros2/ copy"
            fi

            CONFIG_SRC="$INTERFACE_INSTALL_DIR/share/${MODULE_NAME}_interfaces/config"
            if [ -d "$CONFIG_SRC" ]; then
                copy_tree_contents "$CONFIG_SRC" "$NFS_CONFIG_DIR"
                echo "✅ config/ deployed (from install tree)"
            fi

            # Merge any JSON config files from src tree that are not yet in the install
            if [ -d "$INTERFACE_SRC_DIR/config" ]; then
                for src_json in "$INTERFACE_SRC_DIR/config"/*.json; do
                    [ -f "$src_json" ] || continue
                    dest_json="$NFS_CONFIG_DIR/$(basename "$src_json")"
                    if [ ! -f "$dest_json" ]; then
                        cp "$src_json" "$dest_json"
                        echo "✅ config/ added from src tree: $(basename "$src_json")"
                    fi
                done
            fi

            if [ -d "$INTERFACE_SRC_DIR" ]; then
                copy_tree_contents "$INTERFACE_SRC_DIR/msg" "$NFS_MSG_DIR"
                [ -d "$INTERFACE_SRC_DIR/msg" ] && echo "✅ msg/ deployed (incl. _gen/)"

                copy_tree_contents "$INTERFACE_SRC_DIR/srv" "$NFS_SRV_DIR"
                [ -d "$INTERFACE_SRC_DIR/srv" ] && echo "✅ srv/ deployed (incl. _gen/)"

                copy_tree_contents "$INTERFACE_SRC_DIR/action" "$NFS_ACTION_DIR"
                [ -d "$INTERFACE_SRC_DIR/action" ] && echo "✅ action/ deployed"
            else
                echo "ℹ️  No src interface definitions at $INTERFACE_SRC_DIR (optional)"
            fi
        fi

        echo "✅ NFS interfaces fully updated at $NFS_MODULE_DIR"
    fi
    fi  # NFS_WRITE_OK

    # ------------------------------------------------------------------
    # Source ROS2 overlays from all modules on NFS
    # ------------------------------------------------------------------
    echo "🔗 Sourcing ROS2 interfaces from NFS..."
    INTERFACES_SOURCED=0
    for interface_dir in "$NFS_VOLUME_PATH"/*_interfaces; do
        if [ -d "$interface_dir/ros2" ] && [ -f "$interface_dir/ros2/setup.bash" ]; then
            interface_name=$(basename "$interface_dir")
            echo "   Sourcing $interface_name/ros2..."
            set +u  # ROS2 setup.bash may reference AMENT_PREFIX_PATH before it's initialised
            source "$interface_dir/ros2/setup.bash" || echo "⚠️  Failed to source $interface_name"
            set -u
            INTERFACES_SOURCED=$((INTERFACES_SOURCED + 1))
        fi
    done

    [ $INTERFACES_SOURCED -gt 0 ] \
        && echo "✅ Sourced $INTERFACES_SOURCED interface package(s) from NFS" \
        || echo "ℹ️  No interface packages found in NFS (first module?)"

    # ------------------------------------------------------------------
    # Add _gen/ (protobuf) dirs from NFS srv/ and msg/ to PYTHONPATH
    # ------------------------------------------------------------------
    echo "🔗 Loading Protobuf _gen modules from NFS..."
    PROTO_LOADED=0
    for nfs_iface_dir in "$NFS_VOLUME_PATH"/*_interfaces; do
        for sub in msg srv; do
            gen_dir="$nfs_iface_dir/$sub/_gen"
            if [ -d "$gen_dir" ]; then
                # Add the parent (msg/ or srv/) so imports like `from _gen import X_pb2` work,
                # and also the _gen dir itself for direct imports.
                export PYTHONPATH="$nfs_iface_dir/$sub:$gen_dir:${PYTHONPATH:-}"
                PROTO_LOADED=$((PROTO_LOADED + 1))
            fi
        done
    done

    [ $PROTO_LOADED -gt 0 ] \
        && echo "✅ Added $PROTO_LOADED _gen Protobuf path(s) to PYTHONPATH" \
        || echo "ℹ️  No _gen Protobuf directories found in NFS"

else
    echo "⚠️  NFS volume not found at $NFS_VOLUME_PATH"
    echo "   Modules will only see their own interfaces"

    # Fallback: add local src _gen dirs to PYTHONPATH
    for sub in msg srv; do
        local_gen="/workspace/src/${MODULE_NAME}_interfaces/$sub/_gen"
        if [ -d "$local_gen" ]; then
            export PYTHONPATH="/workspace/src/${MODULE_NAME}_interfaces/$sub:$local_gen:${PYTHONPATH:-}"
            echo "✅ Added local $sub/_gen to PYTHONPATH"
        fi
    done
fi

# =============================================================================
# SSL Certificate Auto-Generation
# =============================================================================
echo "=== SSL CERTIFICATE CHECK ==="

# Function to check and create SSL certificates
# Parameters: $1 = cert_name (e.g., "webserver", "frontend")
check_and_create_certificates() {
    local cert_name="${1:-webserver}"
    local cert_path="/workspace/storage/certificates/${cert_name}.crt"
    local key_path="/workspace/storage/certificates/${cert_name}.key"
    local cert_script="/workspace/tools/create_ssl_certificates.sh"
    
    echo "🔍 Checking SSL certificates for: $cert_name"
    echo "   Certificate: $cert_path"
    echo "   Private Key: $key_path"
    
    if [ -f "$cert_path" ] && [ -f "$key_path" ]; then
        echo "✅ SSL certificates found for $cert_name"
        
        # Ensure certificates are readable by vyrauser (may be root-owned from prior run)
        chown vyrauser:vyrauser "$cert_path" "$key_path" 2>/dev/null || true
        chmod 640 "$key_path"
        chmod 644 "$cert_path"
        
        # Check if certificates are still valid (not expired)
        if openssl x509 -checkend 86400 -noout -in "$cert_path" >/dev/null 2>&1; then
            echo "✅ SSL certificates for $cert_name are valid (>24h remaining)"
            return 0
        else
            echo "⚠️ SSL certificates for $cert_name are expiring soon or expired"
            echo "🔄 Regenerating certificates..."
        fi
    else
        echo "❌ SSL certificates not found for $cert_name"
        echo "🔨 Creating new SSL certificates..."
    fi
    
    # Create certificates directory if it doesn't exist
    mkdir -p "/workspace/storage/certificates"
    
    # Check if creation script exists
    if [ -f "$cert_script" ]; then
        echo "🛠️ Using certificate creation script for $cert_name..."
        if "$cert_script" --name "$cert_name" --domain localhost --days 365; then
            echo "✅ SSL certificates for $cert_name created successfully"
            return 0
        else
            echo "❌ Certificate creation script failed for $cert_name"
            return 1
        fi
    else
        echo "⚠️ Certificate script not found, creating $cert_name manually..."
        
        # Fallback: Create certificates directly
        if openssl req -x509 -newkey rsa:4096 \
            -keyout "$key_path" \
            -out "$cert_path" \
            -days 365 \
            -nodes \
            -subj "/CN=localhost/O=${MODULE_NAME}/OU=${cert_name}/C=DE" >/dev/null 2>&1; then
            
            # Set secure permissions and correct ownership
            chown vyrauser:vyrauser "$key_path" "$cert_path" 2>/dev/null || true
            chmod 640 "$key_path"
            chmod 644 "$cert_path"
            
            echo "✅ SSL certificates for $cert_name created manually"
            return 0
        else
            echo "❌ Manual certificate creation failed for $cert_name"
            return 1
        fi
    fi
}

# Check/create backend certificates if backend webserver is enabled
if [ "${ENABLE_BACKEND_WEBSERVER:-false}" = "true" ]; then
    echo "🔐 Backend webserver enabled - checking SSL certificates..."
    
    if check_and_create_certificates "webserver"; then
        echo "✅ Backend SSL certificate check completed successfully"
    else
        echo "⚠️ Backend SSL certificate setup failed - continuing without SSL"
        echo "   Backend will start in HTTP mode"
    fi
else
    echo "⏭️ Backend webserver disabled - skipping backend SSL certificate check"
fi

# Check/create frontend certificates if frontend webserver is enabled
if [ "${ENABLE_FRONTEND_WEBSERVER:-false}" = "true" ]; then
    echo "🔐 Frontend webserver enabled - checking SSL certificates..."
    
    if check_and_create_certificates "frontend"; then
        echo "✅ Frontend SSL certificate check completed successfully"
    else
        echo "⚠️ Frontend SSL certificate setup failed - continuing without SSL"
        echo "   Frontend will start in HTTP mode"
    fi
else
    echo "⏭️ Frontend webserver disabled - skipping frontend SSL certificate check"
fi

# =============================================================================
# Create Log Directories
# =============================================================================
echo "=== CREATING LOG DIRECTORIES ==="

mkdir -p /workspace/log/core
mkdir -p /workspace/log/nginx

# Only create ros2 logs directory if not in SLIM mode
if [ "${VYRA_SLIM:-false}" != "true" ]; then
    mkdir -p /workspace/log/ros2
    echo "✅ Created core, nginx and ros2 log directories"
else
    echo "✅ Created core and nginx log directories (slim mode: skipping ros2)"
fi

echo "===================================="

# =============================================================================
# Supervisord Service Configuration
# =============================================================================
echo "=== CONFIGURING SUPERVISORD SERVICES ==="

# Check Development Mode
if [ "${VYRA_DEV_MODE:-false}" = "true" ]; then
    echo "🚀 DEVELOPMENT MODE ENABLED"

    # Hot reload is started by tools/startup_slim_core.sh when ENABLE_HOT_RELOAD=true

    # Check if npm is available for Vite dev server
    if command -v npm >/dev/null 2>&1; then
        echo "   Starting Vite Dev Server instead of Nginx..."

        # Disable Nginx in dev mode
        ENABLE_FRONTEND_WEBSERVER=false
        
        # Install npm dependencies if needed
        if [ ! -d "/workspace/frontend/node_modules" ] && [ -f "/workspace/frontend/package.json" ]; then
            echo "📦 Installing npm dependencies..."
            cd /workspace/frontend
            npm install
            cd /workspace
        fi
        
        # Start Vite Dev Server in background (only if package.json exists)
        if [ -f "/workspace/frontend/package.json" ]; then
            echo "🔥 Starting Vite Dev Server on port 3000..."
            cd /workspace/frontend
            nohup npm run dev -- --host 0.0.0.0 --port 3000 > /workspace/log/vite.log 2>&1 &
            VITE_PID=$!
            echo "✅ Vite Dev Server started (PID: $VITE_PID)"
            echo "   Frontend URL: http://localhost:3000"
            echo "   Log: /workspace/log/vite.log"
            cd /workspace
        else
            echo "⏭️  No frontend/package.json found — skipping Vite Dev Server"
        fi
    else
        echo "⚠️  npm not available - falling back to Nginx with pre-built frontend"
        echo "   (Set VYRA_DEV_MODE=false or use development base image for Vite hot reload)"
        # Keep ENABLE_FRONTEND_WEBSERVER=true to use nginx with dist/
        # Force enable Nginx by updating supervisord config
        echo "🔧 Enabling Nginx in supervisord config..."
        
        # Replace the VYRA_DEV_MODE check to allow Nginx startup
        sudo sed -i 's/if \[ "$VYRA_DEV_MODE" = "false" \]; then/if [ "$VYRA_DEV_MODE" = "false" ] || [ "${ENABLE_FRONTEND_WEBSERVER:-false}" = "true" ]; then/' /etc/supervisor/conf.d/supervisord.conf 2>/dev/null || \
        sed -i 's/if \[ "$VYRA_DEV_MODE" = "false" \]; then/if [ "$VYRA_DEV_MODE" = "false" ] || [ "${ENABLE_FRONTEND_WEBSERVER:-false}" = "true" ]; then/' /workspace/supervisord.conf 2>/dev/null || true
        
        # Enable autostart
        sudo sed -i '/\[program:nginx\]/,/^\[/ s/autostart=false/autostart=true/' /etc/supervisor/conf.d/supervisord.conf 2>/dev/null || \
        sed -i '/\[program:nginx\]/,/^\[/ s/autostart=false/autostart=true/' /workspace/supervisord.conf 2>/dev/null || true
    fi

else
    echo "🏭 PRODUCTION MODE — Using Nginx with pre-built frontend"
    
    # Check if frontend is built (dist folder should have JS/CSS files, not just index.html)
    if [ -d "/workspace/frontend/dist" ]; then
        ASSET_COUNT=$(find /workspace/frontend/dist -type f \( -name "*.js" -o -name "*.css" \) 2>/dev/null | wc -l)
        
        if [ "$ASSET_COUNT" -eq 0 ]; then
            echo "⚠️  Frontend dist/ folder exists but is empty (no JS/CSS assets)"
            
            # Check if we have Node.js available (dev image)
            if command -v npm >/dev/null 2>&1; then
                echo "🔨 Building frontend with Vite (npm run build)..."
                cd /workspace/frontend
                
                # Install dependencies if node_modules missing
                if [ ! -d "node_modules" ]; then
                    echo "📦 Installing npm dependencies first..."
                    npm install
                fi
                
                # Build the frontend
                if npm run build; then
                    echo "✅ Frontend build completed successfully"
                else
                    echo "❌ Frontend build failed"
                    exit 1
                fi
                
                cd /workspace
            else
                echo "❌ ERROR: Node.js not available in production image"
                echo "💡 Solution: Build frontend before deployment or use dev image"
                echo "   Run locally: cd modules/$MODULE_NAME/frontend && npm run build"
                exit 1
            fi
        else
            echo "✅ Frontend already built ($ASSET_COUNT JS/CSS assets found)"
        fi
    else
        echo "❌ ERROR: /workspace/frontend/dist directory not found"
        echo "💡 Frontend must be built before starting in production mode"
        exit 1
    fi
fi

# Configure Nginx (Frontend Webserver) - only in production mode
if [ "${ENABLE_FRONTEND_WEBSERVER:-false}" = "true" ]; then
    echo "✅ Enabling Nginx (Frontend Webserver)"
    sudo sed -i '/\[program:nginx\]/,/^\[/ s/autostart=false/autostart=true/' /etc/supervisor/conf.d/supervisord.conf 2>/dev/null || \
    sed -i '/\[program:nginx\]/,/^\[/ s/autostart=false/autostart=true/' /workspace/supervisord.conf 2>/dev/null || true
else
    echo "⚠️ Nginx (Frontend Webserver) disabled"
fi

# Prüfe ob Supervisor-Konfiguration existiert und starte Supervisor
if [ -f "/etc/supervisor/conf.d/supervisord.conf" ]; then
    echo "=== STARTING SUPERVISORD ==="
    exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf -n
elif [ -f "/workspace/config/supervisord.conf" ]; then
    echo "=== STARTING SUPERVISORD (workspace/config) ==="
    exec /usr/bin/supervisord -c /workspace/config/supervisord.conf -n
elif [ -f "/workspace/supervisord.conf" ]; then
    echo "=== STARTING SUPERVISORD (Workspace) ==="
    exec /usr/bin/supervisord -c /workspace/supervisord.conf -n
else
    echo "=== NO SUPERVISORD CONFIG - STARTING DEFAULT COMMAND ==="
    echo "=== ENTRYPOINT READY - EXECUTING: $@ ==="
    exec "$@"
fi