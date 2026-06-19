#!/bin/bash
# ==============================================================================
# post_copier_setup.sh
# ==============================================================================
# Called by copier _tasks right after the template is copied.
#
# What this script does:
#   1. Generates a UUID v4 (32 hex chars, no dashes) for the new module instance
#   2. Writes the UUID into .module/module_data.yaml  (uuid field)
#   3. Renames/moves the current module directory to the canonical layout:
#        <parent>/<module_name>_<uuid>/<version>/
#   4. Optionally builds the Docker image (BUILD_AFTER_COPY=true)
#
# Usage (invoked by copier _tasks; executed inside the newly-created module dir):
#   bash tools/post_copier_setup.sh [--build]
#
# Environment variables (alternative to flags):
#   BUILD_AFTER_COPY=true      → runs quick_rebuild_module.sh
# ==============================================================================
set -e

MODULE_DIR=$(cd "$(dirname "$0")/.." && pwd)
TOOLS_DIR="$MODULE_DIR/tools"

# ---------------------------------------------------------------------------
# Parse flags
# ---------------------------------------------------------------------------
BUILD=false

for arg in "$@"; do
    case "$arg" in
        --build)   BUILD=true ;;
    esac
done
[[ "${BUILD_AFTER_COPY:-false}" == "true" ]] && BUILD=true

# ---------------------------------------------------------------------------
# 1. Read module metadata
# ---------------------------------------------------------------------------
MODULE_DATA="$MODULE_DIR/.module/module_data.yaml"
if [ ! -f "$MODULE_DATA" ]; then
    echo "❌ .module/module_data.yaml not found: $MODULE_DATA"
    exit 1
fi

if command -v yq &>/dev/null; then
    MODULE_NAME=$(yq '.name' "$MODULE_DATA" 2>/dev/null)
    MODULE_VERSION=$(yq '.version' "$MODULE_DATA" 2>/dev/null)
else
    MODULE_NAME=$(grep "^name:" "$MODULE_DATA" | cut -d: -f2 | tr -d ' "')
    MODULE_VERSION=$(grep "^version:" "$MODULE_DATA" | cut -d: -f2 | tr -d ' "')
fi

if [ -z "$MODULE_NAME" ] || [ -z "$MODULE_VERSION" ]; then
    echo "❌ Could not read name/version from .module/module_data.yaml"
    exit 1
fi

echo "📦 Module:  $MODULE_NAME"
echo "🔖 Version: $MODULE_VERSION"

# ---------------------------------------------------------------------------
# 2. Generate UUID v4 (32 hex chars, no dashes)
# ---------------------------------------------------------------------------
UUID=$(python3 -c "import uuid; print(uuid.uuid4().hex)")
echo "🆔 UUID:    $UUID"

# ---------------------------------------------------------------------------
# 3. Update .module/module_data.yaml with the generated UUID
# ---------------------------------------------------------------------------
python3 - "$MODULE_DATA" "$UUID" <<'PYEOF'
import sys
import re

module_data_path = sys.argv[1]
new_uuid = sys.argv[2]

with open(module_data_path, "r") as f:
    content = f.read()

# Replace existing uuid field (handles empty, quoted, or non-empty values)
if re.search(r'^uuid:', content, re.MULTILINE):
    content = re.sub(r'^uuid:.*$', f'uuid: "{new_uuid}"', content, flags=re.MULTILINE)
else:
    content = content.rstrip("\n") + f'\nuuid: "{new_uuid}"\n'

with open(module_data_path, "w") as f:
    f.write(content)

print(f"✅ UUID written to module_data.yaml")
PYEOF

# ---------------------------------------------------------------------------
# 4. Move module directory to: <parent>/<module_name>_<uuid>/<version>/
# ---------------------------------------------------------------------------
PARENT_DIR=$(dirname "$MODULE_DIR")
CURRENT_BASENAME=$(basename "$MODULE_DIR")

# Target structure
NEW_INSTANCE_DIR="$PARENT_DIR/${MODULE_NAME}_${UUID}"
NEW_VERSION_DIR="$NEW_INSTANCE_DIR/$MODULE_VERSION"

# Guard: already in the canonical layout (name_uuid/version)?
if [[ "$CURRENT_BASENAME" =~ ^${MODULE_NAME}_[a-f0-9]{32}$ ]]; then
    # We are already <module_name>_<uuid>; just create the version subdirectory
    NEW_VERSION_DIR="$MODULE_DIR/$MODULE_VERSION"
    mkdir -p "$NEW_VERSION_DIR"
    echo "📂 Module already in instance dir, creating version subdir: $NEW_VERSION_DIR"
    # Move contents into version subdir (avoid moving into itself)
    for item in "$MODULE_DIR"/*; do
        [ "$(basename "$item")" = "$MODULE_VERSION" ] && continue
        mv "$item" "$NEW_VERSION_DIR/"
    done
    # Move hidden files/dirs too
    for item in "$MODULE_DIR"/.[!.]*; do
        [ -e "$item" ] || continue
        mv "$item" "$NEW_VERSION_DIR/"
    done
else
    # Create the canonical directory structure and move into it
    mkdir -p "$NEW_INSTANCE_DIR"
    mv "$MODULE_DIR" "$NEW_VERSION_DIR"
    echo "📂 Moved: $MODULE_DIR"
    echo "      → $NEW_VERSION_DIR"
fi

NEW_MODULE_DIR="$NEW_VERSION_DIR"
cd "$NEW_MODULE_DIR"

echo ""
echo "✅ Module directory layout:"
echo "   $NEW_INSTANCE_DIR/"
echo "   └── $MODULE_VERSION/   ← $(basename "$NEW_MODULE_DIR")"

# ---------------------------------------------------------------------------
# 4.5. Update pyproject.toml from module_data.yaml
# ---------------------------------------------------------------------------
echo ""
echo "📝 Syncing module_data.yaml to pyproject.toml [tool.poetry]..."
python3 - "$NEW_MODULE_DIR/.module/module_data.yaml" "$NEW_MODULE_DIR/pyproject.toml" <<'PYEOF'
import sys
import re

yaml_path = sys.argv[1]
toml_path = sys.argv[2]

try:
    with open(yaml_path, 'r') as f:
        yaml_content = f.read()

    name = re.search(r'^name:\s*"?([^"\n]+)"?', yaml_content, re.MULTILINE)
    version = re.search(r'^version:\s*"?([^"\n]+)"?', yaml_content, re.MULTILINE)
    desc = re.search(r'^description:\s*"?([^"\n]+)"?', yaml_content, re.MULTILINE)

    with open(toml_path, 'r') as f:
        toml_content = f.read()

    # Split into tool.poetry part and the rest
    poetry_match = re.search(r'^\[tool\.poetry\]\s*\n(.*?)(?=\n\[|$)', toml_content, re.DOTALL | re.MULTILINE)
    if poetry_match:
        poetry_body = poetry_match.group(1)
        
        if name:
            poetry_body = re.sub(r'^(name\s*=\s*)"[^"]*"', f'\\g<1>"{name.group(1)}"', poetry_body, flags=re.MULTILINE)
        if version:
            poetry_body = re.sub(r'^(version\s*=\s*)"[^"]*"', f'\\g<1>"{version.group(1)}"', poetry_body, flags=re.MULTILINE)
        if desc:
            poetry_body = re.sub(r'^(description\s*=\s*)"[^"]*"', f'\\g<1>"{desc.group(1)}"', poetry_body, flags=re.MULTILINE)

        new_toml = toml_content[:poetry_match.start(1)] + poetry_body + toml_content[poetry_match.end(1):]
        with open(toml_path, 'w') as f:
            f.write(new_toml)
        
        print("✅ Successfully updated pyproject.toml [tool.poetry] from module_data.yaml")
except Exception as e:
    print(f"⚠️ Error syncing: {e}")
PYEOF

# ---------------------------------------------------------------------------
# 5. Optionally build Docker image
# ---------------------------------------------------------------------------
if [ "$BUILD" = "true" ]; then
    echo ""
    echo "🔨 Building Docker image..."
    # Find workspace root
    WORKSPACE="$NEW_MODULE_DIR"
    while [ "$WORKSPACE" != "/" ] && [ ! -f "$WORKSPACE/docker-compose.yml" ]; do
        WORKSPACE=$(dirname "$WORKSPACE")
    done
    if [ ! -f "$WORKSPACE/docker-compose.yml" ]; then
        echo "⚠️  Workspace root not found (no docker-compose.yml). Build skipped."
        echo "   Manual build: cd <VOS2_WORKSPACE> && ./tools/quick_rebuild_module.sh $MODULE_NAME --all-variants"
    else
        DEST_REL="${NEW_MODULE_DIR#$WORKSPACE/}"
        cd "$WORKSPACE"
        bash ./tools/quick_rebuild_module.sh "$MODULE_NAME" --module-dir "$DEST_REL" --all-variants \
            || echo "⚠️  Build failed. Manual: cd $WORKSPACE && ./tools/quick_rebuild_module.sh $MODULE_NAME --all-variants"
        cd "$NEW_MODULE_DIR"
    fi
fi

echo ""
echo "🎉 Setup complete!"
echo "   New module path: $NEW_MODULE_DIR"
