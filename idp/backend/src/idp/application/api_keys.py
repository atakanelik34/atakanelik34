"""API keys for machine ingestion.

Token format: `idp_<prefix>_<secret>` — the prefix (8 hex) identifies the key,
the secret (32 random bytes, url-safe) is shown once and stored only as a
SHA-256 digest (high-entropy secrets need no slow hash). A key's permissions
are its scopes intersected with its creator's current role; deactivating the
creator or revoking the key stops it immediately.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idp.application.audit import AuditAction, AuditEntity, record_audit
from idp.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from idp.domain.identity import API_KEY_SCOPES, Permission, Principal
from idp.infrastructure.db.models import ApiKey
from idp.infrastructure.db.repositories import TenantRepository, UserRepository

TOKEN_PREFIX = "idp_"  # noqa: S105 — a public marker, not a secret
LAST_USED_RESOLUTION = timedelta(minutes=5)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def is_api_key(token: str) -> bool:
    return token.startswith(TOKEN_PREFIX)


class ApiKeyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _require(principal: Principal) -> None:
        if principal.api_key_id is not None or not principal.has(Permission.USERS_WRITE):
            raise AuthorizationError("Missing permission 'users:write'")

    async def list_keys(self, principal: Principal) -> Sequence[ApiKey]:
        self._require(principal)
        return (
            await self._session.scalars(
                select(ApiKey)
                .where(ApiKey.tenant_id == principal.tenant_id)
                .order_by(ApiKey.created_at.desc())
            )
        ).all()

    async def create(
        self,
        principal: Principal,
        *,
        name: str,
        scopes: list[str],
        expires_in_days: int | None,
    ) -> tuple[ApiKey, str]:
        self._require(principal)
        try:
            requested = frozenset(Permission(s) for s in scopes)
        except ValueError as exc:
            raise ValidationError("Unknown scope") from exc
        if not requested or not requested <= API_KEY_SCOPES:
            raise ValidationError(
                "API keys may only carry: " + ", ".join(sorted(p.value for p in API_KEY_SCOPES))
            )
        prefix = secrets.token_hex(4)
        secret = secrets.token_urlsafe(32)
        key = ApiKey(
            tenant_id=principal.tenant_id,
            name=name,
            prefix=prefix,
            secret_hash=_digest(secret),
            scopes=sorted(p.value for p in requested),
            created_by_id=principal.user_id,
            expires_at=datetime.now(UTC) + timedelta(days=expires_in_days)
            if expires_in_days
            else None,
        )
        self._session.add(key)
        await self._session.flush()
        record_audit(
            self._session,
            action=AuditAction.API_KEY_CREATED,
            entity_type=AuditEntity.API_KEY,
            entity_id=key.id,
            tenant_id=principal.tenant_id,
            actor=principal,
            after={"name": name, "prefix": prefix, "scopes": key.scopes},
        )
        await self._session.commit()
        return key, f"{TOKEN_PREFIX}{prefix}_{secret}"

    async def revoke(self, principal: Principal, key_id: uuid.UUID) -> None:
        self._require(principal)
        key = await self._session.get(ApiKey, key_id)
        if key is None or key.tenant_id != principal.tenant_id:
            raise NotFoundError("API key not found")
        if key.revoked_at is None:
            key.revoked_at = datetime.now(UTC)
            record_audit(
                self._session,
                action=AuditAction.API_KEY_REVOKED,
                entity_type=AuditEntity.API_KEY,
                entity_id=key.id,
                tenant_id=principal.tenant_id,
                actor=principal,
                before={"prefix": key.prefix},
            )
            await self._session.commit()

    async def authenticate(self, token: str) -> Principal:
        invalid = AuthenticationError("Invalid API key")
        try:
            prefix, secret = token[len(TOKEN_PREFIX) :].split("_", 1)
        except ValueError as exc:
            raise invalid from exc
        key = await self._session.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
        if key is None or not hmac.compare_digest(key.secret_hash, _digest(secret)):
            raise invalid
        now = datetime.now(UTC)
        if key.revoked_at is not None or (key.expires_at is not None and key.expires_at <= now):
            raise invalid
        user = await UserRepository(self._session).get(
            tenant_id=key.tenant_id, user_id=key.created_by_id
        )
        tenant = await TenantRepository(self._session).get_live(key.tenant_id)
        if user is None or not user.is_active or tenant is None or not tenant.is_active:
            raise invalid
        if key.last_used_at is None or now - key.last_used_at > LAST_USED_RESOLUTION:
            key.last_used_at = now
            await self._session.commit()
        return Principal(
            user_id=user.id,
            tenant_id=key.tenant_id,
            email=user.email,
            role=user.role,
            api_key_id=key.id,
            scopes=frozenset(Permission(s) for s in key.scopes),
        )
