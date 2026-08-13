"""Token minting and verification.

Dev login is the cut; carrying identity in a verified signed token is not.
Swapping in a real IdP means replacing verify_token() - nothing downstream
reads identity from anywhere else.

The dev user list is fixed. Minting tokens for arbitrary strings would let
anyone assume any tenant and make every isolation test meaningless.
"""
from __future__ import annotations

import time

import jwt

from core.config import JWT_ALGORITHM, JWT_SECRET, JWT_TTL_SEC

# The allowlist that stops dev login being an impersonation endpoint.
DEV_USERS: dict[str, str] = {
    "alice": "alice@example.com",
    "bob": "bob@example.com",
}


class AuthError(Exception):
    """Raised when a token is absent, malformed, expired, or not ours."""


def mint_token(user_id: str) -> tuple[str, int]:
    """Sign a token for a known dev user. Returns (token, expires_at)."""
    if user_id not in DEV_USERS:
        raise AuthError(f"unknown dev user: {user_id!r}")
    expires_at = int(time.time()) + JWT_TTL_SEC
    token = jwt.encode(
        {"sub": user_id, "iat": int(time.time()), "exp": expires_at},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    return token, expires_at


def verify_token(token: str) -> str:
    """Verify a token and return the user_id it asserts.

    The algorithm is pinned rather than read from the token header - accepting
    the header's choice is the classic JWT confusion attack.
    """
    try:
        claims = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise AuthError("token expired") from e
    except jwt.InvalidTokenError as e:
        raise AuthError(f"invalid token: {e}") from e

    user_id = claims.get("sub")
    if not isinstance(user_id, str) or user_id not in DEV_USERS:
        # A valid signature for an unknown user is still a failure, so
        # shrinking DEV_USERS revokes old tokens.
        raise AuthError("token subject is not a known user")
    return user_id
