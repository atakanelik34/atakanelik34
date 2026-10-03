"""FastAPI dependencies: resources, sessions, authentication and permission guards."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from idp.application.auth import AuthService
from idp.container import Container
from idp.domain.errors import AuthenticationError, AuthorizationError
from idp.domain.identity import Permission, Principal
from idp.infrastructure.db.tenancy import bind_tenant

_bearer = HTTPBearer(auto_error=False)


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


async def get_session(container: ContainerDep) -> AsyncIterator[AsyncSession]:
    async with container.session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_auth_service(container: ContainerDep, session: SessionDep) -> AuthService:
    return AuthService(session, tokens=container.tokens, login_limiter=container.login_limiter)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def rate_identity(principal: Principal) -> str:
    return f"key:{principal.api_key_id}" if principal.api_key_id else f"user:{principal.user_id}"


async def get_principal(
    auth: AuthServiceDep,
    container: ContainerDep,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Authentication required")
    principal = await auth.authenticate(credentials.credentials)
    await container.api_limiter.hit(rate_identity(principal))
    await bind_tenant(session, principal.tenant_id)
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def require(permission: Permission) -> Callable[[Principal], Awaitable[Principal]]:
    """Route guard: `principal: Annotated[Principal, Depends(require(Permission.X))]`."""

    async def _guard(principal: PrincipalDep) -> Principal:
        if not principal.has(permission):
            raise AuthorizationError(f"Missing permission '{permission.value}'")
        return principal

    return _guard
