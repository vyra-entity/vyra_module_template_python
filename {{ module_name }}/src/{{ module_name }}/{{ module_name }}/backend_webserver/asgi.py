#!/usr/bin/env python3
"""
ASGI entry point for Backend Webserver

This module loads the FastAPI application from the backend_webserver package.
The backend communicates with core application components via dependency injection
(container_injection) instead of gRPC.
"""

import sys
import os
import json
from ..logging_config import get_logger, log_exception, log_function_call, log_function_result
import logging.config
import json

# Configure logging for the application
def setup_logging():
    """Setup logging configuration with ENV variable support"""
    log_config_path = "/workspace/config/backend_webserver_logging.json"
    
    # Get logging format from ENV variable
    logging_format = os.getenv('LOGGING_FORMAT', '%(asctime)s - %(levelname)-8s - %(name)s - %(message)s')
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()


    if os.path.exists(log_config_path):
        try:
            with open(log_config_path, 'r') as f:
                config = json.load(f)
            
            # Override format strings with ENV variable
            for formatter_name, formatter_config in config.get('formatters', {}).items():
                if 'format' in formatter_config:
                    formatter_config['format'] = logging_format
            
            logging.config.dictConfig(config)
            logging.getLogger().setLevel(log_level)
            print(f"✅ Logging configured from {log_config_path} with ENV format")
        except Exception as e:
            print(f"⚠️ Failed to load logging config: {e}")
            logging.basicConfig(
                level=logging.INFO,
                format=logging_format
            )
    else:
        print(f"ℹ️ Webserver backend logging config not found at {log_config_path}, "
              f"using basic config, and log webserver logs in core logs")
        logging.basicConfig(
            level=logging.INFO,
            format=logging_format,
            handlers=[
                logging.StreamHandler(),
            ]
        )

# Setup logging as early as possible
setup_logging()
logger = get_logger(__name__)

# Import FastAPI application from main_rest module
try:
    from .main_rest import app as application
    logger.info("✅ Successfully loaded FastAPI application from backend_webserver")
except ImportError as e:
    log_exception(logger, e, context={"message": "❌ Error importing FastAPI application: {e}"})
    
    # Create minimal error ASGI app
    async def application(scope, receive, send):
        if scope['type'] == 'http':
            await send({
                'type': 'http.response.start',
                'status': 500,
                'headers': [[b'content-type', b'application/json']],
            })
            await send({
                'type': 'http.response.body',
                'body': f'{{"error": "Could not import backend_webserver: {e}"}}'.encode('utf-8'),
            })
    
    logger.error("Using fallback error application")

if __name__ == "__main__":
    # Direct development testing with Uvicorn.
    # Reads /workspace/config/backend_webserver.json (same config as production runner
    # in main.py) to decide whether to start with HTTP or HTTPS.
    import uvicorn

    # Get module name dynamically
    module_name = os.getenv('MODULE_NAME', 'v2_modulemanager')
    app_path = f"{module_name}.{module_name}.backend_webserver.asgi:application"

    # Load backend webserver config file (analogous to nginx.conf for the frontend)
    webserver_config_path = "/workspace/config/backend_webserver.json"
    webserver_config: dict = {}
    if os.path.exists(webserver_config_path):
        try:
            with open(webserver_config_path, "r") as _f:
                webserver_config = json.load(_f)
            logger.info(f"✅ Loaded backend webserver config from {webserver_config_path}")
        except Exception as _e:
            logger.warning(f"⚠️ Failed to load {webserver_config_path}: {_e} — using HTTP")
    else:
        logger.warning(f"ℹ️ {webserver_config_path} not found — defaulting to HTTP mode")

    host = webserver_config.get("host", "0.0.0.0")
    port = int(webserver_config.get("port", 8443))
    use_ssl = bool(webserver_config.get("use_ssl", False))
    reload_dirs = [f"/workspace/src/{module_name}/{module_name}/backend_webserver"]

    ssl_kwargs: dict = {}
    if use_ssl:
        cert_path = "/workspace/storage/certificates/webserver.crt"
        key_path = "/workspace/storage/certificates/webserver.key"
        cert_readable = (
            os.path.exists(cert_path) and os.path.exists(key_path)
            and os.access(cert_path, os.R_OK) and os.access(key_path, os.R_OK)
        )
        if cert_readable:
            logger.info("🔒 Starting with SSL/TLS encryption")
            ssl_kwargs = {"ssl_certfile": cert_path, "ssl_keyfile": key_path}
        else:
            logger.error("❌ use_ssl=true in config but certificates are missing/unreadable — falling back to HTTP")

    if not ssl_kwargs:
        logger.info(f"🌐 Starting without SSL/TLS on http://{host}:{port}")

    uvicorn.run(
        app_path,
        host=host,
        port=port,
        reload=True,
        reload_dirs=reload_dirs,
        log_level="debug",
        **ssl_kwargs
    )
