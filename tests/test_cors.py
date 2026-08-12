"""The browser path.

These exist because of a real miss: the whole walkthrough was verified with a
server-to-server client, which never sends an Origin header and never issues a
preflight, so it passed against an API the browser could not talk to at all.
Every assertion here fails without CORS configured.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

UI_ORIGIN = "http://localhost:5173"


@pytest.fixture()
def client(core_env):
    import app.main

    importlib.reload(app.main)
    return TestClient(app.main.app)


def test_preflight_is_answered_for_the_ui_origin(client):
    """Browsers send OPTIONS before any call carrying an Authorization header."""
    resp = client.options(
        "/api/v1/auth/dev-users",
        headers={
            "Origin": UI_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert resp.status_code == 200, "preflight must not 405"
    assert resp.headers.get("access-control-allow-origin") == UI_ORIGIN
    assert "authorization" in resp.headers.get(
        "access-control-allow-headers", ""
    ).lower(), "the UI cannot send its token without this"


def test_actual_response_carries_the_allow_origin_header(client):
    """A 200 the browser refuses to read is indistinguishable from a failure."""
    resp = client.get("/api/v1/auth/dev-users", headers={"Origin": UI_ORIGIN})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == UI_ORIGIN


def test_preflight_covers_the_methods_the_ui_uses(client):
    """DELETE is the one that would break file removal specifically."""
    for method in ("POST", "DELETE"):
        resp = client.options(
            "/api/v1/directories",
            headers={
                "Origin": UI_ORIGIN,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert resp.status_code == 200, f"{method} preflight failed"
        allowed = resp.headers.get("access-control-allow-methods", "")
        assert method in allowed, f"{method} not in {allowed!r}"


def test_an_unlisted_origin_is_not_granted_access(client):
    """The allowlist is the point: the UI sends a bearer token on every call,
    and any origin echoed back here could ask the browser to send it too."""
    resp = client.get(
        "/api/v1/auth/dev-users", headers={"Origin": "http://evil.example.com"}
    )
    assert resp.headers.get("access-control-allow-origin") != "http://evil.example.com"
    assert resp.headers.get("access-control-allow-origin") != "*"


def test_preflight_passes_the_api_key_gate(client, monkeypatch):
    """With PLATFORM_API_KEY set, the gate must still let preflight through.

    Preflight carries no credentials by design, so gating it would reject every
    cross-origin call before the real request was ever made - and the UI would
    show an unexplained network error rather than a 401.
    """
    monkeypatch.setenv("PLATFORM_API_KEY", "some-key")
    resp = client.options(
        "/api/v1/directories",
        headers={
            "Origin": UI_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == UI_ORIGIN
