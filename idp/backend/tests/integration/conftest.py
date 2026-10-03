from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI

from idp.application.auth import principal_from_user
from idp.application.users import NewUser, TenantBootstrapService, UserService
from idp.config import Settings
from idp.container import Container
from idp.domain.identity import Role
from idp.infrastructure.db.repositories import UserRepository
from idp.main import create_app

OWNER_PASSWORD = "Owner-Password-123!"
USER_PASSWORD = "Member-Password-456!"


@dataclass
class TenantFixture:
    slug: str
    owner_email: str
    owner_password: str = OWNER_PASSWORD


@pytest.fixture
async def app(container: Container, settings: Settings) -> FastAPI:
    application = create_app(settings)
    # Reuse the fixture's container (already truncated + started).
    await application.state.container.close()
    application.state.container = container
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def bootstrap_tenant(container: Container, slug: str) -> TenantFixture:
    email = f"owner@{slug}.test"
    async with container.session_factory() as session:
        await TenantBootstrapService(session).bootstrap(
            tenant_slug=slug,
            tenant_name=slug.title(),
            owner_email=email,
            owner_name="Owner",
            password=OWNER_PASSWORD,
        )
    return TenantFixture(slug=slug, owner_email=email)


async def add_user(container: Container, owner_email: str, email: str, role: Role) -> None:
    async with container.session_factory() as session:
        owner = await UserRepository(session).find_for_login(owner_email)
        assert owner is not None
        await UserService(session).create_user(
            principal_from_user(owner),
            NewUser(email=email, full_name=email, role=role, password=USER_PASSWORD),
        )


@pytest.fixture
async def acme(container: Container) -> TenantFixture:
    return await bootstrap_tenant(container, "acme")


async def login(client: httpx.AsyncClient, email: str, password: str) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
