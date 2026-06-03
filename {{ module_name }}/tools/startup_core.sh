#!/bin/bash
###############################################################################
# startup_slim_core.sh — Unified core startup (Python and Rust)
#
# Invoked by supervisord [program:core]. Routes by environment:
#   VYRA_RUST     true  = Rust module, false/missing = Python (default)
#   VYRA_SLIM     true  = no ROS2 (direct app), false = ROS2 via startup_ros2_core.sh
#   VYRA_DEV_MODE + ENABLE_HOT_RELOAD = development hot reload
###############################################################################

set -euo pipefail

WORKSPACE_ROOT="${WORKSPACE_ROOT:-/workspace}"
LOG_DIR="${LOG_DIR:-/workspace/log}"

if [ -f "${WORKSPACE_ROOT}/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "${WORKSPACE_ROOT}/.env"
    set +a
fi

VYRA_RUST="${VYRA_RUST:-false}"
VYRA_SLIM="${VYRA_SLIM:-false}"
VYRA_DEV_MODE="${VYRA_DEV_MODE:-false}"
ENABLE_HOT_RELOAD="${ENABLE_HOT_RELOAD:-false}"
MODULE_NAME="${MODULE_NAME:-unknown_module}"

mkdir -p "${LOG_DIR}/core"

is_rust() {
    [[ "${VYRA_RUST,,}" == "true" ]]
}

is_slim() {
    [[ "${VYRA_SLIM,,}" == "true" ]]
}

is_dev_hot_reload() {
    [[ "${VYRA_DEV_MODE,,}" == "true" && "${ENABLE_HOT_RELOAD,,}" == "true" ]]
}

find_rust_binary() {
    local binary="" candidate binary_name hyphenated
    local -a binary_names=("$MODULE_NAME")
    hyphenated="${MODULE_NAME//_/-}"
    if [[ "$hyphenated" != "$MODULE_NAME" ]]; then
        binary_names+=("$hyphenated")
    fi
    for binary_dir in "${WORKSPACE_ROOT}/bin" "${WORKSPACE_ROOT}/target/release" "${WORKSPACE_ROOT}/target/debug"; do
        for binary_name in "${binary_names[@]}"; do
            candidate="${binary_dir}/${binary_name}"
            if [ -f "$candidate" ]; then
                binary="$candidate"
                echo "$binary"
                return 0
            fi
        done
    done
    return 1
}

start_python_hot_reload_background() {
    echo "🔥 Python hot reload: starting background watcher..."
    if ! pip show watchdog >/dev/null 2>&1; then
        echo "📦 Installing watchdog for hot reload..."
        pip install watchdog --break-system-packages
    fi
    nohup python3 "${WORKSPACE_ROOT}/tools/start_hot_reload.py" \
        "$MODULE_NAME" core core \
        >> "${LOG_DIR}/core/hot_reload.log" 2>&1 &
    echo "✅ Hot reload watcher started (PID: $!, log: ${LOG_DIR}/core/hot_reload.log)"
}

start_rust_hot_reload() {
    local features="full"
    local bin_name="${MODULE_NAME//_/-}"
    if is_slim; then
        features="slim"
    fi
    echo "🔥 Rust hot reload: exec start_hot_reload.sh (features=${features}, bin=${bin_name})"
    exec bash "${WORKSPACE_ROOT}/tools/start_hot_reload.sh" --features "$features" --bin "$bin_name"
}

start_python_slim() {
    export PYTHONPATH=""
    local src_dir="${WORKSPACE_ROOT}/src/${MODULE_NAME}"
    if [ ! -d "$src_dir" ]; then
        echo "❌ ERROR: Source directory ${src_dir} not found"
        exit 1
    fi
    export PYTHONPATH="${src_dir}:${PYTHONPATH}"
    export PYTHONUNBUFFERED=1
    echo "🚀 SLIM Python: python3 -m ${MODULE_NAME}.main"
    cd "${WORKSPACE_ROOT}"
    exec python3 -m "${MODULE_NAME}.main"
}

start_rust_slim() {
    local binary
    if ! binary="$(find_rust_binary)"; then
        echo "❌ Module binary not found (checked bin/, target/release/, target/debug/)"
        echo "   Expected names: ${MODULE_NAME} or ${MODULE_NAME//_/-}"
        exit 1
    fi
    echo "🚀 SLIM Rust: ${binary} --mode slim"
    exec "$binary" --mode slim
}

echo "=========================================="
echo "🔧 VYRA startup_slim_core.sh"
echo "=========================================="
echo "MODULE_NAME:      ${MODULE_NAME}"
echo "VYRA_RUST:        ${VYRA_RUST}"
echo "VYRA_SLIM:        ${VYRA_SLIM}"
echo "VYRA_DEV_MODE:    ${VYRA_DEV_MODE}"
echo "ENABLE_HOT_RELOAD:${ENABLE_HOT_RELOAD}"
echo "=========================================="

if is_dev_hot_reload; then
    if is_rust; then
        start_rust_hot_reload
    else
        start_python_hot_reload_background
    fi
fi

if is_slim; then
    if is_rust; then
        start_rust_slim
    else
        start_python_slim
    fi
else
    echo "🎯 FULL mode: delegating to startup_ros2_core.sh"
    if [ -f "${WORKSPACE_ROOT}/tools/startup_ros2_core.sh" ]; then
        exec bash "${WORKSPACE_ROOT}/tools/startup_ros2_core.sh"
    fi
    echo "❌ ERROR: startup_ros2_core.sh not found"
    exit 1
fi
