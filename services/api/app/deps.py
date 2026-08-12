"""Request-scoped dependencies.

current_user_id is the single place the API learns who is calling. It reads the
Authorization header, verifies the signature, and returns the subject of the
verified token.

It does not accept a user id from a request body, a query parameter, a path
segment, or an X-User-Id style header, and no route should either. Those are
all attacker-controlled, and a platform whose isolation depends on the caller
naming themselves honestly has no isolation at all. Every handler that touches
tenant data takes this dependency and passes the result to a repository
function as the first argument.
"""
from fastapi import Depends, Header, HTTPException, status

from core.auth import AuthError, verify_token


def current_user_id(authorization: str = Header(default="")) -> str:
    """The verified caller's user_id, or 401."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return verify_token(token.strip())
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


# Alias that reads clearly at the call site: `user_id: str = CurrentUser`.
CurrentUser = Depends(current_user_id)
