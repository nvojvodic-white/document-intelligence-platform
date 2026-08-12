"""Connecting the same bucket twice must not fork a user's configuration.

Directory registration was already idempotent; datasource connection was not,
so clicking one button twice produced two rows for one bucket while clicking
the other twice produced one. Same situation, two behaviours - this pins the
consistent one, and covers the migration that has to run against volumes
created before the constraint existed.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import time
import uuid

from fastapi.testclient import TestClient

from tests.conftest import BUCKET

CONFIG = {"bucket": BUCKET, "endpoint_url": None, "region": "us-east-1"}


def test_connecting_the_same_bucket_twice_returns_one_row(core_env):
    repo = core_env["core.repositories"]
    repo.ensure_user("alice", "alice@example.com")

    first, created_first = repo.create_datasource("alice", "s3", "S3", CONFIG, None)
    second, created_second = repo.create_datasource("alice", "s3", "S3", CONFIG, None)

    assert created_first is True
    assert created_second is False
    assert first["id"] == second["id"]
    assert len(repo.list_datasources("alice")) == 1


def test_a_different_bucket_is_a_different_datasource(core_env):
    repo = core_env["core.repositories"]
    repo.ensure_user("alice", "alice@example.com")

    repo.create_datasource("alice", "s3", "S3", CONFIG, None)
    other, created = repo.create_datasource(
        "alice", "s3", "S3", {**CONFIG, "bucket": "other-bucket"}, None
    )

    assert created is True
    assert len(repo.list_datasources("alice")) == 2
    assert other["config"]["bucket"] == "other-bucket"


def test_two_users_may_each_connect_the_same_bucket(core_env):
    """The constraint is per user, not global - a shared bucket is normal."""
    repo = core_env["core.repositories"]
    for user_id in ("alice", "bob"):
        repo.ensure_user(user_id, f"{user_id}@example.com")
        _, created = repo.create_datasource(user_id, "s3", "S3", CONFIG, None)
        assert created is True

    assert len(repo.list_datasources("alice")) == 1
    assert len(repo.list_datasources("bob")) == 1


def test_endpoint_url_is_part_of_the_identity(core_env):
    """Same bucket name against LocalStack and against real AWS are not the
    same datasource. A NULL endpoint must not read as 'always distinct'."""
    repo = core_env["core.repositories"]
    repo.ensure_user("alice", "alice@example.com")

    repo.create_datasource("alice", "s3", "aws", {**CONFIG, "endpoint_url": None}, None)
    _, created = repo.create_datasource(
        "alice", "s3", "local", {**CONFIG, "endpoint_url": "http://localstack:4566"}, None
    )
    assert created is True

    # ...but two connections with no endpoint still collapse to one.
    _, created_again = repo.create_datasource(
        "alice", "s3", "aws", {**CONFIG, "endpoint_url": None}, None
    )
    assert created_again is False
    assert len(repo.list_datasources("alice")) == 2


def test_api_returns_200_when_the_datasource_already_exists(core_env, s3):
    import app.main

    importlib.reload(app.main)
    client = TestClient(app.main.app)

    token = client.post(
        "/api/v1/auth/dev-login", json={"user_id": "alice"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    body = {"name": "S3", "bucket": BUCKET}

    first = client.post("/api/v1/datasources", json=body, headers=headers)
    second = client.post("/api/v1/datasources", json=body, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 200, "a repeat connect is not a new resource"
    assert first.json()["id"] == second.json()["id"]
    assert len(client.get("/api/v1/datasources", headers=headers).json()) == 1


def test_migration_collapses_duplicates_from_an_older_volume(core_env, monkeypatch):
    """A database written before the constraint must still open.

    CREATE UNIQUE INDEX fails outright on a table that already violates it, so
    without the migration this change would stop the API booting against real
    data - a worse bug than the duplicates it fixes.
    """
    db = core_env["core.db"]
    repo = core_env["core.repositories"]
    repo.ensure_user("alice", "alice@example.com")

    # Write duplicates directly, bypassing the repository, to reproduce the
    # pre-constraint state.
    now = time.time()
    ids = []
    with sqlite3.connect(str(db.DB_PATH)) as raw:
        raw.execute("DROP INDEX IF EXISTS ux_datasources_identity")
        for i in range(3):
            ds_id = uuid.uuid4().hex
            ids.append(ds_id)
            raw.execute(
                "INSERT INTO datasources "
                "(id, user_id, kind, name, config, secret_ref, created_at) "
                "VALUES (?, ?, 's3', ?, ?, NULL, ?)",
                (ds_id, "alice", f"copy{i}", json.dumps(CONFIG), now + i),
            )
        # A directory hanging off the newest duplicate, which must be repointed
        # rather than orphaned.
        raw.execute(
            "INSERT INTO directories "
            "(id, user_id, datasource_id, path, status, created_at) "
            "VALUES (?, 'alice', ?, 'alice/lore/', 'idle', ?)",
            (uuid.uuid4().hex, ids[2], now),
        )
        raw.commit()

    db.init(force=True)

    remaining = repo.list_datasources("alice")
    assert len(remaining) == 1, "duplicates should collapse to one"
    assert remaining[0]["id"] == ids[0], "the oldest row is the survivor"

    directories = repo.list_directories("alice")
    assert len(directories) == 1
    assert directories[0]["datasource_id"] == ids[0], "directory was repointed"


def test_migration_is_a_no_op_on_a_clean_database(core_env):
    """Idempotent: init runs on every process start, not just once."""
    db = core_env["core.db"]
    repo = core_env["core.repositories"]
    repo.ensure_user("alice", "alice@example.com")
    repo.create_datasource("alice", "s3", "S3", CONFIG, None)

    db.init(force=True)
    db.init(force=True)

    assert len(repo.list_datasources("alice")) == 1
