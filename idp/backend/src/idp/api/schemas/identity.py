from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from idp.domain.identity import Permission, Role
from idp.infrastructure.security.passwords import MAX_PASSWORD_LENGTH

_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _lower(value: str) -> str:
    return value.lower()


# Syntactic check only: deliverability/TLD rules would reject internal enterprise
# domains such as `corp.local`, which on-prem deployments commonly use.
Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=320, pattern=_EMAIL_PATTERN),
    AfterValidator(_lower),
]


class LoginRequest(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 — OAuth2 token type, not a secret
    expires_at: datetime


class MeResponse(BaseModel):
    user: UserOut
    tenant: TenantOut
    permissions: list[Permission]


class CreateUserRequest(BaseModel):
    email: Email
    full_name: str = Field(min_length=1, max_length=200)
    role: Role
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
