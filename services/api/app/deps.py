"""Request-scoped dependencies.

current_user_id is the single place the API learns who is calling: it verifies
the bearer token and returns its subject. It never accepts a user id from a
body, query param, path segment, or header - a platform whose isolation depends
on callers naming themselves honestly has none.
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


# Reads as `user_id: str = CurrentUser` at the call site.
CurrentUser = Depends(current_user_id)
