#!/bin/bash
# startup_ros2_core.sh — Start module core with ROS2 (FULL mode)
# Called by startup_slim_core.sh when VYRA_SLIM=false

set -euo pipefail

WORKSPACE_ROOT="${WORKSPACE_ROOT:-/workspace}"
cd "${WORKSPACE_ROOT}"

if [ -f "${WORKSPACE_ROOT}/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "${WORKSPACE_ROOT}/.env"
    set +a
fi

VYRA_RUST="${VYRA_RUST:-false}"
MODULE_NAME="${MODULE_NAME:-unknown_module}"

echo "---------------------------------------------------------------"
echo " 🚀 Starting ROS2 Core Module: ${MODULE_NAME} (VYRA_RUST=${VYRA_RUST})"
echo "---------------------------------------------------------------"

export ROS_LOG_DIR="${WORKSPACE_ROOT}/log/ros2"

if [ -f "${WORKSPACE_ROOT}/config/cyclonedds.xml" ]; then
    export CYCLONEDDS_URI="file://${WORKSPACE_ROOT}/config/cyclonedds.xml"
    echo "✅ CYCLONEDDS_URI=${CYCLONEDDS_URI}"
else
    echo "⚠️ cyclonedds.xml not found, using default DDS config"
fi

if [[ "${VYRA_RUST,,}" == "true" ]]; then
    ROS_DISTRO="${ROS_DISTRO:-kilted}"
    if [ -f "/opt/ros/${ROS_DISTRO}/setup.bash" ]; then
        set +u
        # shellcheck source=/dev/null
        source "/opt/ros/${ROS_DISTRO}/setup.bash"
        set -u
        echo "[ros2] Sourced ROS2 ${ROS_DISTRO}"
    else
        echo "❌ ROS2 not found at /opt/ros/${ROS_DISTRO}"
        exit 1
    fi

    export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-1}"
    echo "[ros2] ROS_DOMAIN_ID=${ROS_DOMAIN_ID}"

    if [[ "${VYRA_SECURITY_ENABLED:-false}" == "true" ]]; then
        export ROS_SECURITY_ENABLE=true
        export ROS_SECURITY_STRATEGY=Enforce
        export ROS_SECURITY_KEYSTORE="${WORKSPACE_ROOT}/storage/certificates/sros2_keystore"
        echo "[sros2] Security enabled"
    fi

    HYPHENATED="${MODULE_NAME//_/-}"
    BINARY=""
    for candidate in \
        "${WORKSPACE_ROOT}/bin/${MODULE_NAME}" \
        "${WORKSPACE_ROOT}/bin/${HYPHENATED}" \
        "${WORKSPACE_ROOT}/target/release/${MODULE_NAME}" \
        "${WORKSPACE_ROOT}/target/release/${HYPHENATED}" \
        "${WORKSPACE_ROOT}/target/debug/${MODULE_NAME}" \
        "${WORKSPACE_ROOT}/target/debug/${HYPHENATED}"; do
        if [ -f "$candidate" ]; then
            BINARY="$candidate"
            break
        fi
    done

    if [ -z "$BINARY" ]; then
        echo "❌ Rust module binary not found for FULL mode"
        exit 1
    fi

    echo "[ros2] Starting ${BINARY} --mode full"
    exec "$BINARY" --mode full
fi

# --- Python FULL mode ---
if [ -f "${WORKSPACE_ROOT}/install/setup.bash" ]; then
    set +u
    # shellcheck source=/dev/null
    source "${WORKSPACE_ROOT}/install/setup.bash"
    set -u
else
    echo "❌ install/setup.bash not found"
    exit 1
fi

echo "SECURITY ENCLAVE: ${ROS_SECURITY_ENCLAVE:-}"

PYTHON_VER="python$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
SITE_PKG_DIR="${WORKSPACE_ROOT}/install/${MODULE_NAME}/lib/${PYTHON_VER}/site-packages"
if [ -d "${SITE_PKG_DIR}" ] && \
   ! find "${SITE_PKG_DIR}" -maxdepth 1 \( -name "${MODULE_NAME}-*.egg-info" -o -name "${MODULE_NAME}-*.dist-info" \) -type d 2>/dev/null | grep -q .; then
    echo "⚠️  egg-info missing — regenerating via pip install (no-deps)..."
    pip install --quiet --no-build-isolation --no-deps \
        --target "${SITE_PKG_DIR}" \
        "${WORKSPACE_ROOT}/src/${MODULE_NAME}"
    echo "✅ egg-info regenerated"
fi

export PYTHONUNBUFFERED=1

SOURCE_PACKAGE_DIR="${WORKSPACE_ROOT}/src/${MODULE_NAME}"
if [ "${VYRA_DEV_MODE:-false}" = "true" ] && [ -d "$SOURCE_PACKAGE_DIR" ]; then
    export PYTHONPATH="${SOURCE_PACKAGE_DIR}:${PYTHONPATH:-}"
    echo "🚀 Starting Core Module from source tree (dev)..."
    exec python3 -m "${MODULE_NAME}.main"
fi

echo "🚀 Starting Core Module via ros2 run..."
exec ros2 run "${MODULE_NAME}" core
