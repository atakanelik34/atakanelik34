from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from idp.api.deps import SessionDep, require
from idp.api.schemas.identity import CreateUserRequest, UserOut
from idp.application.users import NewUser, UserService
from idp.domain.identity import Permission, Principal

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
async def list_users(
    principal: Annotated[Principal, Depends(require(Permission.USERS_READ))],
    session: SessionDep,
) -> list[UserOut]:
    users = await UserService(session).list_users(principal)
    return [UserOut.model_validate(u) for u in users]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    principal: Annotated[Principal, Depends(require(Permission.USERS_WRITE))],
    session: SessionDep,
) -> UserOut:
    user = await UserService(session).create_user(
        principal,
        NewUser(email=body.email, full_name=body.full_name, role=body.role, password=body.password),
    )
    return UserOut.model_validate(user)
