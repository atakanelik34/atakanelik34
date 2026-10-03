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


async def get_principal(
    auth: AuthServiceDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Authentication required")
    return await auth.authenticate(credentials.credentials)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def require(permission: Permission) -> Callable[[Principal], Awaitable[Principal]]:
    """Route guard: `principal: Annotated[Principal, Depends(require(Permission.X))]`."""

    async def _guard(principal: PrincipalDep) -> Principal:
        if not principal.has(permission):
            raise AuthorizationError(f"Missing permission '{permission.value}'")
        return principal

    return _guard
