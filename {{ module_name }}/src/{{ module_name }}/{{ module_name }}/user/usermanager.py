"""
User Manager Runner for {{ module_name }}

This module provides the main entry point for the user manager task.
It initializes the internal user manager.
"""
import asyncio
from ..logging_config import get_logger, log_exception, log_function_call, log_function_result
import os
from pathlib import Path

from vyra_base.core.entity import VyraEntity

from .internal_usermanager import InternalUserManager

logger = get_logger(__name__)


class UserManager:
    """
    User Manager orchestrator.
    
    Manages internal user manager and server for user authentication.
    """
    
    def __init__(self, entity: VyraEntity):
        """
        Initialize User Manager.
        
        Args:
            entity: VyraEntity instance
        """
        self.entity = entity
        self.internal_usermanager: InternalUserManager
        
        logger.info("🔧 User Manager initializing...")
    
    async def initialize(self) -> bool:
        """
        Initialize internal user manager and gRPC server.
        
        Returns:
            bool: True if initialization successful
        """
        try:
            # Initialize internal user manager
            self.internal_usermanager = InternalUserManager(self.entity)
            
            # Register ROS2 interfaces (if needed)
            await self.internal_usermanager.register_endpoints()
            
            # Initialize default admin user from .env if no users exist
            initial_admin_user = os.getenv('INITIAL_ADMIN_USER', 'admin')
            initial_admin_password = os.getenv('INITIAL_ADMIN_PASSWORD', 'admin')
            
            await self.internal_usermanager.initialize_admin_from_env(
                initial_admin_user,
                initial_admin_password
            )
            
            logger.info("✅ User Manager initialized successfully")
            return True
            
        except Exception as e:
            log_exception(logger, e, context={"message": "❌ Failed to initialize User Manager: {e}"})
            return False
    
    async def shutdown(self):
        """Shutdown user manager."""
        logger.info("✅ User Manager stopped")


async def usermanager_runner(entity: VyraEntity) -> None:
    """
    User Manager task runner.
    
    This function runs as an asyncio task managed by TaskManager.
    
    Args:
        entity: VyraEntity instance
    """
    logger.info('🚀 Starting User Manager runner...')
    
    usermanager = UserManager(entity)
    
    try:
        # Initialize user manager
        if not await usermanager.initialize():
            logger.error("❌ Failed to initialize User Manager")
            return
        
        # Keep the task running (gRPC server runs in background)
        logger.info("✅ User Manager is running")
        
        # Keep alive - the task should run until cancelled
        
        while True:
            await asyncio.sleep(1)
            
    except asyncio.CancelledError:
        logger.info('User Manager runner task cancelled.')
    except Exception as e:
        log_exception(logger, e, context={"message": "❌ User Manager runner error: {e}"})
    finally:
        await usermanager.shutdown()
        logger.info('User Manager runner finished.')
