"""Datasource scope.

One demo bucket with prefixes named after their intended owner is a naming
convention, not a boundary - alice could browse into bob-private/ and register
it, and the documents were then legitimately hers. Correct, and misleading in a
system whose point is data safety.

Scope makes it structural. It comes from server-side policy, so a tenant cannot
widen it by asking.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from core import policy
from tests.conftest import BUCKET


@pytest.fixture()
def client(core_env, s3, monkeypatch):
    monkeypatch.setenv(
        "DATASOURCE_PREFIXES", "library/,library-archive/,{user_id}-private/"
    )
    import app.main

    importlib.reload(app.main)
    return TestClient(app.main.app)


def _auth(client, user_id: str) -> dict[str, str]:
    r = client.post("/api/v1/auth/dev-login", json={"user_id": user_id})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _connect(client, headers) -> str:
    r = client.post(
        "/api/v1/datasources", json={"name": "S3", "bucket": BUCKET}, headers=headers
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


# --- the policy itself ------------------------------------------------------


def test_policy_expands_the_user_into_the_prefix(monkeypatch):
    monkeypatch.setenv("DATASOURCE_PREFIXES", "library/,{user_id}-private/")
    assert policy.allowed_prefixes("alice") == ["library/", "alice-private/"]
    assert policy.allowed_prefixes("bob") == ["library/", "bob-private/"]


def test_an_empty_policy_means_unrestricted(monkeypatch):
    monkeypatch.setenv("DATASOURCE_PREFIXES", "")
    assert policy.allowed_prefixes("alice") == []
    assert policy.may_register("anything/", [])
    assert policy.may_browse("anything/", [])


def test_registering_needs_to_be_inside_a_prefix_not_merely_above_one():
    allowed = ["library/", "alice-private/"]
    assert policy.may_register("library/", allowed)
    assert policy.may_register("library/tolkien_gateway/", allowed)
    assert not policy.may_register("", allowed), "the bucket root is not in scope"
    assert not policy.may_register("bob-private/", allowed)


def test_a_prefix_boundary_is_a_path_boundary():
    """'alice-priv' must not match 'alice-private/' by string prefix."""
    allowed = ["alice-private/"]
    assert not policy.may_register("alice-priv", allowed)
    assert not policy.may_register("alice-private-backup/", allowed)
    assert policy.may_register("alice-private/", allowed)


def test_ancestors_stay_browsable():
    """Otherwise there is no way to navigate down to your own prefix."""
    allowed = ["library/", "alice-private/"]
    assert policy.may_browse("", allowed)
    assert policy.may_browse("library/", allowed)
    assert not policy.may_browse("bob-private/", allowed)


# --- enforcement ------------------------------------------------------------


def test_alice_cannot_register_bobs_prefix(client):
    headers = _auth(client, "alice")
    ds = _connect(client, headers)

    resp = client.post(
        "/api/v1/directories",
        json={"datasource_id": ds, "path": "bob-private/"},
        headers=headers,
    )
    assert resp.status_code == 403
    assert "scope" in resp.json()["detail"]
    assert client.get("/api/v1/directories", headers=headers).json()["directories"] == []


def test_alice_can_register_her_own_and_the_shared_prefix(client):
    headers = _auth(client, "alice")
    ds = _connect(client, headers)

    for path in ("library/", "alice-private/"):
        resp = client.post(
            "/api/v1/directories",
            json={"datasource_id": ds, "path": path},
            headers=headers,
        )
        assert resp.status_code == 201, f"{path}: {resp.text}"


def test_browsing_hides_prefixes_out_of_scope(client):
    """bob-private/ exists in the bucket but must not appear for alice."""
    headers = _auth(client, "alice")
    ds = _connect(client, headers)

    listing = client.get(
        f"/api/v1/datasources/{ds}/browse?path=", headers=headers
    ).json()
    assert not any("bob" in d for d in listing["directories"])


def test_browsing_into_another_tenants_prefix_is_refused(client):
    headers = _auth(client, "alice")
    ds = _connect(client, headers)

    resp = client.get(
        f"/api/v1/datasources/{ds}/browse?path=bob-private/", headers=headers
    )
    assert resp.status_code == 403


def test_scope_comes_from_policy_not_from_the_request(client):
    """A tenant asking for a wider scope gets the provisioned one anyway.

    Reported rather than stored, so it cannot drift from what is enforced -
    a datasource created before a policy change would otherwise advertise a
    scope nobody is applying.
    """
    headers = _auth(client, "alice")
    resp = client.post(
        "/api/v1/datasources",
        json={"name": "S3", "bucket": BUCKET, "allowed_prefixes": ["", "bob-private/"]},
        headers=headers,
    )
    assert resp.status_code in (200, 201)
    assert resp.json()["allowed_prefixes"] == [
        "library/",
        "library-archive/",
        "alice-private/",
    ]
    assert "allowed_prefixes" not in resp.json()["config"], "scope is not stored"


def test_each_tenant_gets_their_own_scope(client):
    for user_id in ("alice", "bob"):
        headers = _auth(client, user_id)
        ds = _connect(client, headers)
        seen = client.get("/api/v1/datasources", headers=headers).json()[0]
        assert f"{user_id}-private/" in seen["allowed_prefixes"]
        other = "bob" if user_id == "alice" else "alice"
        assert f"{other}-private/" not in seen["allowed_prefixes"]
        assert client.post(
            "/api/v1/directories",
            json={"datasource_id": ds, "path": f"{other}-private/"},
            headers=headers,
        ).status_code == 403
