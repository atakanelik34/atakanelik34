from __future__ import annotations

from fastapi import APIRouter

from idp.api.deps import AuthServiceDep, PrincipalDep, SessionDep
from idp.api.schemas.identity import LoginRequest, MeResponse, TenantOut, TokenResponse, UserOut
from idp.context import current_client_ip
from idp.domain.errors import AuthenticationError
from idp.infrastructure.db.repositories import TenantRepository, UserRepository

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, auth: AuthServiceDep) -> TokenResponse:
    result = await auth.login(
        email=body.email, password=body.password, client_ip=current_client_ip()
    )
    return TokenResponse(access_token=result.token.token, expires_at=result.token.expires_at)


@router.get("/me", response_model=MeResponse)
async def me(principal: PrincipalDep, session: SessionDep) -> MeResponse:
    user = await UserRepository(session).get(
        tenant_id=principal.tenant_id, user_id=principal.user_id
    )
    tenant = await TenantRepository(session).get_live(principal.tenant_id)
    if user is None or tenant is None:
        raise AuthenticationError("Invalid access token")
    return MeResponse(
        user=UserOut.model_validate(user),
        tenant=TenantOut.model_validate(tenant),
        permissions=sorted(principal.permissions),
    )
