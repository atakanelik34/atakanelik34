import uuid
from itertools import pairwise

import pytest

from idp.domain.identity import ROLE_PERMISSIONS, Permission, Principal, Role


def _principal(role: Role) -> Principal:
    return Principal(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), email="a@b.test", role=role)


def test_owner_has_every_permission() -> None:
    assert ROLE_PERMISSIONS[Role.OWNER] == frozenset(Permission)


def test_roles_are_strictly_nested() -> None:
    order = [Role.VIEWER, Role.REVIEWER, Role.OPERATOR, Role.ADMIN, Role.OWNER]
    for lower, higher in pairwise(order):
        assert ROLE_PERMISSIONS[lower] < ROLE_PERMISSIONS[higher]


@pytest.mark.parametrize(
    ("role", "permission", "allowed"),
    [
        (Role.VIEWER, Permission.DOCUMENTS_READ, True),
        (Role.VIEWER, Permission.DOCUMENTS_WRITE, False),
        (Role.REVIEWER, Permission.REVIEWS_WRITE, True),
        (Role.REVIEWER, Permission.SYSTEM_READ, False),
        (Role.OPERATOR, Permission.USERS_WRITE, False),
        (Role.ADMIN, Permission.AUDIT_READ, True),
    ],
)
def test_permission_matrix(role: Role, permission: Permission, allowed: bool) -> None:
    assert _principal(role).has(permission) is allowed


def test_admin_cannot_mint_owners_but_owner_can() -> None:
    assert not _principal(Role.ADMIN).can_assign(Role.OWNER)
    assert _principal(Role.ADMIN).can_assign(Role.REVIEWER)
    assert _principal(Role.OWNER).can_assign(Role.OWNER)
    assert not _principal(Role.OPERATOR).can_assign(Role.VIEWER)
