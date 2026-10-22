"""Request and response models for owner-managed member accounts."""

from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.auth import normalise_email
from app.security.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH

DISPLAY_NAME_MAX = 120


def _clean_display_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Enter a display name.")
    if any(unicodedata.category(char).startswith("C") for char in cleaned):
        raise ValueError("Display name contains characters that are not allowed.")
    return cleaned


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=320)
    display_name: str = Field(max_length=DISPLAY_NAME_MAX)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    # Only members can be created here. The owner is created once, at setup.
    role: Literal["member"]

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        return normalise_email(value)

    @field_validator("display_name")
    @classmethod
    def _check_display_name(cls, value: str) -> str:
        return _clean_display_name(value)


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool | None = None
    display_name: str | None = Field(default=None, max_length=DISPLAY_NAME_MAX)

    @field_validator("display_name")
    @classmethod
    def _check_display_name(cls, value: str | None) -> str | None:
        return None if value is None else _clean_display_name(value)


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class AdminUserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class UserListOut(BaseModel):
    items: list[AdminUserOut]
