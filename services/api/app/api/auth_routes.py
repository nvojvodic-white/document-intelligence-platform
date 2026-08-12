"""Dev login.

Mints a token for one of the two fixed demo users. This is the piece a real
deployment replaces with an identity provider; it is separated into its own
router so that replacement touches one file.
"""
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from core.auth import DEV_USERS, AuthError, mint_token
from core.repositories import ensure_user

from app.deps import CurrentUser

router = APIRouter()


class DevLoginRequest(BaseModel):
    user_id: str = Field(..., max_length=64)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: int
    user_id: str


@router.post("/dev-login", response_model=TokenResponse)
def dev_login(req: DevLoginRequest) -> TokenResponse:
    """Issue a signed token for a known demo user.

    The user_id in this request body is a *login* choice, not an identity
    assertion - it is checked against the fixed allowlist and can only produce
    a token for a user that already exists. Every other endpoint ignores body
    fields entirely and reads identity from the verified token.
    """
    try:
        token, expires_at = mint_token(req.user_id)
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e

    ensure_user(req.user_id, DEV_USERS[req.user_id])
    return TokenResponse(
        access_token=token, expires_at=expires_at, user_id=req.user_id
    )


@router.get("/me")
def me(user_id: str = CurrentUser) -> dict:
    """Echo the identity the server derived from the token. Useful in the
    walkthrough to show that switching users switches what is visible."""
    return {"user_id": user_id, "email": DEV_USERS[user_id]}


@router.get("/dev-users")
def dev_users() -> dict:
    """The demo tenants the UI offers as login buttons."""
    return {"users": [{"user_id": u, "email": e} for u, e in DEV_USERS.items()]}
