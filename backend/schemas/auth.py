"""Authentication request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from backend.core.constants import Department, Role
from backend.security.hash import validate_password_strength


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    email: EmailStr
    password: str = Field(min_length=10, max_length=72)
    full_name: str | None = Field(default=None, max_length=200)
    department: str
    role: str = Role.USER.value

    @field_validator("username")
    @classmethod
    def _clean_username(cls, v: str) -> str:
        v = v.strip().lower()
        if not v.replace(".", "").replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                "Username may contain only letters, digits, dot, underscore and hyphen."
            )
        return v

    @field_validator("department")
    @classmethod
    def _valid_department(cls, v: str) -> str:
        if not Department.is_valid(v):
            raise ValueError(
                f"Department must be one of: {', '.join(Department.values())}"
            )
        return v

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: str) -> str:
        if v not in Role.values():
            raise ValueError(f"Role must be one of: {', '.join(Role.values())}")
        return v

    @field_validator("password")
    @classmethod
    def _strong_password(cls, v: str) -> str:
        problems = validate_password_strength(v)
        if problems:
            raise ValueError(" ".join(problems))
        return v


class UserLogin(BaseModel):
    """Login requires the department as a third field.

    Not decoration: it makes the user state which data domain they intend to
    work in, and a mismatch against their account is recorded as a failed
    attempt. Someone who phishes a credential also has to know the department.
    """

    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=72)
    department: str

    @field_validator("username")
    @classmethod
    def _clean_username(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("department")
    @classmethod
    def _valid_department(cls, v: str) -> str:
        if not Department.is_valid(v):
            raise ValueError(
                f"Department must be one of: {', '.join(Department.values())}"
            )
        return v


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    full_name: str | None = None
    department: str
    role: str
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")
    user: UserResponse


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=72)

    @field_validator("new_password")
    @classmethod
    def _strong_password(cls, v: str) -> str:
        problems = validate_password_strength(v)
        if problems:
            raise ValueError(" ".join(problems))
        return v


class DepartmentInfo(BaseModel):
    """Populates the department picker on the login screen."""

    name: str
    description: str
