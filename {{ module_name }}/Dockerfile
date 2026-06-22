# syntax=docker/dockerfile:1
# ==============================================================================
# VYRA Module Dockerfile - Optimized Multi-Stage Build
# Module: ${MODULE_NAME}
# ==============================================================================
# Local Development: Uses vyra_image_python:development/production (built locally)
# CI/CD Production: Uses ghcr.io/vyra-entity/vyra_framework:development/slim
# ==============================================================================

# Base image configuration (override with --build-arg)
# Development (VYRA_DEV_MODE=true):  Uses full-development (includes Node.js for Vite)
# Production (VYRA_DEV_MODE=false):  Uses full-production (minimal runtime)
ARG BUILDER_BASE_IMAGE=vyra_image_python:development
ARG RUNTIME_BASE_IMAGE=vyra_image_python:production

# =============================================================================
# Stage 1: Builder - Build ROS2 packages and frontend
# =============================================================================
FROM ${BUILDER_BASE_IMAGE} AS builder

# Module name from build argument
ARG MODULE_NAME
ENV MODULE_NAME=${MODULE_NAME}

# SLIM mode flag: when true, skip all ROS2 build steps (no colcon, no interfaces)
# Set to true when building a SLIM module (e.g. --build-arg VYRA_SLIM=true)
ARG VYRA_SLIM=false
ENV VYRA_SLIM=${VYRA_SLIM}

# Switch to root for build operations (base image may end with USER vyrauser)
USER root

WORKDIR /workspace

# =============================================================================
# Poetry Installation & Dependency Management (Cache-Optimized)
# =============================================================================
# Install Poetry for professional dependency management
# --ignore-installed packaging: avoids conflict with Debian-managed packaging package
RUN pip install --no-cache-dir poetry --break-system-packages --ignore-installed packaging

# Configure Poetry to NOT create virtualenvs (use system Python in container)
RUN poetry config virtualenvs.create false

# THE TRICK: Copy ONLY dependency files first (enables Docker layer caching)
# If you only change your code, this layer will be CACHED
COPY --chown=vyrauser:vyrauser pyproject.toml poetry.lock* ./

# Install dependencies WITHOUT the project itself (--no-root)
# This layer is cached unless pyproject.toml or poetry.lock change
RUN poetry install --only main -E slim --no-interaction --no-ansi --no-root || \
    echo "⚠️  Poetry install failed, continuing with fallback pip installation"

# =============================================================================
# Module Source Code & Configuration
# =============================================================================
# Clean workspace from base image to prevent stale packages
RUN rm -rf /workspace/src /workspace/build /workspace/install

# NOW copy the actual module sources (changes here don't invalidate dependency cache)
COPY --chown=vyrauser:vyrauser src/ ./src/
COPY --chown=vyrauser:vyrauser config/ ./config/
COPY --chown=vyrauser:vyrauser tools/ ./tools/
COPY --chown=vyrauser:vyrauser --chmod=755 vyra_entrypoint.sh ./vyra_entrypoint.sh
COPY --chown=vyrauser:vyrauser --chmod=755 .module/ ./.module/
COPY --chown=vyrauser:vyrauser frontend/ ./frontend/
COPY --chown=vyrauser:vyrauser wheels/ ./wheels/
COPY --chown=vyrauser:vyrauser storage/ ./storage/

# Install the project itself (now WITH all the heavy dependencies already cached)
RUN poetry install --only main -E slim --no-interaction --no-ansi || \
    echo "⚠️  Poetry project install failed, using wheel installation as fallback"

# Fallback: Install module-specific Python dependencies via pip (legacy support)
RUN if [ -f ".module/requirements.txt" ]; then \
        pip install --no-cache-dir --timeout 120 --retries 5 -r .module/requirements.txt --break-system-packages; \
    fi

# Run pre-install script if exists (for custom repository setup, etc.)
RUN if [ -f ".module/pre-install.sh" ]; then \
        bash .module/pre-install.sh; \
    fi

# Install module-specific system packages
RUN if [ -f ".module/system-packages.txt" ]; then \
        apt-get update -o Acquire::ForceIPv4=true -qq && \
        while read -r package; do \
            [ -z "$package" ] && continue; \
            [ "${package:0:1}" = "#" ] && continue; \
            apt-get install -o Acquire::ForceIPv4=true -y "$package" || echo "⚠️  Failed to install $package, skipping"; \
        done < .module/system-packages.txt; \
        apt-get clean && rm -rf /var/lib/apt/lists/*; \
    fi

# Install wheel dependencies (vyra_base and module-specific wheels) [only latest versions]
# Cache-buster: Force fresh installation even if wheel files appear unchanged
ARG CACHE_BUST
# Store build ID so the entrypoint can detect when the image was rebuilt
# This ensures a stale volume-mounted install/ is discarded on new deployments
RUN echo "${CACHE_BUST:-$(date +%s)}" > /opt/vyra/build_id && \
    echo "✅ Build ID stored: $(cat /opt/vyra/build_id)"
RUN echo "Cache bust: ${CACHE_BUST:-$(date +%s)}" && \
    if [ -d "wheels" ]; then \
        set -e; \
        tmpdir=$(mktemp -d); \
        \
        echo "📦 Installing wheel packages (force-reinstall):"; \
        for pkg in $(ls wheels/*.whl | sed -E 's#.*/([^/-]+)-.*#\1#' | sort -u); do \
            latest=$(ls wheels/"$pkg"-*.whl | sort -V | tail -n 1); \
            echo "  - $(basename "$latest")"; \
            cp "$latest" "$tmpdir/"; \
        done; \
        \
        pip install "$tmpdir"/*.whl \
            --break-system-packages \
            --force-reinstall \
            --no-deps \
            --ignore-installed cryptography; \
        \
        echo "✅ Wheel installation complete"; \
        rm -rf "$tmpdir"; \
        # pip install wheels/*.whl --break-system-packages --ignore-installed cryptography || true; \
    fi

# Setup Proto interfaces (for vyra_callables via gRPC/Redis transport)
RUN python3 tools/setup_proto_interfaces.py || echo "No Proto interfaces found (optional)"

# Generate gRPC proto files from .proto definitions
RUN if [ -d "storage/interfaces" ] && [ "$(ls -A storage/interfaces/*.proto 2>/dev/null)" ]; then \
        echo "🔧 Generating gRPC proto files..."; \
        python3 tools/generate_grpc_protos.py \
            --proto-dir /workspace/storage/interfaces \
            --output-dir /workspace/src/rest_api/grpc_generated \
            --create-helpers; \
    else \
        echo "No .proto files found in storage/interfaces, skipping gRPC generation"; \
    fi

# =============================================================================
# ROS2 Interface Build & Colcon Compilation
# When VYRA_SLIM=true: skip all ROS2 build steps, create stub dirs for COPY
# When VYRA_SLIM=false (default): full ROS2 interface generation and colcon build
# =============================================================================
RUN if [ "${VYRA_SLIM:-false}" = "true" ]; then \
        echo "🎯 SLIM Mode: Skipping ROS2 build, interface generation and colcon"; \
        mkdir -p /workspace/install /opt/vyra/install_backup; \
    else \
        echo "🎯 Full ROS2 Mode: Building interfaces and running colcon"; \
        rm -rf /workspace/install /workspace/build; \
        \
        # Install gRPC C++ plugin for proto code generation \
        apt-get update -o Acquire::ForceIPv4=true -qq && \
        (apt-get install -o Acquire::ForceIPv4=true -y --no-install-recommends protobuf-compiler-grpc 2>/dev/null || \
         echo "⚠️  grpc_cpp_plugin not available - C++ proto generation will be skipped") && \
        apt-get clean && rm -rf /var/lib/apt/lists/*; \
        \
        # Resolve MODULE_NAME if not provided via build arg \
        if [ -z "$MODULE_NAME" ] && [ -f ".module/module_data.yaml" ]; then \
            export MODULE_NAME=$(grep "^name:" .module/module_data.yaml | cut -d: -f2 | tr -d ' ' | tr -d "'" | tr -d '"'); \
        fi; \
        \
        # Generate ROS2 interface definitions (must run after wheel install) \
        python3 tools/setup_interfaces.py --interface_pkg "${MODULE_NAME}_interfaces"; \
        \
        # Source ROS2 underlay and build with colcon \
        . /opt/ros/kilted/setup.bash; \
        colcon --log-base log/ros2 build --packages-skip vyra_module_template_interfaces --cmake-args -DCMAKE_BUILD_TYPE=Release; \
        \
        # Stamp install/ with build ID so entrypoint can detect stale mounts \
        cp /opt/vyra/build_id /workspace/install/.build_id; \
        \
        # Fix ownership for runtime stage COPY \
        chown -R vyrauser:vyrauser /workspace/install; \
        \
        # Backup install/ for volume-mount scenarios (entrypoint restores from here) \
        cp -r /workspace/install /opt/vyra/install_backup; \
        chown -R vyrauser:vyrauser /opt/vyra/install_backup; \
    fi

# Extract MODULE_NAME from module_data.yaml if not provided
RUN if [ -z "$MODULE_NAME" ] && [ -f ".module/module_data.yaml" ]; then \
        export MODULE_NAME=$(grep "^name:" .module/module_data.yaml | cut -d: -f2 | tr -d ' ' | tr -d "'" | tr -d '"'); \
        echo "MODULE_NAME extracted from module_data.yaml: $MODULE_NAME"; \
    fi && \
    echo "MODULE_NAME=${MODULE_NAME}" > /tmp/module_name.env

# Copy module-specific interfaces to staging area for NFS volume (skip in SLIM mode)
RUN if [ "${VYRA_SLIM:-false}" = "true" ]; then \
        echo "🎯 SLIM Mode: Skipping interface staging (no ROS2 interfaces)"; \
        mkdir -p /tmp/module_interfaces_staging; \
    else \
        source /tmp/module_name.env && \
        mkdir -p /tmp/module_interfaces_staging/${MODULE_NAME}_interfaces && \
        if [ -d "install/${MODULE_NAME}_interfaces" ]; then \
            echo "📦 Staging ${MODULE_NAME}_interfaces for NFS volume..."; \
            cp -r install/${MODULE_NAME}_interfaces/* /tmp/module_interfaces_staging/${MODULE_NAME}_interfaces/; \
            echo "✓ Interfaces staged at /tmp/module_interfaces_staging/${MODULE_NAME}_interfaces"; \
        else \
            echo "⚠️  No ${MODULE_NAME}_interfaces found in install directory"; \
        fi; \
    fi

RUN mkdir -p ./storage/sros2_keystore

RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    set -e; \
    if [ -d "frontend" ] && [ -f "frontend/package.json" ] && [ -d "frontend/src" ] && [ "$(ls -A frontend/src)" ] && command -v npm >/dev/null 2>&1; then \
        cd frontend && \
        npm install && \
        npm run build && \
        mkdir -p /opt/vyra/frontend_dist_backup && \
        cp -a dist/. /opt/vyra/frontend_dist_backup/ && \
        rm -rf /opt/vyra/frontend_node_modules_backup && \
        cp -a node_modules /opt/vyra/frontend_node_modules_backup && \
        test -x /opt/vyra/frontend_node_modules_backup/.bin/vite && \
        rm -rf node_modules && \
        cd ..; \
    elif [ -d "frontend/dist" ] && [ "$(ls -A frontend/dist 2>/dev/null)" ]; then \
        echo "⚠️  npm not in builder image — using pre-built frontend/dist"; \
        mkdir -p /opt/vyra/frontend_dist_backup /opt/vyra/frontend_node_modules_backup; \
        cp -a frontend/dist/. /opt/vyra/frontend_dist_backup/; \
    else \
        echo "⚠️  Skipping frontend build (template or incomplete structure)"; \
        mkdir -p frontend/dist /opt/vyra/frontend_dist_backup /opt/vyra/frontend_node_modules_backup; \
        echo '<!DOCTYPE html><html><body><h1>Frontend Template</h1></body></html>' > frontend/dist/index.html; \
        cp frontend/dist/index.html /opt/vyra/frontend_dist_backup/index.html; \
    fi; \
    mkdir -p /opt/vyra/frontend_dist_backup /opt/vyra/frontend_node_modules_backup

# Normalize vyra_base to a fixed staging path.
# The SLIM base image installs packages to site-packages while the full ROS2 base
# image uses dist-packages. By staging here we avoid a hardcoded path in the
# runtime COPY instruction.
RUN python3 -c \
    "import vyra_base, shutil, os; \
     src = os.path.dirname(os.path.abspath(vyra_base.__file__)); \
     shutil.copytree(src, '/tmp/vyra_base_staging', dirs_exist_ok=True)" && \
    echo "✅ vyra_base staged from $(python3 -c 'import vyra_base,os; print(os.path.dirname(os.path.abspath(vyra_base.__file__)))')"

# =============================================================================
# Stage 2: Runtime - Minimal module layer (~150MB)
# =============================================================================
FROM ${RUNTIME_BASE_IMAGE} AS runtime

ARG MODULE_NAME
ENV MODULE_NAME=${MODULE_NAME}

# Switch to root for system operations (base image ends with vyrauser)
USER root

# Copy module name from builder stage if not set
COPY --from=builder /tmp/module_name.env /tmp/module_name.env
RUN if [ -z "$MODULE_NAME" ]; then \
        . /tmp/module_name.env; \
    fi

WORKDIR /workspace

# Copy ONLY module artifacts (base image already has everything else)
COPY --from=builder /workspace/install/ ./install/
# Note: build/ directory not needed in runtime (only for compilation)

# Override vyra_base with the version installed in the builder stage.
# Uses a staging path to handle dist-packages (full ROS2 base) vs site-packages
# (SLIM base) transparently, without hardcoding the Python installation path.
COPY --from=builder /tmp/vyra_base_staging/ /tmp/vyra_base_staging/
RUN python3 -c \
    "import site, shutil; \
     target = site.getsitepackages()[0] + '/vyra_base'; \
     shutil.copytree('/tmp/vyra_base_staging', target, dirs_exist_ok=True)" && \
    rm -rf /tmp/vyra_base_staging

COPY --from=builder /workspace/config/ ./config/
COPY --from=builder /workspace/tools/ ./tools/
COPY --from=builder /workspace/vyra_entrypoint.sh ./vyra_entrypoint.sh
COPY --from=builder /workspace/.module/ ./.module/
COPY --from=builder /workspace/frontend/dist ./frontend/dist
COPY --from=builder /workspace/src/ ./src/
COPY --from=builder /workspace/storage/ ./storage/

# Copy staged interfaces for NFS volume initialization
COPY --from=builder /tmp/module_interfaces_staging/ /tmp/module_interfaces_staging/

# Backup install/ directory for volume mount scenarios
# When workspace is mounted as volume, install/ from image is hidden
COPY --from=builder --chown=vyrauser:vyrauser /opt/vyra/install_backup /opt/vyra/install_backup

# Backup frontend assets for volume mount scenarios (vyra-network is internal — no npm registry access)
COPY --from=builder --chown=vyrauser:vyrauser /opt/vyra/frontend_dist_backup /opt/vyra/frontend_dist_backup
COPY --from=builder --chown=vyrauser:vyrauser /opt/vyra/frontend_node_modules_backup /opt/vyra/frontend_node_modules_backup

# Create runtime directories
RUN mkdir -p log/ros2 log/nginx storage

# Make logs and storage accessable by vyrauser (and writable by all users for ROS2 logging)
RUN chown -R vyrauser:vyrauser /workspace/log /workspace/storage


# Install Python dependencies for runtime (uvicorn, fastapi, etc.)
RUN if [ -f ".module/requirements.txt" ]; then \
        pip install --no-cache-dir --timeout 120 --retries 5 -r .module/requirements.txt --break-system-packages; \
    fi

# ROS2 environment configuration
ENV ROS_DOMAIN_ID=42
ENV VYRA_BUILD_MODE=ci

LABEL maintainer="VYRA Development Team" \
      org.opencontainers.image.source="https://github.com/variobotic-gmbh/${MODULE_NAME}" \
      module="${MODULE_NAME}" \
      stage="runtime"

# Note: Container runs as root to allow supervisord to manage processes
# Individual programs run as vyrauser (configured in supervisord.conf)

CMD ["/workspace/vyra_entrypoint.sh"]

