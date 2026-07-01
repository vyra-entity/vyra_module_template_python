"""
Database model for User Management
"""
from ..logging_config import get_logger, log_exception, log_function_call, log_function_result
from enum import Enum
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, Integer, JSON, Enum as SQLEnum, func
from sqlalchemy.orm import Mapped, mapped_column

from vyra_base.storage.tb_base import Base

logger = get_logger(__name__)


class UserRole(Enum):
    """User roles for access control"""

    ADMIN = "admin"
    MANAGER = "manager"
    OPERATOR = "operator"
    VIEWER = "viewer"
    CUSTOM = "custom"


class UserLevel(Enum):
    """User access levels"""

    LEVEL_0 = 0  # Limited read-only
    LEVEL_1 = 1  # Read-only
    LEVEL_2 = 2  # Module operations
    LEVEL_3 = 3  # Module management
    LEVEL_4 = 4  # Full system access (admin)


class User(Base):
    """
    Database model for User Management.

    Stores user credentials, roles, permissions, and access levels.

    Attributes:
        id (int): Primary key auto-increment
        username (str): Unique username
        password_hash (str): Hashed password (bcrypt)
        display_name (str): Optional display name
        lock_edit (bool): Whether the account is locked for editing by admins
        email (str): Optional email address
        role (UserRole): User role (admin, operator, viewer, custom)
        level (UserLevel): Access level (0-4, higher = more access)
        enabled (bool): Whether user account is active
        permissions (dict): Custom permissions JSON
        created_at (datetime): Account creation timestamp
        last_login (datetime): Last successful login
        last_password_change (datetime): Last password change
        login_attempts (int): Failed login attempt counter
        locked_until (datetime): Account lock timestamp if locked
        user_metadata (dict): Additional user metadata
    """

    __tablename__ = "user"

    # Primary identification
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=True, default="")
    lock_edit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=True)

    # Role and access control
    role: Mapped[UserRole] = mapped_column(
        SQLEnum(UserRole), nullable=False, default=UserRole.VIEWER
    )
    level: Mapped[UserLevel] = mapped_column(
        SQLEnum(UserLevel), nullable=False, default=UserLevel.LEVEL_3
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Permissions
    permissions: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    last_password_change: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Security
    login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    # Additional user data
    user_metadata: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username}, role={self.role.value}, level={self.level.value})>"
