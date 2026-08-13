"""The sync engine: one claimed run, start to finish.

Every transition is a database write, so the awkward cases are answered by
reading a row: a second queue attempt is rejected by a partial unique index,
counters are written per file, a stale heartbeat is reclaimed as failed, a bad
file increments a counter and the run ends `partial`, anything missing from the
listing is soft deleted, and "nothing new" is just files_new = 0.

The dedup ladder is in _process_object, cheapest rung first.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from core import repositories as repo
from core import vectors
from core.chunking import chunk_text
from core.config import (
    EMBEDDING_VERSION,
    EXTRACTION_VERSION,
    HEARTBEAT_TIMEOUT_SEC,
)
from core.extraction import extract
from core.hashing import sha256_bytes
from core.storage import ProviderError, get_provider
from core.textstore import read_text, write_text

log = logging.getLogger(__name__)

# Every file would be a needless write; too rare and a slow directory looks
# dead to the reclaimer.
_HEARTBEAT_EVERY = 5


class SyncError(Exception):
    """A failure that ends the whole run, as opposed to one file."""


def run_sync(run: dict[str, Any]) -> dict[str, Any]:
    """Execute one claimed run, returning the finished row.

    Owns the run from `running` to a terminal state. Never raises for a
    per-file problem, only for one that voids the whole run.
    """
    run_id = run["id"]
    user_id = run["user_id"]

    try:
        directory = repo.get_directory(user_id, run["directory_id"])
        if directory is None:
            raise SyncError("directory no longer exists")
        datasource = repo.get_datasource(user_id, directory["datasource_id"])
        if datasource is None:
            raise SyncError("datasource no longer exists")
        provider = get_provider(datasource)
        listing = provider.list_objects(directory["path"])
    except (SyncError, ProviderError) as e:
        # Nothing listed means nothing to attribute; fail the run.
        log.warning("run %s failed before listing: %s", run_id, e)
        return repo.finish_run(run_id, "failed", str(e))

    failures = 0
    seen_keys: set[str] = set()

    for index, obj in enumerate(listing):
        seen_keys.add(obj.key)
        repo.bump_run_counters(run_id, files_seen=1)
        if index % _HEARTBEAT_EVERY == 0:
            repo.heartbeat_run(run_id)

        try:
            _process_object(run_id, user_id, directory, datasource, provider, obj)
        except Exception as e:  # noqa: BLE001 - one bad file must not end the run
            failures += 1
            log.warning("run %s: %s failed: %s", run_id, obj.key, e)
            repo.bump_run_counters(run_id, files_failed=1)
            repo.upsert_file(
                user_id=user_id,
                datasource_id=datasource["id"],
                directory_id=directory["id"],
                provider_key=obj.key,
                etag=obj.etag,
                size=obj.size,
                mtime=obj.mtime,
                # No sha256 on failure: a file we could not process must not
                # look like content the user holds.
                sha256=None,
                state="failed",
                error=str(e)[:500],
            )

    deleted = _reap_deleted(user_id, directory, seen_keys)
    if deleted:
        repo.bump_run_counters(run_id, files_deleted=deleted)

    repo.heartbeat_run(run_id)
    state = "partial" if failures else "succeeded"
    error = f"{failures} file(s) failed" if failures else None
    return repo.finish_run(run_id, state, error)


def _process_object(
    run_id: str,
    user_id: str,
    directory: dict,
    datasource: dict,
    provider,
    obj,
) -> None:
    """Index one object, cheapest applicable rung first."""
    existing = repo.get_file_by_key(user_id, datasource["id"], obj.key)

    # Rung 0: unchanged at the provider, so never downloaded. A re-sync of an
    # unchanged directory is one LIST call and nothing else.
    if _unchanged(existing, obj):
        repo.bump_run_counters(run_id, files_skipped=1)
        return

    data = provider.fetch(obj.key)
    sha256 = sha256_bytes(data)
    repo.bump_run_counters(run_id, bytes_downloaded=len(data))
    repo.upsert_blob(sha256, len(data))

    # Rung 1: bytes known globally at the current extraction version, so reuse
    # the text and chunking.
    chunks = _text_chunks(sha256, obj.key, data)

    # Rung 2: user already possesses these bytes, so only attribution is new
    # and the vector store is untouched.
    if repo.user_has_blob(user_id, sha256):
        repo.upsert_file(
            user_id=user_id,
            datasource_id=datasource["id"],
            directory_id=directory["id"],
            provider_key=obj.key,
            etag=obj.etag,
            size=obj.size,
            mtime=obj.mtime,
            sha256=sha256,
            state="indexed",
        )
        repo.bump_run_counters(run_id, files_new=1, chunks_reused=len(chunks))
        return

    # Rung 3: new to this user. Vectors are copied from cache when known
    # globally, computed only when genuinely new, always into their own
    # collection.
    result = vectors.index_blob_for_user(user_id, sha256, chunks)
    repo.add_user_blob(user_id, sha256)
    repo.mark_blob_embedded(sha256, EMBEDDING_VERSION)

    repo.upsert_file(
        user_id=user_id,
        datasource_id=datasource["id"],
        directory_id=directory["id"],
        provider_key=obj.key,
        etag=obj.etag,
        size=obj.size,
        mtime=obj.mtime,
        sha256=sha256,
        state="indexed",
    )
    repo.bump_run_counters(
        run_id,
        files_new=1,
        chunks_embedded=result["embedded"],
        chunks_reused=result["from_cache"],
    )


def _unchanged(existing: dict | None, obj) -> bool:
    """Does the recorded row still describe this object exactly?

    A previously failed row is retried rather than skipped, so a transient
    failure does not become permanent.
    """
    if not existing or existing.get("deleted_at") or existing.get("state") != "indexed":
        return False
    if not existing.get("sha256"):
        return False
    return (
        existing.get("etag") == obj.etag
        and existing.get("size") == obj.size
        and existing.get("mtime") == obj.mtime
    )


def _text_chunks(sha256: str, provider_key: str, data: bytes) -> list[str]:
    """Chunks for a blob, reusing cached extraction when current."""
    blob = repo.get_blob(sha256)
    if blob and blob.get("extraction_version") == EXTRACTION_VERSION:
        cached = repo.get_chunks(sha256)
        if cached:
            return [c["text"] for c in cached]
        # Extraction current but chunks missing: re-chunk the stored text.
        text = read_text(blob.get("extracted_text_ref") or "")
        if text:
            chunks = chunk_text(text)
            repo.replace_chunks(sha256, chunks)
            return chunks

    # ExtractionError propagates to the per-file handler in run_sync.
    text = extract(provider_key, data)
    chunks = chunk_text(text)
    repo.mark_blob_extracted(sha256, write_text(sha256, text), EXTRACTION_VERSION)
    repo.replace_chunks(sha256, chunks)
    return chunks


def _reap_deleted(user_id: str, directory: dict, seen_keys: set[str]) -> int:
    """Soft delete rows whose object is gone from the source.

    Same rule as manual removal: drop vectors only once no other live row of
    this user references the same bytes.
    """
    deleted = 0
    for row in repo.list_live_provider_keys(user_id, directory["id"]):
        if row["provider_key"] in seen_keys:
            continue
        repo.soft_delete_file(user_id, row["id"])
        deleted += 1
        sha256 = row.get("sha256")
        if sha256 and not repo.user_still_references_blob(user_id, sha256):
            vectors.drop_blob_for_user(user_id, sha256)
            repo.remove_user_blob(user_id, sha256)
    return deleted


# --- worker loop ------------------------------------------------------------


def poll_once() -> dict[str, Any] | None:
    """Reclaim dead runs, then claim and run at most one queued run. Separate
    from the loop so tests can step the worker."""
    for stale in repo.reclaim_dead_runs(HEARTBEAT_TIMEOUT_SEC):
        log.warning(
            "reclaimed run %s: heartbeat older than %ss",
            stale["id"],
            HEARTBEAT_TIMEOUT_SEC,
        )

    run = repo.claim_next_run()
    if run is None:
        return None

    log.info("claimed run %s (user=%s)", run["id"], run["user_id"])
    started = time.perf_counter()
    finished = run_sync(run)
    log.info(
        "run %s finished %s in %.1fs (seen=%s new=%s skipped=%s failed=%s "
        "deleted=%s embedded=%s reused=%s)",
        finished["id"],
        finished["state"],
        time.perf_counter() - started,
        finished["files_seen"],
        finished["files_new"],
        finished["files_skipped"],
        finished["files_failed"],
        finished["files_deleted"],
        finished["chunks_embedded"],
        finished["chunks_reused"],
    )
    return finished
