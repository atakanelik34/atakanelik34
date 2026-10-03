import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from idp.container import Container
from idp.domain.identity import Role
from idp.infrastructure.db.models import AuditLog, User
from tests.integration.conftest import (
    USER_PASSWORD,
    TenantFixture,
    add_user,
    bootstrap_tenant,
    login,
)


async def test_login_and_me(client: httpx.AsyncClient, acme: TenantFixture) -> None:
    headers = await login(client, acme.owner_email, acme.owner_password)
    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == acme.owner_email
    assert body["user"]["role"] == "owner"
    assert body["tenant"]["slug"] == "acme"
    assert "tenant:manage" in body["permissions"]
    assert "password_hash" not in body["user"]


async def test_login_is_case_insensitive_on_email(
    client: httpx.AsyncClient, acme: TenantFixture
) -> None:
    await login(client, acme.owner_email.upper(), acme.owner_password)


@pytest.mark.parametrize("email", ["owner@acme.test", "nobody@acme.test"])
async def test_bad_credentials_give_identical_generic_error(
    client: httpx.AsyncClient, acme: TenantFixture, email: str
) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "wrong-password-xyz"}
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["error_category"] == "AUTHENTICATION_ERROR"
    assert body["detail"] == "Invalid email or password"


async def test_login_attempts_are_audited(
    client: httpx.AsyncClient, container: Container, acme: TenantFixture
) -> None:
    await client.post(
        "/api/v1/auth/login", json={"email": acme.owner_email, "password": "wrong-password-xyz"}
    )
    await login(client, acme.owner_email, acme.owner_password)
    async with container.session_factory() as session:
        rows = (await session.scalars(select(AuditLog).order_by(AuditLog.occurred_at))).all()
    actions = [r.action for r in rows]
    assert "auth.login_failed" in actions
    assert "auth.login_succeeded" in actions
    success = next(r for r in rows if r.action == "auth.login_succeeded")
    assert success.correlation_id
    assert str(success.ip_address) == "203.0.113.10"


async def test_login_is_rate_limited(client: httpx.AsyncClient, acme: TenantFixture) -> None:
    payload = {"email": acme.owner_email, "password": "wrong-password-xyz"}
    statuses = [
        (await client.post("/api/v1/auth/login", json=payload)).status_code for _ in range(6)
    ]
    assert statuses[:5] == [401] * 5
    assert statuses[5] == 429
    limited = await client.post("/api/v1/auth/login", json=payload)
    assert limited.json()["error_category"] == "RATE_LIMITED"
    assert int(limited.headers["Retry-After"]) > 0


async def test_missing_and_garbage_tokens_are_rejected(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    response = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_token_revoked_by_version_bump_and_deactivation(
    client: httpx.AsyncClient, container: Container, acme: TenantFixture
) -> None:
    headers = await login(client, acme.owner_email, acme.owner_password)
    async with container.session_factory() as session:
        await session.execute(
            update(User).where(User.email == acme.owner_email).values(token_version=1)
        )
        await session.commit()
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401

    headers = await login(client, acme.owner_email, acme.owner_password)
    async with container.session_factory() as session:
        await session.execute(
            update(User).where(User.email == acme.owner_email).values(is_active=False)
        )
        await session.commit()
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401


async def test_validation_errors_never_echo_submitted_values(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": "not-an-email", "password": "s3cret-value"}
    )
    assert response.status_code == 422
    assert "s3cret-value" not in response.text
    assert response.json()["error_category"] == "VALIDATION_ERROR"


async def test_users_rbac_and_creation(
    client: httpx.AsyncClient, container: Container, acme: TenantFixture
) -> None:
    owner = await login(client, acme.owner_email, acme.owner_password)
    created = await client.post(
        "/api/v1/users",
        headers=owner,
        json={
            "email": "Reviewer@Acme.test",
            "full_name": "Rita Reviewer",
            "role": "reviewer",
            "password": USER_PASSWORD,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["email"] == "reviewer@acme.test"

    duplicate = await client.post(
        "/api/v1/users",
        headers=owner,
        json={
            "email": "reviewer@acme.test",
            "full_name": "Dup",
            "role": "viewer",
            "password": USER_PASSWORD,
        },
    )
    assert duplicate.status_code == 409

    reviewer = await login(client, "reviewer@acme.test", USER_PASSWORD)
    assert (await client.get("/api/v1/users", headers=reviewer)).status_code == 403
    forbidden = await client.get("/api/v1/system/status", headers=reviewer)
    assert forbidden.status_code == 403
    assert forbidden.json()["error_category"] == "AUTHORIZATION_ERROR"

    listed = await client.get("/api/v1/users", headers=owner)
    assert {u["email"] for u in listed.json()} == {acme.owner_email, "reviewer@acme.test"}


async def test_admin_cannot_escalate_to_owner(
    client: httpx.AsyncClient, container: Container, acme: TenantFixture
) -> None:
    await add_user(container, acme.owner_email, "admin@acme.test", Role.ADMIN)
    admin = await login(client, "admin@acme.test", USER_PASSWORD)
    response = await client.post(
        "/api/v1/users",
        headers=admin,
        json={
            "email": "sneaky@acme.test",
            "full_name": "Sneaky",
            "role": "owner",
            "password": USER_PASSWORD,
        },
    )
    assert response.status_code == 403


async def test_weak_password_is_refused(client: httpx.AsyncClient, acme: TenantFixture) -> None:
    owner = await login(client, acme.owner_email, acme.owner_password)
    response = await client.post(
        "/api/v1/users",
        headers=owner,
        json={
            "email": "w@acme.test",
            "full_name": "W",
            "role": "viewer",
            "password": "aaaaaaaaaaaa",
        },
    )
    assert response.status_code == 422


async def test_tenants_are_isolated(
    client: httpx.AsyncClient, container: Container, acme: TenantFixture
) -> None:
    globex = await bootstrap_tenant(container, "globex")
    acme_owner = await login(client, acme.owner_email, acme.owner_password)
    globex_owner = await login(client, globex.owner_email, globex.owner_password)

    acme_users = (await client.get("/api/v1/users", headers=acme_owner)).json()
    globex_users = (await client.get("/api/v1/users", headers=globex_owner)).json()
    assert {u["email"] for u in acme_users} == {acme.owner_email}
    assert {u["email"] for u in globex_users} == {globex.owner_email}


async def test_audit_log_is_append_only(container: Container, acme: TenantFixture) -> None:
    async with container.session_factory() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(text("UPDATE audit_logs SET action = 'tampered'"))
        await session.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(text("DELETE FROM audit_logs"))
