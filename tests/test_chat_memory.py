"""Conversation memory: where it lives, and who can read it.

Two bugs motivated these. The store defaulted to a path relative to the working
directory, which inside the container resolved to the image's own writable
layer rather than the mounted volume - so history vanished on every restart
while the platform database beside it survived. And the UI generated a fresh
session id on each mount, so switching users and back started an empty
conversation over turns that were still on disk.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(core_env):
    import app.main

    importlib.reload(app.main)
    return TestClient(app.main.app)


def _token(client, user_id: str) -> dict[str, str]:
    resp = client.post("/api/v1/auth/dev-login", json={"user_id": user_id})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_the_conversation_store_lives_beside_the_platform_database(core_env):
    """Both stores must land on the same volume, or one of them is ephemeral."""
    from pathlib import Path

    from app.rag.memory import store
    from core.config import DB_PATH

    conversation_db = Path(store.DB_PATH).resolve()
    platform_db = Path(DB_PATH).resolve()
    assert conversation_db.parent == platform_db.parent, (
        f"conversation history at {conversation_db} is not beside the platform "
        f"database at {platform_db}; if one is on the mounted volume and the "
        "other is not, restarts silently discard history"
    )
    assert conversation_db.is_absolute(), (
        "a relative path resolves against the working directory, which is not "
        "the volume inside the container"
    )


def test_turns_persist_and_can_be_read_back(client):
    """What the UI needs to rehydrate a conversation after a remount."""
    from app.rag.memory.store import append_turn

    headers = _token(client, "alice")
    append_turn("alice:s-abc", "user", "Who was Sauron?")
    append_turn("alice:s-abc", "assistant", "A Maia of Aule.")

    resp = client.get("/api/v1/rag/sessions/s-abc/turns", headers=headers)
    assert resp.status_code == 200
    turns = resp.json()["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["content"] == "Who was Sauron?"


def test_one_users_session_id_does_not_read_another_users_conversation(client):
    """Session ids come from the client, so they are namespaced by the verified
    user. Guessing another user's id must return nothing."""
    from app.rag.memory.store import append_turn

    append_turn("alice:s-shared", "user", "alice's private question")

    bob = _token(client, "bob")
    resp = client.get("/api/v1/rag/sessions/s-shared/turns", headers=bob)
    assert resp.status_code == 200
    assert resp.json()["turns"] == [], (
        "bob used the same session id string and must still see nothing"
    )

    alice = _token(client, "alice")
    assert client.get(
        "/api/v1/rag/sessions/s-shared/turns", headers=alice
    ).json()["turns"], "alice should still read her own"


def test_clearing_a_session_only_clears_your_own(client):
    from app.rag.memory.store import append_turn

    append_turn("alice:s-x", "user", "keep me")
    append_turn("bob:s-x", "user", "keep me too")

    bob = _token(client, "bob")
    client.delete("/api/v1/rag/sessions/s-x", headers=bob)

    alice = _token(client, "alice")
    assert client.get("/api/v1/rag/sessions/s-x/turns", headers=alice).json()["turns"], (
        "bob clearing his session must not clear alice's"
    )
    assert client.get("/api/v1/rag/sessions/s-x/turns", headers=bob).json()["turns"] == []


def test_hydration_requires_authentication(client):
    assert client.get("/api/v1/rag/sessions/anything/turns").status_code == 401
