"""Request and response models for authentication."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def normalise_email(value: str) -> str:
    """Lower-cases and validates an email address. Raises ValueError so pydantic reports a 422."""
    email = value.strip().lower()
    if len(email) > 320 or not _EMAIL_RE.match(email):
        raise ValueError("Enter a valid email address.")
    return email


class SetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=320)
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(default="", max_length=120)

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        return normalise_email(value)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=320)
    password: str = Field(max_length=256)


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    created_at: datetime
    last_login_at: datetime | None = None


class AuthStatus(BaseModel):
    setup_required: bool
    authenticated: bool
    user: UserOut | None = None


class SessionOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    user_agent: str | None = None
    ip_address: str | None = None
    current: bool = False
