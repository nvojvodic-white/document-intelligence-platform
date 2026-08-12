"""The same isolation property, asserted through HTTP.

The repository-level tests prove the queries are scoped. These prove the API
actually uses them: that a real bearer token for bob, sent to every route that
takes an id, cannot reach anything of alice's.

Retrieval is exercised without an LLM. Synthesis is the only step that needs
one, so these tests call the retrieval layer directly rather than mocking a
model - what is under test is which documents are reachable, not how they get
summarised.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import BUCKET


@pytest.fixture()
def client(core_env, s3, monkeypatch):
    import importlib

    import app.main

    importlib.reload(app.main)
    return TestClient(app.main.app)


def _token(client, user_id: str) -> dict[str, str]:
    resp = client.post("/api/v1/auth/dev-login", json={"user_id": user_id})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def synced(client, core_env):
    """Alice and bob each connect S3, register their prefix, and sync."""
    sync = core_env["core.sync"]
    out = {}
    for user_id in ("alice", "bob"):
        headers = _token(client, user_id)
        ds = client.post(
            "/api/v1/datasources",
            json={"name": "corpus", "bucket": BUCKET},
            headers=headers,
        )
        assert ds.status_code == 201, ds.text
        datasource_id = ds.json()["id"]

        reg = client.post(
            "/api/v1/directories",
            json={"datasource_id": datasource_id, "path": f"{user_id}/lore/"},
            headers=headers,
        )
        assert reg.status_code == 201, reg.text
        directory_id = reg.json()["directory"]["id"]

        assert client.post(
            f"/api/v1/directories/{directory_id}/sync", headers=headers
        ).status_code == 202
        out[user_id] = {
            "headers": headers,
            "datasource_id": datasource_id,
            "directory_id": directory_id,
        }

    while sync.poll_once() is not None:
        pass
    return out


# --- the walkthrough works at all -------------------------------------------


def test_connect_browse_register_sync(client, synced):
    alice = synced["alice"]

    browse = client.get(
        f"/api/v1/datasources/{alice['datasource_id']}/browse",
        headers=alice["headers"],
    )
    assert browse.status_code == 200
    assert "alice/" in browse.json()["directories"]

    runs = client.get(
        f"/api/v1/directories/{alice['directory_id']}/runs", headers=alice["headers"]
    ).json()["runs"]
    assert runs[0]["state"] == "succeeded"
    assert runs[0]["files_new"] == 6

    files = client.get(
        f"/api/v1/directories/{alice['directory_id']}/files", headers=alice["headers"]
    ).json()["files"]
    assert len(files) == 6
    assert all(f["provider_key"].startswith("alice/lore/") for f in files)


def test_double_click_sync_returns_the_same_run(client, synced):
    alice = synced["alice"]
    first = client.post(
        f"/api/v1/directories/{alice['directory_id']}/sync", headers=alice["headers"]
    ).json()
    second = client.post(
        f"/api/v1/directories/{alice['directory_id']}/sync", headers=alice["headers"]
    ).json()

    assert first["already_in_progress"] is False
    assert second["already_in_progress"] is True
    assert first["run"]["id"] == second["run"]["id"]


# --- isolation across the HTTP boundary -------------------------------------


def test_bob_cannot_reach_any_of_alices_resources(client, synced):
    alice, bob = synced["alice"], synced["bob"]

    # Every route that takes an id, with a valid token for the wrong user.
    assert client.get(
        f"/api/v1/datasources/{alice['datasource_id']}/browse", headers=bob["headers"]
    ).status_code == 404
    assert client.get(
        f"/api/v1/directories/{alice['directory_id']}/files", headers=bob["headers"]
    ).status_code == 404
    assert client.get(
        f"/api/v1/directories/{alice['directory_id']}/runs", headers=bob["headers"]
    ).status_code == 404
    assert client.post(
        f"/api/v1/directories/{alice['directory_id']}/sync", headers=bob["headers"]
    ).status_code == 404

    alice_file = client.get(
        f"/api/v1/directories/{alice['directory_id']}/files", headers=alice["headers"]
    ).json()["files"][0]
    assert client.delete(
        f"/api/v1/files/{alice_file['id']}", headers=bob["headers"]
    ).status_code == 404

    alice_run = client.get(
        f"/api/v1/directories/{alice['directory_id']}/runs", headers=alice["headers"]
    ).json()["runs"][0]
    assert client.get(
        f"/api/v1/runs/{alice_run['id']}", headers=bob["headers"]
    ).status_code == 404


def test_listing_endpoints_show_only_your_own(client, synced):
    for user_id in ("alice", "bob"):
        headers = synced[user_id]["headers"]
        datasources = client.get("/api/v1/datasources", headers=headers).json()
        directories = client.get("/api/v1/directories", headers=headers).json()

        assert len(datasources) == 1
        assert len(directories["directories"]) == 1
        assert directories["directories"][0]["path"] == f"{user_id}/lore/"


def test_every_tenant_route_rejects_an_unauthenticated_caller(client, synced):
    alice = synced["alice"]
    for method, path in [
        ("get", "/api/v1/datasources"),
        ("get", "/api/v1/directories"),
        ("post", "/api/v1/directories"),
        ("get", f"/api/v1/directories/{alice['directory_id']}/files"),
        ("post", f"/api/v1/directories/{alice['directory_id']}/sync"),
        ("get", "/api/v1/auth/me"),
    ]:
        resp = (
            client.post(path, json={})
            if method == "post"
            else client.get(path)
        )
        assert resp.status_code == 401, f"{method.upper()} {path} was not 401"


# --- retrieval and citations ------------------------------------------------


def test_retrieval_is_scoped_and_cites_the_source_file(client, synced):
    """The end of the one path that must work, minus synthesis."""
    from app.rag.retrieval.user_scoped import retrieve

    alice_docs = retrieve("alice", "Who is Tom Bombadil?", k=4, kind="hybrid")
    assert alice_docs, "alice should retrieve her own Bombadil document"
    assert any(
        d.metadata["source"].endswith("tom-bombadil.md") for d in alice_docs
    )
    # Citations name the file, carry an id the UI can link, and a chunk id a
    # reviewer can verify against the retrieved text.
    for doc in alice_docs:
        assert doc.metadata["title"]
        assert doc.metadata["source"].startswith("alice/lore/")
        assert doc.metadata["file_id"]
        assert doc.metadata["chunk_id"].startswith(doc.metadata["sha256"])

    bob_docs = retrieve("bob", "Who is Tom Bombadil?", k=4, kind="hybrid")
    assert all(
        not d.metadata["source"].endswith("tom-bombadil.md") for d in bob_docs
    )
    assert all(d.metadata["source"].startswith("bob/") for d in bob_docs)


def test_sparse_retrieval_is_built_per_user(client, synced):
    """The BM25 half of hybrid is built from the user's own chunks only."""
    from app.rag.retrieval.user_scoped import retrieve

    bob_docs = retrieve("bob", "Tom Bombadil Withywindle Old Forest", k=5, kind="sparse")
    assert all(d.metadata["source"].startswith("bob/") for d in bob_docs)


def test_removing_a_file_stops_it_being_retrievable(client, synced):
    """Remove a file and the answer changes - the last step of the walkthrough."""
    from app.rag.retrieval.user_scoped import retrieve

    alice = synced["alice"]
    files = client.get(
        f"/api/v1/directories/{alice['directory_id']}/files", headers=alice["headers"]
    ).json()["files"]
    bombadil = next(f for f in files if f["provider_key"].endswith("tom-bombadil.md"))

    assert any(
        d.metadata["source"].endswith("tom-bombadil.md")
        for d in retrieve("alice", "Who is Tom Bombadil?", k=4)
    )

    resp = client.delete(f"/api/v1/files/{bombadil['id']}", headers=alice["headers"])
    assert resp.status_code == 200
    assert resp.json()["vectors_dropped"] > 0

    assert all(
        not d.metadata["source"].endswith("tom-bombadil.md")
        for d in retrieve("alice", "Who is Tom Bombadil?", k=4)
    )
    remaining = client.get(
        f"/api/v1/directories/{alice['directory_id']}/files", headers=alice["headers"]
    ).json()["files"]
    assert len(remaining) == 5
