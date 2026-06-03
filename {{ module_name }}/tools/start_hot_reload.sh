#!/bin/bash
# start_hot_reload.sh — Rust: cargo-watch hot reload (exec from startup_slim_core.sh)
# Python: use start_hot_reload.py (background watcher via startup_slim_core.sh)
#
# Usage:
#   ./tools/start_hot_reload.sh [--features slim|full] [--bin BINARY_NAME]

set -euo pipefail

WORKSPACE_ROOT="${WORKSPACE_ROOT:-/workspace}"
cd "${WORKSPACE_ROOT}"

if [ -f "${WORKSPACE_ROOT}/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "${WORKSPACE_ROOT}/.env"
    set +a
fi

MODULE_NAME="${MODULE_NAME:-unknown_module}"
FEATURES="${VYRA_FEATURES:-slim}"
BIN_NAME="${MODULE_NAME//_/-}"

if [[ "${VYRA_SLIM:-false}" != "true" ]]; then
    FEATURES="full"
fi

while [[ $# -gt 0 ]]; do
    case $1 in
        --features) FEATURES="$2"; shift 2 ;;
        --bin)      BIN_NAME="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [--features slim|full] [--bin BINARY_NAME]"
            exit 0
            ;;
        *) echo "❌ Unknown option: $1 (use --help)"; exit 1 ;;
    esac
done

if ! command -v cargo-watch &>/dev/null; then
    if command -v cargo &>/dev/null; then
        echo "⚠️  cargo-watch not found — installing..."
        cargo install cargo-watch
    else
        echo "⚠️  cargo-watch not available — falling back to prebuilt binary"
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
            echo "❌ No prebuilt binary found"
            exit 1
        fi
        MODE_ARG="slim"
        [[ "$FEATURES" == "full" ]] && MODE_ARG="full"
        exec "$BINARY" --mode "$MODE_ARG"
    fi
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULE_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$MODULE_ROOT"

echo "🔥 Hot Reload — ${MODULE_NAME}"
echo "   Features: ${FEATURES}"
echo "   Binary:   ${BIN_NAME}"
echo "   Watch:    src/"
echo ""

exec cargo watch \
    -w src \
    ${CARGO_ARGS:-} \
    -x "run --features ${FEATURES} --bin ${BIN_NAME}"
