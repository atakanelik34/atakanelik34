"""JWT access tokens."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from idp.config import Settings
from idp.domain.errors import AuthenticationError

_REQUIRED_CLAIMS = ["exp", "iat", "iss", "sub", "tid", "ver", "jti"]


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    token_version: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    expires_at: datetime


class TokenService:
    def __init__(self, settings: Settings) -> None:
        self._secret = settings.jwt_secret.get_secret_value()
        self._algorithm = settings.jwt_algorithm
        self._issuer = settings.jwt_issuer
        self._ttl = timedelta(minutes=settings.access_token_ttl_minutes)

    def issue(self, *, user_id: uuid.UUID, tenant_id: uuid.UUID, token_version: int) -> IssuedToken:
        now = datetime.now(UTC)
        expires_at = now + self._ttl
        payload = {
            "iss": self._issuer,
            "sub": str(user_id),
            "tid": str(tenant_id),
            "ver": token_version,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": uuid.uuid4().hex,
        }
        token = jwt.encode(payload, self._secret, algorithm=self._algorithm)
        return IssuedToken(token=token, expires_at=expires_at)

    def decode(self, token: str) -> AccessTokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                options={"require": _REQUIRED_CLAIMS},
            )
            return AccessTokenClaims(
                user_id=uuid.UUID(payload["sub"]),
                tenant_id=uuid.UUID(payload["tid"]),
                token_version=int(payload["ver"]),
                expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Access token expired") from exc
        except (jwt.PyJWTError, ValueError, KeyError) as exc:
            raise AuthenticationError("Invalid access token") from exc
