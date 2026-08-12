"""Token minting and verification.

Dev login is a deliberate cut - no Clerk, no Ory. What is *not* cut is the
property those services would provide: identity is carried in a signed token
and is verified server-side before it is trusted. Swapping in a real IdP later
means replacing verify_token() with their verifier; nothing downstream reads
identity from anywhere else, so nothing downstream changes.

The two dev users are fixed. A login endpoint that mints a token for an
arbitrary caller-supplied string would let anyone assume any tenant, which
would make every isolation test in this repo meaningless.
"""
from __future__ import annotations

import time

import jwt

from core.config import JWT_ALGORITHM, JWT_SECRET, JWT_TTL_SEC

# The demo tenants. Hardcoded on purpose: this is the allowlist that stops dev
# login from being an impersonation endpoint.
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

    The algorithm is pinned to a single value rather than taken from the
    token's own header. Accepting the header's choice is the classic JWT
    confusion attack: a token claiming alg=none, or an RS256 key reused as an
    HS256 secret, would otherwise verify.
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
        # A validly signed token for a user we do not recognise is still a
        # failure. If DEV_USERS shrinks, old tokens stop working - which is the
        # behaviour you want from a revocation list.
        raise AuthError("token subject is not a known user")
    return user_id
