"""User management and tenant bootstrap use cases."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idp.application.audit import ActorType, AuditAction, AuditEntity, record_audit
from idp.domain.errors import AuthorizationError, ConflictError, ValidationError
from idp.domain.identity import Permission, Principal, Role
from idp.infrastructure.db.models import Project, Tenant, User
from idp.infrastructure.db.repositories import TenantRepository, UserRepository
from idp.infrastructure.security.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
)

_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
DEFAULT_PROJECT_KEY = "default"


def validate_password_policy(password: str) -> None:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ValidationError(
            f"Password must be between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters"
        )
    if password.strip() != password or len(set(password)) < 6:
        raise ValidationError("Password is too weak")


def _user_snapshot(user: User) -> dict[str, str | bool]:
    # Never include password_hash in audit snapshots.
    return {
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value,
        "is_active": user.is_active,
    }


@dataclass(frozen=True, slots=True)
class NewUser:
    email: str
    full_name: str
    role: Role
    password: str


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)

    async def list_users(self, principal: Principal) -> Sequence[User]:
        if not principal.has(Permission.USERS_READ):
            raise AuthorizationError("Not allowed to list users")
        return await self._users.list(tenant_id=principal.tenant_id)

    async def create_user(self, principal: Principal, new: NewUser) -> User:
        if not principal.has(Permission.USERS_WRITE):
            raise AuthorizationError("Not allowed to create users")
        if not principal.can_assign(new.role):
            raise AuthorizationError(f"Not allowed to assign role '{new.role.value}'")
        validate_password_policy(new.password)
        if await self._users.email_taken(new.email):
            raise ConflictError("A user with this email already exists")

        user = User(
            tenant_id=principal.tenant_id,
            email=new.email.lower(),
            full_name=new.full_name.strip(),
            role=new.role,
            password_hash=hash_password(new.password),
        )
        self._session.add(user)
        try:
            await self._session.flush()
        except IntegrityError as exc:  # concurrent create with the same email
            await self._session.rollback()
            raise ConflictError("A user with this email already exists") from exc
        record_audit(
            self._session,
            action=AuditAction.USER_CREATED,
            entity_type=AuditEntity.USER,
            entity_id=user.id,
            tenant_id=principal.tenant_id,
            actor=principal,
            after=_user_snapshot(user),
        )
        await self._session.commit()
        return user


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    tenant: Tenant
    owner: User
    project: Project


class TenantBootstrapService:
    """Creates a tenant with its first owner and default project (CLI only)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tenants = TenantRepository(session)
        self._users = UserRepository(session)

    async def bootstrap(
        self,
        *,
        tenant_slug: str,
        tenant_name: str,
        owner_email: str,
        owner_name: str,
        password: str,
    ) -> BootstrapResult:
        if not _SLUG_PATTERN.fullmatch(tenant_slug):
            raise ValidationError("Tenant slug must be lowercase letters, digits and hyphens")
        validate_password_policy(password)
        if await self._tenants.get_by_slug(tenant_slug) is not None:
            raise ConflictError(f"Tenant '{tenant_slug}' already exists")
        if await self._users.email_taken(owner_email):
            raise ConflictError("A user with this email already exists")

        tenant = Tenant(slug=tenant_slug, name=tenant_name)
        self._session.add(tenant)
        await self._session.flush()
        owner = User(
            tenant_id=tenant.id,
            email=owner_email.lower(),
            full_name=owner_name,
            role=Role.OWNER,
            password_hash=hash_password(password),
        )
        self._session.add(owner)
        await self._session.flush()
        project = Project(
            tenant_id=tenant.id, key=DEFAULT_PROJECT_KEY, name="Default", created_by_id=owner.id
        )
        self._session.add(project)
        await self._session.flush()
        record_audit(
            self._session,
            action=AuditAction.TENANT_BOOTSTRAPPED,
            entity_type=AuditEntity.TENANT,
            entity_id=tenant.id,
            tenant_id=tenant.id,
            actor_type=ActorType.SYSTEM,
            after={"slug": tenant.slug, "owner_email": owner.email},
        )
        await self._session.commit()
        return BootstrapResult(tenant=tenant, owner=owner, project=project)
