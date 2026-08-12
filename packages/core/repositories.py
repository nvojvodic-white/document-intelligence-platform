"""Every query in the system, with tenant scope built into the signatures.

The rule this module exists to enforce: **any function that touches tenant data
takes user_id as its first argument**, and that user_id reaches the WHERE
clause. There are no unscoped reads of files, directories, datasources, or
chunks. A reviewer should be able to check the isolation claim by reading the
function signatures below rather than by auditing call sites.

Three tables are deliberately global and take no user_id - `blobs`, `chunks`,
and `embedding_cache`. They are keyed by the sha256 of content. Reaching them
requires already holding the bytes that hash to that key, and nothing in them
records who else holds the same bytes. Text resolution for answers still goes
through get_chunk_texts(), which joins user_blobs, so possession is checked on
the read path even though the storage is shared.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any, Iterable

from core import db


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> float:
    return time.time()


def _rows(cur) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


def _row(cur) -> dict[str, Any] | None:
    r = cur.fetchone()
    return dict(r) if r else None


# --- users ------------------------------------------------------------------


def ensure_user(user_id: str, email: str) -> dict[str, Any]:
    """Idempotently create a user. Used by the dev-login seed."""
    with db.connect() as c:
        c.execute(
            "INSERT INTO users (id, email, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO NOTHING",
            (user_id, email, _now()),
        )
        return _row(c.execute("SELECT * FROM users WHERE id = ?", (user_id,)))


def get_user(user_id: str) -> dict[str, Any] | None:
    with db.connect() as c:
        return _row(c.execute("SELECT * FROM users WHERE id = ?", (user_id,)))


# --- datasources ------------------------------------------------------------


def create_datasource(
    user_id: str, kind: str, name: str, config: dict, secret_ref: str | None
) -> dict[str, Any]:
    """Register a datasource. `config` holds non-secret settings only; the
    credential is referenced by name and resolved from the environment at use
    time, so it never enters the database or an API response."""
    ds_id = _id()
    with db.connect() as c:
        c.execute(
            "INSERT INTO datasources "
            "(id, user_id, kind, name, config, secret_ref, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ds_id, user_id, kind, name, json.dumps(config), secret_ref, _now()),
        )
    return get_datasource(user_id, ds_id)


def list_datasources(user_id: str) -> list[dict[str, Any]]:
    with db.connect() as c:
        rows = _rows(
            c.execute(
                "SELECT * FROM datasources WHERE user_id = ? ORDER BY created_at",
                (user_id,),
            )
        )
    return [_hydrate_datasource(r) for r in rows]


def get_datasource(user_id: str, datasource_id: str) -> dict[str, Any] | None:
    """Scoped by user_id as well as id: a datasource id belonging to another
    user reads as absent rather than as forbidden."""
    with db.connect() as c:
        row = _row(
            c.execute(
                "SELECT * FROM datasources WHERE user_id = ? AND id = ?",
                (user_id, datasource_id),
            )
        )
    return _hydrate_datasource(row) if row else None


def _hydrate_datasource(row: dict[str, Any]) -> dict[str, Any]:
    row["config"] = json.loads(row["config"])
    return row


# --- directories ------------------------------------------------------------


def create_directory(
    user_id: str, datasource_id: str, path: str
) -> tuple[dict[str, Any], bool]:
    """Register a directory for syncing. Returns (directory, created).

    Re-registering the same path is not an error: the unique constraint on
    (user_id, datasource_id, path) makes it a no-op that returns the existing
    row, so a double submit cannot fork a directory into two.
    """
    with db.connect() as c:
        cur = c.execute(
            "INSERT INTO directories "
            "(id, user_id, datasource_id, path, status, created_at) "
            "VALUES (?, ?, ?, ?, 'idle', ?) "
            "ON CONFLICT(user_id, datasource_id, path) DO NOTHING",
            (_id(), user_id, datasource_id, path, _now()),
        )
        created = cur.rowcount > 0
        row = _row(
            c.execute(
                "SELECT * FROM directories "
                "WHERE user_id = ? AND datasource_id = ? AND path = ?",
                (user_id, datasource_id, path),
            )
        )
    return row, created


def list_directories(user_id: str) -> list[dict[str, Any]]:
    with db.connect() as c:
        return _rows(
            c.execute(
                "SELECT * FROM directories WHERE user_id = ? ORDER BY created_at",
                (user_id,),
            )
        )


def get_directory(user_id: str, directory_id: str) -> dict[str, Any] | None:
    with db.connect() as c:
        return _row(
            c.execute(
                "SELECT * FROM directories WHERE user_id = ? AND id = ?",
                (user_id, directory_id),
            )
        )


def set_directory_status(user_id: str, directory_id: str, status: str) -> None:
    with db.connect() as c:
        c.execute(
            "UPDATE directories SET status = ? WHERE user_id = ? AND id = ?",
            (status, user_id, directory_id),
        )


# --- blobs (global, content-addressed, no user_id) --------------------------


def get_blob(sha256: str) -> dict[str, Any] | None:
    with db.connect() as c:
        return _row(c.execute("SELECT * FROM blobs WHERE sha256 = ?", (sha256,)))


def upsert_blob(sha256: str, byte_size: int) -> None:
    with db.connect() as c:
        c.execute(
            "INSERT INTO blobs (sha256, byte_size, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(sha256) DO NOTHING",
            (sha256, byte_size, _now()),
        )


def mark_blob_extracted(
    sha256: str, text_ref: str, extraction_version: str
) -> None:
    with db.connect() as c:
        c.execute(
            "UPDATE blobs SET extracted_text_ref = ?, extraction_version = ? "
            "WHERE sha256 = ?",
            (text_ref, extraction_version, sha256),
        )


def mark_blob_embedded(sha256: str, embedding_version: str) -> None:
    with db.connect() as c:
        c.execute(
            "UPDATE blobs SET embedding_version = ? WHERE sha256 = ?",
            (embedding_version, sha256),
        )


# --- user_blobs (possession) ------------------------------------------------


def user_has_blob(user_id: str, sha256: str) -> bool:
    with db.connect() as c:
        return (
            c.execute(
                "SELECT 1 FROM user_blobs WHERE user_id = ? AND sha256 = ?",
                (user_id, sha256),
            ).fetchone()
            is not None
        )


def add_user_blob(user_id: str, sha256: str) -> None:
    with db.connect() as c:
        c.execute(
            "INSERT INTO user_blobs (user_id, sha256, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, sha256) DO NOTHING",
            (user_id, sha256, _now()),
        )


def remove_user_blob(user_id: str, sha256: str) -> None:
    with db.connect() as c:
        c.execute(
            "DELETE FROM user_blobs WHERE user_id = ? AND sha256 = ?",
            (user_id, sha256),
        )


# --- chunks (global storage, user-scoped reads) -----------------------------


def replace_chunks(sha256: str, texts: list[str]) -> None:
    """Store the chunking of a blob, replacing any previous chunking of it."""
    with db.connect() as c:
        c.execute("DELETE FROM chunks WHERE sha256 = ?", (sha256,))
        c.executemany(
            "INSERT INTO chunks (sha256, ordinal, text) VALUES (?, ?, ?)",
            [(sha256, i, t) for i, t in enumerate(texts)],
        )


def get_chunks(sha256: str) -> list[dict[str, Any]]:
    with db.connect() as c:
        return _rows(
            c.execute(
                "SELECT sha256, ordinal, text FROM chunks WHERE sha256 = ? "
                "ORDER BY ordinal",
                (sha256,),
            )
        )


def get_chunk_texts(user_id: str, chunk_ids: Iterable[str]) -> dict[str, str]:
    """Resolve chunk ids to text, for this user only.

    This is isolation mechanism four. Retrieval hands back ids; text is only
    ever produced here, and only for chunks whose content the user actually
    possesses (the user_blobs join). A vector that somehow surfaced from
    another tenant's collection resolves to nothing and cannot become visible
    text in an answer.

    Chunk id format is '{sha256}:{ordinal}'. Unknown or unpossessed ids are
    omitted from the result rather than raising, so a partially stale index
    degrades to fewer citations instead of an error.
    """
    parsed: list[tuple[str, int]] = []
    for cid in chunk_ids:
        sha, _, ordinal = cid.rpartition(":")
        if sha and ordinal.isdigit():
            parsed.append((sha, int(ordinal)))
    if not parsed:
        return {}

    out: dict[str, str] = {}
    with db.connect() as c:
        # Chunked into batches so a large k cannot exceed SQLite's variable
        # limit (999 by default; each pair costs two).
        for i in range(0, len(parsed), 400):
            batch = parsed[i : i + 400]
            conds = " OR ".join(["(c.sha256 = ? AND c.ordinal = ?)"] * len(batch))
            params: list[Any] = [user_id]
            for sha, ordinal in batch:
                params.extend([sha, ordinal])
            rows = _rows(
                c.execute(
                    "SELECT c.sha256, c.ordinal, c.text FROM chunks c "
                    "JOIN user_blobs ub ON ub.sha256 = c.sha256 AND ub.user_id = ? "
                    f"WHERE {conds}",
                    params,
                )
            )
            for r in rows:
                out[f"{r['sha256']}:{r['ordinal']}"] = r["text"]
    return out


# --- embedding cache (global, keyed by content + version) -------------------


def get_cached_vectors(
    sha256: str, embedding_version: str
) -> dict[int, bytes]:
    """Cached vectors for a blob's chunks, as {ordinal: raw float32 bytes}."""
    with db.connect() as c:
        rows = _rows(
            c.execute(
                "SELECT ordinal, vector FROM embedding_cache "
                "WHERE sha256 = ? AND embedding_version = ? ORDER BY ordinal",
                (sha256, embedding_version),
            )
        )
    return {r["ordinal"]: r["vector"] for r in rows}


def put_cached_vectors(
    sha256: str,
    embedding_version: str,
    vectors: dict[int, bytes],
    dim: int,
) -> None:
    now = _now()
    with db.connect() as c:
        c.executemany(
            "INSERT INTO embedding_cache "
            "(sha256, ordinal, embedding_version, vector, dim, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(sha256, ordinal, embedding_version) DO NOTHING",
            [
                (sha256, ordinal, embedding_version, vec, dim, now)
                for ordinal, vec in sorted(vectors.items())
            ],
        )


# --- files (attribution) ----------------------------------------------------


def get_file_by_key(
    user_id: str, datasource_id: str, provider_key: str
) -> dict[str, Any] | None:
    with db.connect() as c:
        return _row(
            c.execute(
                "SELECT * FROM files "
                "WHERE user_id = ? AND datasource_id = ? AND provider_key = ?",
                (user_id, datasource_id, provider_key),
            )
        )


def upsert_file(
    user_id: str,
    datasource_id: str,
    directory_id: str,
    provider_key: str,
    etag: str | None,
    size: int | None,
    mtime: float | None,
    sha256: str | None,
    state: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Record what this user saw at this provider key.

    Clears deleted_at on write, so a file that reappears at the source is
    revived through the same path that first created it rather than through a
    special case.
    """
    now = _now()
    with db.connect() as c:
        c.execute(
            "INSERT INTO files (id, user_id, datasource_id, directory_id, "
            "provider_key, etag, size, mtime, sha256, state, error, "
            "deleted_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?) "
            "ON CONFLICT(user_id, datasource_id, provider_key) DO UPDATE SET "
            "directory_id = excluded.directory_id, etag = excluded.etag, "
            "size = excluded.size, mtime = excluded.mtime, "
            "sha256 = excluded.sha256, state = excluded.state, "
            "error = excluded.error, deleted_at = NULL, updated_at = ?",
            (
                _id(), user_id, datasource_id, directory_id, provider_key,
                etag, size, mtime, sha256, state, error, now, now, now,
            ),
        )
    return get_file_by_key(user_id, datasource_id, provider_key)


def list_files(
    user_id: str, directory_id: str | None = None, include_deleted: bool = False
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM files WHERE user_id = ?"
    params: list[Any] = [user_id]
    if directory_id:
        sql += " AND directory_id = ?"
        params.append(directory_id)
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    sql += " ORDER BY provider_key"
    with db.connect() as c:
        return _rows(c.execute(sql, params))


def get_file(user_id: str, file_id: str) -> dict[str, Any] | None:
    with db.connect() as c:
        return _row(
            c.execute(
                "SELECT * FROM files WHERE user_id = ? AND id = ?",
                (user_id, file_id),
            )
        )


def soft_delete_file(user_id: str, file_id: str) -> dict[str, Any] | None:
    """Soft delete, returning the row as it was. Attribution is retained; only
    its visibility changes. Dropping the content's vectors is a separate,
    conditional step - see user_still_references_blob."""
    now = _now()
    with db.connect() as c:
        c.execute(
            "UPDATE files SET state = 'deleted', deleted_at = ?, updated_at = ? "
            "WHERE user_id = ? AND id = ? AND deleted_at IS NULL",
            (now, now, user_id, file_id),
        )
    return get_file(user_id, file_id)


def user_still_references_blob(
    user_id: str, sha256: str, excluding_file_id: str | None = None
) -> bool:
    """Does this user hold any other live file row for these bytes?

    The guard on removal. The same content can arrive under several names or in
    several directories, so vectors may only be dropped once the last live
    reference for that user is gone.
    """
    sql = (
        "SELECT 1 FROM files WHERE user_id = ? AND sha256 = ? "
        "AND deleted_at IS NULL"
    )
    params: list[Any] = [user_id, sha256]
    if excluding_file_id:
        sql += " AND id != ?"
        params.append(excluding_file_id)
    with db.connect() as c:
        return c.execute(sql + " LIMIT 1", params).fetchone() is not None


def list_live_provider_keys(user_id: str, directory_id: str) -> list[dict[str, Any]]:
    """Live rows for a directory, used to detect deletions at the source."""
    with db.connect() as c:
        return _rows(
            c.execute(
                "SELECT id, provider_key, sha256 FROM files "
                "WHERE user_id = ? AND directory_id = ? AND deleted_at IS NULL",
                (user_id, directory_id),
            )
        )


# --- sync runs --------------------------------------------------------------


def enqueue_run(user_id: str, directory_id: str) -> tuple[dict[str, Any], bool]:
    """Queue a sync. Returns (run, created).

    Double-click protection is the partial unique index on
    sync_runs(directory_id) WHERE state IN ('queued','running'), not a check
    here: two concurrent POSTs both attempt the insert, the database rejects
    the loser, and that caller is handed the run already in flight. Checking
    first and inserting second would leave a race between the two statements.
    """
    # Bounded retry rather than recursion: the losing insert can find no active
    # run if the winner finished in between, and that deserves one more attempt
    # rather than a phantom "already in progress". A pathological interleaving
    # must terminate, so the loop is capped instead of calling itself.
    for _attempt in range(3):
        try:
            with db.connect() as c:
                c.execute(
                    "INSERT INTO sync_runs "
                    "(id, user_id, directory_id, state, created_at) "
                    "VALUES (?, ?, ?, 'queued', ?)",
                    (_id(), user_id, directory_id, _now()),
                )
            set_directory_status(user_id, directory_id, "queued")
            return get_active_run(user_id, directory_id), True
        except sqlite3.IntegrityError:
            existing = get_active_run(user_id, directory_id)
            if existing is not None:
                return existing, False
    raise RuntimeError(
        f"could not enqueue a run for directory {directory_id}: the active-run "
        "slot kept changing hands"
    )


def get_active_run(user_id: str, directory_id: str) -> dict[str, Any] | None:
    with db.connect() as c:
        return _row(
            c.execute(
                "SELECT * FROM sync_runs WHERE user_id = ? AND directory_id = ? "
                "AND state IN ('queued', 'running')",
                (user_id, directory_id),
            )
        )


def get_run(user_id: str, run_id: str) -> dict[str, Any] | None:
    with db.connect() as c:
        return _row(
            c.execute(
                "SELECT * FROM sync_runs WHERE user_id = ? AND id = ?",
                (user_id, run_id),
            )
        )


def list_runs(user_id: str, directory_id: str, limit: int = 10) -> list[dict[str, Any]]:
    with db.connect() as c:
        return _rows(
            c.execute(
                "SELECT * FROM sync_runs WHERE user_id = ? AND directory_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, directory_id, limit),
            )
        )


def claim_next_run() -> dict[str, Any] | None:
    """Claim one queued run for execution. Worker-side, so no user_id: the
    worker serves every tenant and reads the owner off the claimed row.

    A single conditional UPDATE inside an IMMEDIATE transaction. The WHERE
    clause carries the precondition, so a second claimant updates zero rows
    rather than stealing a run already taken.
    """
    now = _now()
    with db.transaction() as c:
        row = c.execute(
            "SELECT * FROM sync_runs WHERE state = 'queued' "
            "ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        cur = c.execute(
            "UPDATE sync_runs SET state = 'running', started_at = ?, "
            "heartbeat_at = ? WHERE id = ? AND state = 'queued'",
            (now, now, row["id"]),
        )
        if cur.rowcount == 0:
            return None
        claimed = dict(c.execute(
            "SELECT * FROM sync_runs WHERE id = ?", (row["id"],)
        ).fetchone())
    set_directory_status(claimed["user_id"], claimed["directory_id"], "running")
    return claimed


def heartbeat_run(run_id: str) -> None:
    with db.connect() as c:
        c.execute(
            "UPDATE sync_runs SET heartbeat_at = ? WHERE id = ? AND state = 'running'",
            (_now(), run_id),
        )


def bump_run_counters(run_id: str, **deltas: int) -> None:
    """Increment counters in place, so progress is read from the database
    rather than from the worker's memory. A UI refresh mid-run shows real
    progress, and a crashed worker leaves an honest partial count behind."""
    allowed = {
        "files_seen", "files_new", "files_skipped", "files_failed", "files_deleted"
    }
    sets, params = [], []
    for key, delta in deltas.items():
        if key not in allowed:
            raise ValueError(f"unknown counter: {key}")
        sets.append(f"{key} = {key} + ?")
        params.append(delta)
    if not sets:
        return
    params.append(run_id)
    with db.connect() as c:
        c.execute(f"UPDATE sync_runs SET {', '.join(sets)} WHERE id = ?", params)


def finish_run(run_id: str, state: str, error: str | None = None) -> dict[str, Any]:
    with db.connect() as c:
        c.execute(
            "UPDATE sync_runs SET state = ?, error = ?, finished_at = ? WHERE id = ?",
            (state, error, _now(), run_id),
        )
        run = _row(c.execute("SELECT * FROM sync_runs WHERE id = ?", (run_id,)))
    set_directory_status(run["user_id"], run["directory_id"], state)
    return run


def reclaim_dead_runs(timeout_sec: float) -> list[dict[str, Any]]:
    """Fail running runs whose heartbeat has gone stale, freeing the directory.

    Without this a worker killed mid-run would hold the partial unique index
    entry forever and that directory could never be synced again.
    """
    cutoff = _now() - timeout_sec
    reclaimed: list[dict[str, Any]] = []
    with db.transaction() as c:
        stale = [
            dict(r)
            for r in c.execute(
                "SELECT id FROM sync_runs WHERE state = 'running' "
                "AND (heartbeat_at IS NULL OR heartbeat_at < ?)",
                (cutoff,),
            ).fetchall()
        ]
        for run in stale:
            c.execute(
                "UPDATE sync_runs SET state = 'failed', error = ?, finished_at = ? "
                "WHERE id = ? AND state = 'running'",
                ("worker died: heartbeat stale", _now(), run["id"]),
            )
            # Re-read after the update. Returning the pre-update snapshot would
            # hand callers rows still labelled 'running', which is exactly the
            # thing this function just stopped being true.
            reclaimed.append(
                dict(c.execute(
                    "SELECT * FROM sync_runs WHERE id = ?", (run["id"],)
                ).fetchone())
            )
    for run in reclaimed:
        set_directory_status(run["user_id"], run["directory_id"], "failed")
    return reclaimed
