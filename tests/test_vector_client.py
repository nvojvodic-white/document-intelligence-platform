"""How the vector store is reached.

Chroma's in-process client assumes one process owns its directory. The API and
worker are two processes on one volume, so reading during a sync failed with
"Error executing plan: Internal error: Error finding id" - intermittently, and
only while a sync was writing, which is the worst way for a bug to present.

CHROMA_HOST switches to the service. These pin the selection so a future change
cannot quietly put two processes back on one directory.
"""
from __future__ import annotations

import importlib

import chromadb


def _reload_with(monkeypatch, **env):
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    import core.config

    importlib.reload(core.config)
    import core.vectors

    return importlib.reload(core.vectors)


def test_chroma_host_selects_the_http_client(core_env, monkeypatch, tmp_path):
    """With a host set, nothing touches the filesystem."""
    vectors = _reload_with(
        monkeypatch, CHROMA_HOST="chroma", CHROMA_PORT="8000",
        CHROMA_DIR=str(tmp_path / "unused"),
    )

    captured = {}

    def fake_http(host, port, **kwargs):
        captured["host"] = host
        captured["port"] = port
        return object()

    monkeypatch.setattr(chromadb, "HttpClient", fake_http)
    monkeypatch.setattr(
        chromadb, "PersistentClient",
        lambda **kw: pytest_fail("PersistentClient must not be used with CHROMA_HOST"),
    )

    vectors._client = None
    vectors._get_client()

    assert captured == {"host": "chroma", "port": 8000}
    assert not (tmp_path / "unused").exists(), "no directory should be created"


def test_without_a_host_it_falls_back_to_on_disk(core_env, monkeypatch, tmp_path):
    """Single-process runs and the test suite keep the in-process store."""
    target = tmp_path / "chroma-local"
    vectors = _reload_with(
        monkeypatch, CHROMA_HOST=None, CHROMA_DIR=str(target)
    )

    monkeypatch.setattr(
        chromadb, "HttpClient",
        lambda **kw: pytest_fail("HttpClient must not be used without CHROMA_HOST"),
    )
    vectors._client = None
    vectors._get_client()

    assert target.exists()


def test_the_client_is_built_once(core_env, monkeypatch, tmp_path):
    """The client is cached; rebuilding per call would open a new connection
    for every query."""
    vectors = _reload_with(monkeypatch, CHROMA_HOST="chroma")
    calls = []
    monkeypatch.setattr(
        chromadb, "HttpClient", lambda host, port, **kw: calls.append(1) or object()
    )
    vectors._client = None
    vectors._get_client()
    vectors._get_client()
    assert len(calls) == 1


def pytest_fail(msg):
    raise AssertionError(msg)
