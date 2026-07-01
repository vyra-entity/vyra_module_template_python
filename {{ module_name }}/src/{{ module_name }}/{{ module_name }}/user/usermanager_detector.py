"""
User Manager Detection Module

Checks if an external usermanager module is available in the system
by querying registered modules.
"""

from ..logging_config import get_logger, log_exception, log_function_call, log_function_result
from typing import Optional, Dict, Any

from ..application.registry import ModuleRegistry

logger = get_logger(__name__)


class UserManagerDetector:
    """
    Detects availability of external usermanager module.

    Checks registered modules to find modules with blueprints "usermanager".
    """

    def __init__(self, module_registry: ModuleRegistry):
        """
        Initialize detector

        Args:
            module_registry: Module registry to query
        """
        self.module_registry = module_registry
        self._cache_timeout = 30  # seconds
        self._last_check: Optional[float] = None
        self._cached_result: Optional[Dict[str, Any]] = None

    async def check_usermanager_available(self) -> Dict[str, Any]:
        """
        Check if external usermanager module is available

        Queries registered modules for modules with blueprints="usermanager".

        Returns:
            Dict with:
                - available (bool): True if usermanager found
                - module_id (str): Module ID if found
                - module_name (str): Module name if found
                - message (str): Status message
        """
        try:
            # Get all registered modules
            result = await self.module_registry.get_registered_modules_impl(include_disabled=False)

            if not result or result.get("status") != 0:  # 0 = SUCCESS
                logger.warning("Failed to query registered modules")
                return {"available": False, "message": "Failed to query registered modules"}

            modules = result.get("modules", [])

            # Search for usermanager blueprints
            for module in modules:
                blueprints = str(module.get("blueprints", ""))
                if blueprints.lower() == "usermanager":
                    logger.info(f"✅ External usermanager found: {module.get('name')}")
                    return {
                        "available": True,
                        "module_id": module.get("id"),
                        "module_name": module.get("name"),
                        "namespace": module.get("namespace", ""),
                        "message": f"External usermanager '{module.get('name')}' is available",
                    }

            logger.info("No external usermanager module found")
            return {"available": False, "message": "No external usermanager module registered"}

        except Exception as e:
            logger.error(f"❌ Error checking usermanager availability: {e}", exc_info=True)
            return {"available": False, "message": f"Error checking usermanager: {str(e)}"}
