"""Shared fixtures.

Every test runs against a temporary DATA_DIR: its own SQLite database, its own
Chroma directory, its own text store. core.config reads those paths at import
time, so the environment is set and the modules are reloaded before anything
else imports them.

Embeddings use the deterministic offline provider, so the suite needs no API
keys and no network. That matters because the isolation test is the evidence
for the data-safety claim - a test that can only run with secrets present is a
test that quietly stops running.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

BUCKET = "tolkien-corpus"

# A small fixture corpus, deliberately NOT the demo corpus in corpus/tolkien.
# The demo corpus is ~2,300 documents; seeding it into moto for every test
# would turn a 30-second suite into a several-minute one and make the expected
# counters churn every time a document is added. The fixture is 12 files
# carrying the same properties under test: a within-user duplicate, a
# cross-user duplicate, and a unique document per user.
CORPUS = Path(__file__).parent / "fixtures" / "corpus"

# The demo corpus, checked for its own invariants by test_demo_corpus.py.
DEMO_CORPUS = Path(__file__).resolve().parents[1] / "corpus" / "tolkien"


@pytest.fixture()
def core_env(tmp_path, monkeypatch):
    """A clean platform: fresh database, fresh vector store, no API keys."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "platform.db"))
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("TEXT_STORE_DIR", str(tmp_path / "text"))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.setenv("JWT_SECRET", "test-secret")

    # config caches paths at import; reload it and everything holding them.
    import core.config

    importlib.reload(core.config)
    modules = [
        "core.db",
        "core.textstore",
        "core.embeddings",
        "core.repositories",
        "core.vectors",
        "core.sync",
        # Resolves its path from DATA_DIR at import time, so without a reload it
        # keeps whichever tmp_path was current when some earlier test first
        # imported it - and then writes one test's turns into another's
        # directory.
        "app.rag.memory.store",
    ]
    reloaded = {}
    for name in modules:
        try:
            reloaded[name] = importlib.reload(importlib.import_module(name))
        except ModuleNotFoundError:
            pass
    reloaded["core.db"].init(force=True)
    yield reloaded


@pytest.fixture()
def s3(core_env, monkeypatch):
    """A mocked S3 bucket seeded from the committed corpus.

    Seeded from the real corpus files rather than from invented strings, so the
    dedup pairs under test are the same bytes the demo uses.
    """
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        for path in sorted(CORPUS.rglob("*.md")):
            key = path.relative_to(CORPUS).as_posix()
            if "/" not in key:
                continue  # the fixture's own README, not a document
            client.put_object(Bucket=BUCKET, Key=key, Body=path.read_bytes())
        yield client


@pytest.fixture()
def datasource_row():
    """A datasource dict shaped like a stored row, pointed at the mock bucket."""
    return {
        "id": "ds-test",
        "kind": "s3",
        "config": {"bucket": BUCKET, "endpoint_url": None, "region": "us-east-1"},
        "secret_ref": None,
    }


def corpus_bytes(relative: str) -> bytes:
    return (CORPUS / relative).read_bytes()


@pytest.fixture(autouse=True)
def _no_ambient_openai(monkeypatch):
    """Fail loudly if a test ever reaches for the real embedding provider.

    Without this, a developer's ambient OPENAI_API_KEY would let a test that
    should be offline quietly make paid network calls, and CI would then fail
    on a machine that had no key.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
