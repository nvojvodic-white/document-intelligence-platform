"""Which parts of a datasource a tenant may register.

Server-side and never client-supplied: a tenant asking for a wider scope than
they were provisioned is the thing this exists to refuse. A real deployment
reads it from the IdP or an admin API; here it comes from DATASOURCE_PREFIXES,
with `{user_id}` expanded per tenant.

An empty policy means unrestricted, which is what tests and single-tenant runs
use. Restricting browse alone would be theatre - register enforces it too, and
that is the gate that matters.
"""
from __future__ import annotations

import os

# Both demo tenants share library/ (which is what makes cross-user dedup worth
# showing) and hold one private prefix each.
DEFAULT_POLICY = "library/,library-archive/,{user_id}-private/"


def normalise(prefix: str) -> str:
    """No leading slash, always a trailing one.

    The trailing slash is what stops 'alice-priv' matching 'alice-private/' by
    string prefix.
    """
    prefix = (prefix or "").strip().lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return prefix


def allowed_prefixes(user_id: str) -> list[str]:
    """Prefixes this user may register. Empty list means no restriction."""
    raw = os.getenv("DATASOURCE_PREFIXES", DEFAULT_POLICY).strip()
    if raw in ("", "*"):
        return []
    return [
        normalise(p.replace("{user_id}", user_id))
        for p in raw.split(",")
        if p.strip()
    ]


def may_register(path: str, allowed: list[str]) -> bool:
    """True when `path` sits inside an allowed prefix.

    Being merely on the way to one is not enough: registering "" would pull in
    the whole bucket.
    """
    if not allowed:
        return True
    return any(normalise(path).startswith(a) for a in allowed)


def may_browse(path: str, allowed: list[str]) -> bool:
    """True when `path` is inside an allowed prefix or an ancestor of one.

    Ancestors have to be visible or there is no way to navigate down to the
    prefixes the user is allowed to register.
    """
    if not allowed:
        return True
    path = normalise(path)
    return any(path.startswith(a) or a.startswith(path) for a in allowed)
