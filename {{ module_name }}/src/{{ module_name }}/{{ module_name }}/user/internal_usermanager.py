"""
Internal User Manager for VYRA Module Manager

Handles local user management when no external usermanager module is available.
Provides user CRUD operations, authentication, and authorization.
"""

from ..logging_config import get_logger, log_exception, log_function_call, log_function_result
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from vyra_base.core.entity import VyraEntity
from vyra_base.storage.db_manipulator import DbManipulator, DBReturnValue
from vyra_base.com.clients.sql import DBSTATUS
from vyra_base.com import remote_service

try:
    from vyra_base.helper.crypto_helper import hash_password_bcrypt, verify_password_bcrypt
except ImportError:
    import bcrypt as _bcrypt  # type: ignore

    def hash_password_bcrypt(password: str) -> str:  # type: ignore[misc]
        """Fallback: hash a password using bcrypt directly."""
        return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()

    def verify_password_bcrypt(password: str, hashed: str) -> bool:  # type: ignore[misc]
        """Fallback: verify a bcrypt-hashed password."""
        return _bcrypt.checkpw(password.encode(), hashed.encode())


from .tb_users import User, UserRole, UserLevel
from ..interface import register_endpoint_callbacks

logger = get_logger(__name__)


class InternalUserManager:
    """
    Internal User Manager for local user management.

    Manages users, authentication, and authorization when no external
    usermanager module is available.
    """

    def __init__(self, entity: VyraEntity):
        """
        Initialize Internal User Manager

        Args:
            entity: VyraEntity instance for database and ROS2 access
        """
        self.entity = entity

        # Database manipulator for User table
        self.user_manipulator = DbManipulator(entity.database_access, User)

        # Security settings
        self.max_login_attempts = 5
        self.lockout_duration = timedelta(minutes=15)
        self.password_min_length = 4  # Minimum password length

        logger.info("✅ Internal User Manager initialized")

    async def register_endpoints(self):
        """Register ROS2 callable interfaces"""
        register_endpoint_callbacks(self.entity, callback_parent=self)

    async def initialize_default_admin(self) -> bool:
        """
        Create default admin user if no users exist

        Default credentials:
        - Username: admin
        - Password: admin
        - Role: ADMIN
        - Level: LEVEL_4

        Returns:
            bool: True if admin was created or already exists
        """
        try:
            # Check if any users exist
            result: DBReturnValue = await self.user_manipulator.get_all()

            if (
                result.status == DBSTATUS.SUCCESS
                and isinstance(result.value, list)
                and len(result.value) == 0
            ):
                # No users exist, create default admin
                admin_data = {
                    "username": "admin",
                    "password_hash": self._hash_password("admin"),
                    "role": UserRole.ADMIN,
                    "level": UserLevel.LEVEL_4,
                    "enabled": True,
                    "permissions": {
                        "modules": ["*"],  # Access to all modules
                        "operations": ["*"],  # All operations
                    },
                    "user_metadata": {
                        "password_change_required": True  # Force password change on first login
                    },
                }

                result = await self.user_manipulator.add(admin_data)

                if result.status == DBSTATUS.SUCCESS:
                    logger.info("✅ Default admin user created (username: admin, password: admin)")
                    logger.warning("⚠️  Please change the default admin password after first login!")
                    return True
                else:
                    logger.error(f"❌ Failed to create default admin: {result.details}")
                    return False

            logger.info("ℹ️  Users already exist, skipping default admin creation")
            return True

        except Exception as e:
            logger.error(f"❌ Error initializing default admin: {e}", exc_info=True)
            return False

    async def initialize_admin_from_env(self, username: str, password: str) -> bool:
        """
        Create admin user from environment variables if no users exist.

        This method is called during startup to ensure an admin user exists.
        If no users are found in the database, it creates an admin user with
        credentials from INITIAL_ADMIN_USER and INITIAL_ADMIN_PASSWORD env vars.

        Args:
            username: Admin username from INITIAL_ADMIN_USER env var
            password: Admin password from INITIAL_ADMIN_PASSWORD env var

        Returns:
            bool: True if admin was created or already exists
        """
        try:
            # Check if any users exist
            result: DBReturnValue = await self.user_manipulator.get_all()

            if result.status == DBSTATUS.NOT_FOUND or (
                result.status == DBSTATUS.SUCCESS
                and isinstance(result.value, list)
                and len(result.value) == 0
            ):
                logger.info(f"📝 No users found, creating initial admin user: {username}")

                # No users exist, create admin from env vars
                admin_data = {
                    "username": username,
                    "password_hash": self._hash_password(password),
                    "role": UserRole.ADMIN,
                    "level": UserLevel.LEVEL_4,
                    "enabled": True,
                    "permissions": {
                        "modules": ["*"],  # Access to all modules
                        "operations": ["*"],  # All operations
                    },
                    "user_metadata": {
                        "password_change_required": True  # Force password change on first login
                    },
                }

                result = await self.user_manipulator.add(admin_data)

                if result.status == DBSTATUS.SUCCESS:
                    logger.info(f"✅ Initial admin user created: {username}")
                    logger.warning("⚠️  Password change required on first login!")
                    return True
                else:
                    logger.error(f"❌ Failed to create initial admin: {result.details}")
                    return False

            if isinstance(result.value, list):
                logger.info(
                    f"ℹ️  Users already exist ({len(result.value)} users), skipping admin creation"
                )
            else:
                logger.warning(f"⚠️  Unexpected result when checking for existing users: {result}")
            return True

        except Exception as e:
            logger.error(f"❌ Error initializing admin from env: {e}", exc_info=True)
            return False

    # =============================================================================
    # Authentication Methods
    # =============================================================================

    async def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """
        Authenticate user with username and password

        Args:
            username: Username
            password: Plain text password

        Returns:
            User info dict if authenticated, None otherwise
        """
        try:
            # Get user by username
            result: DBReturnValue = await self.user_manipulator.get_all(
                filters={"username": username}
            )

            if result.status != DBSTATUS.SUCCESS or not result.value:
                logger.warning(f"🔒 Authentication failed: User '{username}' not found")
                return None

            if isinstance(result.value, list):
                user: User = result.value[0]
            else:
                logger.error(f"❌ Unexpected result type when retrieving user: {result}")
                return None

            # Check if account is locked
            if user.locked_until and datetime.now(user.locked_until.tzinfo) < user.locked_until:
                logger.warning(f"🔒 Account locked until {user.locked_until}: {username}")
                return None

            # Check if account is enabled
            if not user.enabled:
                logger.warning(f"🔒 Account disabled: {username}")
                return None

            # Verify password
            if not verify_password_bcrypt(password, user.password_hash):
                # Increment failed login attempts
                await self._handle_failed_login(user)
                logger.warning(f"🔒 Authentication failed: Invalid password for '{username}'")
                return None

            # Reset failed login attempts on successful login
            await self._handle_successful_login(user)

            logger.info(f"✅ User authenticated: {username}")

            # Check if password change is required
            password_change_required = False
            if user.user_metadata and isinstance(user.user_metadata, dict):
                password_change_required = user.user_metadata.get("password_change_required", False)

            return {
                "user_id": user.id,
                "username": user.username,
                "email": user.email,
                "role": user.role.value,
                "level": user.level.value,
                "enabled": user.enabled,
                "permissions": user.permissions,
                "last_login": user.last_login.isoformat() if user.last_login else None,
                "password_change_required": password_change_required,
            }

        except Exception as e:
            logger.error(f"❌ Authentication error for '{username}': {e}", exc_info=True)
            return None

    async def _handle_failed_login(self, user: User):
        """Handle failed login attempt"""
        try:
            login_attempts = user.login_attempts + 1
            update_data: Dict[str, Any] = {"login_attempts": login_attempts}

            # Lock account if max attempts exceeded
            if login_attempts >= self.max_login_attempts:
                locked_until = datetime.now() + self.lockout_duration
                update_data["locked_until"] = locked_until
                logger.warning(f"🔒 Account locked due to failed login attempts: {user.username}")

            await self.user_manipulator.update(update_data, {"username": user.username})

        except Exception as e:
            logger.error(f"❌ Error handling failed login: {e}")

    async def _handle_successful_login(self, user: User):
        """Handle successful login"""
        try:
            await self.user_manipulator.update(
                {"login_attempts": 0, "locked_until": None, "last_login": datetime.now()},
                {"username": user.username},
            )
        except Exception as e:
            logger.error(f"❌ Error handling successful login: {e}")

    # =============================================================================
    # User CRUD Operations
    # =============================================================================

    # @remote_service()
    async def create_user(self, request: Any, response: Any) -> None:
        """
        Create new user (ROS2 service interface)

        Request fields:
            - username: str
            - password: str
            - email: str (optional)
            - role: str (admin, operator, viewer, custom)
            - level: int (0-4)
            - permissions: dict (optional)
        """
        result = await self.create_user_impl(
            username=request.username,
            password=request.password,
            email=getattr(request, "email", None),
            role=getattr(request, "role", "viewer"),
            level=getattr(request, "level", 3),
            permissions=getattr(request, "permissions", {}),
        )

        response.success = result["success"]
        response.message = result["message"]
        if "user_id" in result:
            response.user_id = result["user_id"]

    async def create_user_impl(
        self,
        username: str,
        password: str,
        email: Optional[str] = None,
        role: str = "viewer",
        level: int = 3,
        permissions: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """
        Create new user (internal implementation)

        Args:
            username: Username (unique)
            password: Plain text password
            email: Email address
            role: User role (admin, operator, viewer, custom)
            level: Access level (0-4)
            permissions: Custom permissions dict

        Returns:
            Dict with success, message, and user_id
        """
        try:
            # Validate password
            if len(password) < self.password_min_length:
                return {
                    "success": False,
                    "message": f"Password must be at least {self.password_min_length} characters",
                }

            # Check if user already exists
            result: DBReturnValue = await self.user_manipulator.get_all(
                filters={"username": username}
            )

            if result.status == DBSTATUS.SUCCESS and result.value:
                return {"success": False, "message": f"User '{username}' already exists"}

            # Parse role and level
            try:
                user_role = UserRole(role)
            except ValueError:
                user_role = UserRole.VIEWER

            try:
                user_level = UserLevel(level)
            except ValueError:
                user_level = UserLevel.LEVEL_3

            # Create user
            user_data = {
                "username": username,
                "password_hash": self._hash_password(password),
                "email": email,
                "role": user_role,
                "level": user_level,
                "enabled": True,
                "permissions": permissions or {},
                "login_attempts": 0,
            }

            result = await self.user_manipulator.add(user_data)

            if result.status == DBSTATUS.SUCCESS:
                # Extract user_id from various possible result formats
                user_id = None
                if isinstance(result.value, list) and result.value:
                    user_id = result.value[0].id
                elif result.value is not None and hasattr(result.value, "id"):
                    user_id = result.value.id
                elif isinstance(result.details, dict):
                    user_id = result.details.get("id") or result.details.get("data", {}).get("id")
                logger.info(f"✅ User created: {username} (ID: {user_id})")
                return {
                    "success": True,
                    "message": f"User '{username}' created successfully",
                    "user_id": user_id,
                }
            else:
                return {"success": False, "message": f"Failed to create user: {result.details}"}

        except Exception as e:
            logger.error(f"❌ Error creating user '{username}': {e}", exc_info=True)
            return {"success": False, "message": f"Internal error: {str(e)}"}

    @remote_service()
    async def list_users(self, request: Any, response: Any) -> None:
        """List all users (ROS2 service interface)"""
        result = await self.list_users_impl()

        response.success = result["success"]
        response.message = result["message"]
        response.users = str(result.get("users", []))  # Convert to string for ROS2

    async def list_users_impl(self) -> Dict[str, Any]:
        """
        List all users (internal implementation)

        Returns:
            Dict with success, message, and users list
        """
        try:
            result: DBReturnValue = await self.user_manipulator.get_all()

            if result.status == DBSTATUS.SUCCESS and isinstance(result.value, list):
                users = [
                    {
                        "user_id": user.id,
                        "username": user.username,
                        "email": user.email,
                        "role": user.role.value,
                        "level": user.level.value,
                        "enabled": user.enabled,
                        "lock_edit": getattr(user, "lock_edit", False),
                        "created_at": user.created_at.isoformat(),
                        "last_login": user.last_login.isoformat() if user.last_login else None,
                    }
                    for user in result.value
                ]

                return {"success": True, "message": f"Retrieved {len(users)} users", "users": users}
            else:
                return {
                    "success": False,
                    "message": f"Failed to retrieve users: {result.details}",
                    "users": [],
                }

        except Exception as e:
            logger.error(f"❌ Error listing users: {e}", exc_info=True)
            return {"success": False, "message": f"Internal error: {str(e)}", "users": []}

    async def change_password_impl(
        self, username: str, old_password: str, new_password: str
    ) -> Dict[str, Any]:
        """
        Change user password

        Args:
            username: Username
            old_password: Current password
            new_password: New password

        Returns:
            Dict with success and message
        """
        try:
            # Authenticate with old password
            user_info = await self.authenticate(username, old_password)
            if not user_info:
                return {"success": False, "message": "Invalid current password"}

            # Validate new password
            if len(new_password) < self.password_min_length:
                return {
                    "success": False,
                    "message": f"Password must be at least {self.password_min_length} characters",
                }

            # Update password
            update_data = {
                "password_hash": self._hash_password(new_password),
                "last_password_change": datetime.now(),
            }

            # Clear password_change_required flag if it was set
            result_user = await self.user_manipulator.get_all(filters={"username": username})
            if (
                result_user.status == DBSTATUS.SUCCESS
                and isinstance(result_user.value, list)
                and result_user.value
            ):
                user = result_user.value[0]
                if (
                    user.user_metadata
                    and isinstance(user.user_metadata, dict)
                    and user.user_metadata.get("password_change_required")
                ):
                    updated_metadata = user.user_metadata.copy()
                    updated_metadata["password_change_required"] = False
                    updated_metadata["user_metadata"] = updated_metadata
                    logger.info(f"✅ Password change requirement cleared for user: {username}")

            result = await self.user_manipulator.update(update_data, {"username": username})

            if result.status == DBSTATUS.SUCCESS:
                logger.info(f"✅ Password changed for user: {username}")
                return {"success": True, "message": "Password changed successfully"}
            else:
                return {"success": False, "message": f"Failed to change password: {result.details}"}

        except Exception as e:
            logger.error(f"❌ Error changing password for '{username}': {e}", exc_info=True)
            return {"success": False, "message": f"Internal error: {str(e)}"}

    async def get_user_impl(self, username: str):
        """
        Get user information by username.

        Args:
            username: Username to retrieve

        Returns:
            User object or None if not found
        """
        try:
            result = await self.user_manipulator.get_all(filters={"username": username})

            if result.status == DBSTATUS.SUCCESS and isinstance(result.value, list):
                return result.value[0]

            return None

        except Exception as e:
            logger.error(f"❌ Error getting user '{username}': {e}", exc_info=True)
            return None

    async def update_user_impl(self, username: str, **kwargs):
        """
        Update user information.

        Args:
            username: Username to update
            **kwargs: Fields to update (role, level, permissions, etc.)

        Returns:
            Updated User object or None if failed
        """
        try:
            # Filter out None values
            update_data = {k: v for k, v in kwargs.items() if v is not None}

            if not update_data:
                logger.warning(f"⚠️  No update data provided for user '{username}'")
                return None

            result = await self.user_manipulator.update(update_data, {"username": username})

            if result.status == DBSTATUS.SUCCESS:
                logger.info(f"✅ User '{username}' updated successfully")
                # Return updated user
                return await self.get_user_impl(username)
            else:
                logger.error(f"❌ Failed to update user '{username}': {result.details}")
                return None

        except Exception as e:
            logger.error(f"❌ Error updating user '{username}': {e}", exc_info=True)
            return None

    async def delete_user_impl(self, username: str) -> bool:
        """
        Delete user by username.

        Args:
            username: Username to delete

        Returns:
            bool: True if deleted successfully, False otherwise
        """
        try:
            # Prevent deleting the last admin user
            result = await self.user_manipulator.get_all(filters={"role": UserRole.ADMIN})
            if (
                result.status == DBSTATUS.SUCCESS
                and isinstance(result.value, list)
                and len(result.value) <= 1
            ):
                user = await self.get_user_impl(username)
                if user and user.role == UserRole.ADMIN:
                    logger.warning(f"⚠️  Cannot delete last admin user: {username}")
                    return False

            # Prevent deleting a locked user
            target = await self.get_user_impl(username)
            if target and getattr(target, "lock_edit", False):
                logger.warning(f"⚠️  Cannot delete locked user: {username}")
                return False

            result = await self.user_manipulator.delete({"username": username})

            if result.status == DBSTATUS.SUCCESS:
                logger.info(f"✅ User '{username}' deleted successfully")
                return True
            else:
                logger.error(f"❌ Failed to delete user '{username}': {result.details}")
                return False

        except Exception as e:
            logger.error(f"❌ Error deleting user '{username}': {e}", exc_info=True)
            return False

    async def set_user_enabled_impl(self, username: str, enabled: bool) -> bool:
        """
        Enable or disable user account.

        Args:
            username: Username to enable/disable
            enabled: True to enable, False to disable

        Returns:
            bool: True if updated successfully, False otherwise
        """
        try:
            # Prevent disabling the last admin user
            if not enabled:
                result = await self.user_manipulator.get_all(
                    filters={"role": UserRole.ADMIN, "enabled": True}
                )
                if (
                    result.status == DBSTATUS.SUCCESS
                    and isinstance(result.value, list)
                    and len(result.value) <= 1
                ):
                    user = await self.get_user_impl(username)
                    if user and user.role == UserRole.ADMIN:
                        logger.warning(f"⚠️  Cannot disable last enabled admin user: {username}")
                        return False

            result = await self.user_manipulator.update(
                {"enabled": enabled}, {"username": username}
            )

            if result.status == DBSTATUS.SUCCESS:
                status = "enabled" if enabled else "disabled"
                logger.info(f"✅ User '{username}' {status} successfully")
                return True
            else:
                logger.error(f"❌ Failed to update user status: {result.details}")
                return False

        except Exception as e:
            logger.error(f"❌ Error setting user enabled status: {e}", exc_info=True)
            return False

    # =============================================================================
    # Helper Methods
    # =============================================================================

    def _hash_password(self, password: str) -> str:
        """Hash password using bcrypt."""
        return hash_password_bcrypt(password)
