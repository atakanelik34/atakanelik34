"""Identity, roles and permissions (RBAC).

Routes declare the permission they need; roles map to permission sets here and
nowhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


class Permission(StrEnum):
    DOCUMENTS_READ = "documents:read"
    DOCUMENTS_WRITE = "documents:write"
    REVIEWS_READ = "reviews:read"
    REVIEWS_WRITE = "reviews:write"
    CONFIG_READ = "config:read"
    CONFIG_WRITE = "config:write"
    USERS_READ = "users:read"
    USERS_WRITE = "users:write"
    AUDIT_READ = "audit:read"
    SYSTEM_READ = "system:read"
    # Approve or reject configured business actions (ERP posting, webhooks, email).
    ACTIONS_EXECUTE = "actions:execute"
    # Tenant-wide settings (e.g. data-residency processing policy, API keys). Owner only.
    TENANT_MANAGE = "tenant:manage"


_VIEWER = frozenset({Permission.DOCUMENTS_READ, Permission.REVIEWS_READ, Permission.CONFIG_READ})
_REVIEWER = _VIEWER | {Permission.REVIEWS_WRITE}
_OPERATOR = _REVIEWER | {
    Permission.DOCUMENTS_WRITE,
    Permission.SYSTEM_READ,
    Permission.ACTIONS_EXECUTE,
}
_ADMIN = _OPERATOR | {
    Permission.CONFIG_WRITE,
    Permission.USERS_READ,
    Permission.USERS_WRITE,
    Permission.AUDIT_READ,
}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: _VIEWER,
    Role.REVIEWER: frozenset(_REVIEWER),
    Role.OPERATOR: frozenset(_OPERATOR),
    Role.ADMIN: frozenset(_ADMIN),
    Role.OWNER: frozenset(Permission),
}

# Roles a given role may assign to other users (prevents privilege escalation).
ASSIGNABLE_ROLES: dict[Role, frozenset[Role]] = {
    Role.OWNER: frozenset(Role),
    Role.ADMIN: frozenset({Role.ADMIN, Role.OPERATOR, Role.REVIEWER, Role.VIEWER}),
    Role.OPERATOR: frozenset(),
    Role.REVIEWER: frozenset(),
    Role.VIEWER: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated actor of a request. Tenant scope comes only from here."""

    user_id: UUID
    tenant_id: UUID
    email: str
    role: Role
    # Set when authenticated with an API key: the key's id and scopes. A key's
    # permissions are its scopes intersected with its creator's role, so a key
    # can never do more than the person who made it.
    api_key_id: UUID | None = None
    scopes: frozenset[Permission] | None = None

    @property
    def permissions(self) -> frozenset[Permission]:
        role = ROLE_PERMISSIONS[self.role]
        return role if self.scopes is None else role & self.scopes

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions

    def can_assign(self, role: Role) -> bool:
        return role in ASSIGNABLE_ROLES[self.role]


# Scopes an API key may carry (machine ingestion and result retrieval).
API_KEY_SCOPES: frozenset[Permission] = frozenset(
    {Permission.DOCUMENTS_READ, Permission.DOCUMENTS_WRITE}
)
