"""Authentication use cases: login and token → principal resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from idp.application.audit import AuditAction, AuditEntity, record_audit
from idp.domain.errors import AuthenticationError
from idp.domain.identity import Principal
from idp.infrastructure.db.models import User
from idp.infrastructure.db.repositories import TenantRepository, UserRepository
from idp.infrastructure.logging import get_logger
from idp.infrastructure.security.passwords import hash_password, needs_rehash, verify_password
from idp.infrastructure.security.rate_limit import RedisRateLimiter
from idp.infrastructure.security.tokens import IssuedToken, TokenService

log = get_logger(__name__)

_INVALID_CREDENTIALS = "Invalid email or password"


@dataclass(frozen=True, slots=True)
class LoginResult:
    token: IssuedToken
    principal: Principal


def principal_from_user(user: User) -> Principal:
    return Principal(user_id=user.id, tenant_id=user.tenant_id, email=user.email, role=user.role)


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        tokens: TokenService,
        login_limiter: RedisRateLimiter,
    ) -> None:
        self._session = session
        self._tokens = tokens
        self._limiter = login_limiter
        self._users = UserRepository(session)
        self._tenants = TenantRepository(session)

    async def login(self, *, email: str, password: str, client_ip: str | None) -> LoginResult:
        rate_identity = f"{(client_ip or 'unknown')}|{email.lower()}"
        await self._limiter.hit(rate_identity)

        user = await self._users.find_for_login(email)
        password_ok = verify_password(user.password_hash if user else None, password)
        tenant = await self._tenants.get_live(user.tenant_id) if user else None

        if (
            user is None
            or not password_ok
            or not user.is_active
            or tenant is None
            or not tenant.is_active
        ):
            record_audit(
                self._session,
                action=AuditAction.AUTH_LOGIN_FAILED,
                entity_type=AuditEntity.USER,
                entity_id=user.id if user else None,
                tenant_id=user.tenant_id if user else None,
            )
            await self._session.commit()
            log.info("auth.login_failed", user_known=user is not None)
            raise AuthenticationError(_INVALID_CREDENTIALS)

        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        user.last_login_at = datetime.now(UTC)
        principal = principal_from_user(user)
        record_audit(
            self._session,
            action=AuditAction.AUTH_LOGIN_SUCCEEDED,
            entity_type=AuditEntity.USER,
            entity_id=user.id,
            tenant_id=user.tenant_id,
            actor=principal,
        )
        await self._session.commit()
        await self._limiter.reset(rate_identity)

        token = self._tokens.issue(
            user_id=user.id, tenant_id=user.tenant_id, token_version=user.token_version
        )
        log.info("auth.login_succeeded", user_id=str(user.id), tenant_id=str(user.tenant_id))
        return LoginResult(token=token, principal=principal)

    async def authenticate(self, token: str) -> Principal:
        claims = self._tokens.decode(token)
        user = await self._users.get(tenant_id=claims.tenant_id, user_id=claims.user_id)
        if user is None or not user.is_active or user.token_version != claims.token_version:
            raise AuthenticationError("Invalid access token")
        tenant = await self._tenants.get_live(user.tenant_id)
        if tenant is None or not tenant.is_active:
            raise AuthenticationError("Invalid access token")
        return principal_from_user(user)
