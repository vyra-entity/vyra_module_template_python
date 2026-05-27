#!/bin/bash
# setup_interfaces.sh — Entry point for VYRA interface setup (Python module).
#
# Thin wrapper around setup_interfaces.py.  All logic lives in the Python
# script so that the implementation is identical across all module types.
#
# Usage: ./tools/setup_interfaces.sh [args forwarded to setup_interfaces.py]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "------------------------------------------------------------"
echo " Setting up module interfaces (Python module)"
echo " Script: $SCRIPT_DIR/setup_interfaces.py"
echo "------------------------------------------------------------"

exec python3 "$SCRIPT_DIR/setup_interfaces.py" "$@"
