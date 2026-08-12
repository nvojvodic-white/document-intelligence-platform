"""The sync state machine and the dedup ladder.

One test per awkward case named in the brief, because "what happens if I click
sync twice" is the kind of question that gets asked in review and should be
answerable by pointing at a test rather than by reasoning about the code.
"""
from __future__ import annotations

import pytest

from tests.conftest import BUCKET, corpus_bytes


@pytest.fixture()
def alice(s3, core_env, datasource_row):
    """Alice with a registered directory over alice/lore/, nothing synced yet."""
    repo = core_env["core.repositories"]
    repo.ensure_user("alice", "alice@example.com")
    ds = repo.create_datasource(
        "alice", "s3", "corpus", datasource_row["config"], None
    )
    directory, _ = repo.create_directory("alice", ds["id"], "alice/lore/")
    return {"ds": ds, "dir": directory}


def _sync(core_env, user_id, directory_id):
    repo = core_env["core.repositories"]
    sync = core_env["core.sync"]
    repo.enqueue_run(user_id, directory_id)
    return sync.poll_once()


# --- the happy path ---------------------------------------------------------


def test_first_sync_indexes_every_document(alice, core_env):
    run = _sync(core_env, "alice", alice["dir"]["id"])

    assert run["state"] == "succeeded"
    assert run["files_seen"] == 6
    assert run["files_new"] == 6
    assert run["files_skipped"] == 0
    assert run["files_failed"] == 0
    assert run["chunks_embedded"] > 0
    assert run["bytes_downloaded"] > 0


# --- rung 0: the cheap path -------------------------------------------------


def test_resync_of_an_unchanged_directory_downloads_nothing(alice, core_env):
    """The claim that a re-sync costs one LIST call and nothing else."""
    _sync(core_env, "alice", alice["dir"]["id"])
    second = _sync(core_env, "alice", alice["dir"]["id"])

    assert second["state"] == "succeeded"
    assert second["files_seen"] == 6
    assert second["files_skipped"] == 6
    assert second["files_new"] == 0, "nothing new: honest reporting, not a UI case"
    assert second["bytes_downloaded"] == 0, "no object was downloaded"
    assert second["chunks_embedded"] == 0, "no embedding call was made"


# --- rung 2: within-user dedup ----------------------------------------------


def test_same_bytes_in_a_second_directory_only_adds_attribution(alice, core_env):
    """alice/archive/mithril.md is byte-identical to alice/lore/mithril.md."""
    repo = core_env["core.repositories"]
    vectors = core_env["core.vectors"]
    from core.hashing import sha256_bytes

    _sync(core_env, "alice", alice["dir"]["id"])
    size_after_first = vectors.collection_size("alice")

    archive, _ = repo.create_directory("alice", alice["ds"]["id"], "alice/archive/")
    run = _sync(core_env, "alice", archive["id"])

    assert run["state"] == "succeeded"
    assert run["files_new"] == 1
    assert run["chunks_embedded"] == 0, "the vector store must not be touched"
    assert vectors.collection_size("alice") == size_after_first

    # Two attribution rows, one blob, one possession row.
    sha = sha256_bytes(corpus_bytes("alice/lore/mithril.md"))
    rows = [f for f in repo.list_files("alice") if f["sha256"] == sha]
    assert len(rows) == 2, "the same content under two names is two files rows"
    assert len({r["provider_key"] for r in rows}) == 2


# --- double click -----------------------------------------------------------


def test_second_sync_request_returns_the_run_already_in_flight(alice, core_env):
    repo = core_env["core.repositories"]

    first, created_first = repo.enqueue_run("alice", alice["dir"]["id"])
    second, created_second = repo.enqueue_run("alice", alice["dir"]["id"])

    assert created_first is True
    assert created_second is False, "a second run must not be created"
    assert first["id"] == second["id"], "the caller gets the run already queued"
    assert len(repo.list_runs("alice", alice["dir"]["id"], limit=10)) == 1


# --- refresh mid-run --------------------------------------------------------


def test_counters_are_readable_from_the_database_during_a_run(alice, core_env):
    """Progress is a row, not process memory, so a refresh shows real numbers."""
    repo = core_env["core.repositories"]
    run, _ = repo.enqueue_run("alice", alice["dir"]["id"])

    repo.claim_next_run()
    repo.bump_run_counters(run["id"], files_seen=2, files_new=1)

    # A different caller - as the API is, relative to the worker - reads the
    # same counters back mid-flight.
    observed = repo.get_run("alice", run["id"])
    assert observed["state"] == "running"
    assert observed["files_seen"] == 2
    assert observed["files_new"] == 1


# --- dead worker ------------------------------------------------------------


def test_a_run_whose_worker_died_is_reclaimed_and_the_directory_freed(
    alice, core_env
):
    repo = core_env["core.repositories"]
    repo.enqueue_run("alice", alice["dir"]["id"])
    claimed = repo.claim_next_run()
    assert claimed["state"] == "running"

    # Nothing is reclaimed while the heartbeat is fresh.
    assert repo.reclaim_dead_runs(120) == []

    # A negative timeout makes every heartbeat stale, standing in for a worker
    # that was killed without waiting two minutes of wall clock.
    reclaimed = repo.reclaim_dead_runs(-1)
    assert len(reclaimed) == 1
    assert reclaimed[0]["state"] == "failed"
    assert "heartbeat" in reclaimed[0]["error"]

    # The directory is free again, which is the point of reclaiming.
    _, created = repo.enqueue_run("alice", alice["dir"]["id"])
    assert created is True


# --- one bad file -----------------------------------------------------------


def test_one_unreadable_file_does_not_abort_the_run(alice, core_env, s3):
    """An unsupported type fails its own file and the run ends `partial`."""
    repo = core_env["core.repositories"]
    s3.put_object(
        Bucket=BUCKET, Key="alice/lore/broken.bin", Body=b"\x00\x01\x02not text"
    )

    run = _sync(core_env, "alice", alice["dir"]["id"])

    assert run["state"] == "partial", "partial, not failed: the others indexed"
    assert run["files_seen"] == 7
    assert run["files_failed"] == 1
    assert run["files_new"] == 6, "every good file still indexed"

    failed = [f for f in repo.list_files("alice") if f["state"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["provider_key"].endswith("broken.bin")
    assert failed[0]["error"]
    assert failed[0]["sha256"] is None, (
        "a file we could not process must not look like content the user holds"
    )


def test_an_unreachable_datasource_fails_the_whole_run(alice, core_env):
    """No listing means nothing can be attributed, so the run fails outright."""
    repo = core_env["core.repositories"]
    broken = repo.create_datasource(
        "alice", "s3", "gone", {"bucket": "no-such-bucket"}, None
    )
    directory, _ = repo.create_directory("alice", broken["id"], "whatever/")

    run = _sync(core_env, "alice", directory["id"])

    assert run["state"] == "failed"
    assert run["error"]
    assert run["files_seen"] == 0


# --- deleted at source ------------------------------------------------------


def test_a_file_removed_at_the_source_is_soft_deleted_and_unindexed(
    alice, core_env, s3
):
    repo = core_env["core.repositories"]
    vectors = core_env["core.vectors"]
    from core.hashing import sha256_bytes

    _sync(core_env, "alice", alice["dir"]["id"])
    sha = sha256_bytes(corpus_bytes("alice/lore/tom-bombadil.md"))
    assert repo.get_chunk_texts("alice", [f"{sha}:0"])

    s3.delete_object(Bucket=BUCKET, Key="alice/lore/tom-bombadil.md")
    run = _sync(core_env, "alice", alice["dir"]["id"])

    assert run["state"] == "succeeded"
    assert run["files_deleted"] == 1

    live = [f["provider_key"] for f in repo.list_files("alice")]
    assert not any(k.endswith("tom-bombadil.md") for k in live)

    # Attribution is retained, only its visibility changed.
    all_rows = repo.list_files("alice", include_deleted=True)
    deleted = [f for f in all_rows if f["provider_key"].endswith("tom-bombadil.md")]
    assert len(deleted) == 1 and deleted[0]["deleted_at"]

    # The vectors are gone from alice's collection, so answers stop citing it.
    assert all(h["sha256"] != sha for h in vectors.query("alice", "Bombadil", k=5))

    # The blob and its chunks persist: content, not attribution.
    assert repo.get_blob(sha) is not None
    assert repo.get_chunks(sha)


def test_a_file_that_reappears_at_the_source_is_revived(alice, core_env, s3):
    """Re-adding an object restores it through the same path that created it."""
    repo = core_env["core.repositories"]
    body = corpus_bytes("alice/lore/rivendell.md")

    _sync(core_env, "alice", alice["dir"]["id"])
    s3.delete_object(Bucket=BUCKET, Key="alice/lore/rivendell.md")
    _sync(core_env, "alice", alice["dir"]["id"])
    assert not any(
        f["provider_key"].endswith("rivendell.md") for f in repo.list_files("alice")
    )

    s3.put_object(Bucket=BUCKET, Key="alice/lore/rivendell.md", Body=body)
    run = _sync(core_env, "alice", alice["dir"]["id"])

    live = [f for f in repo.list_files("alice") if f["provider_key"].endswith("rivendell.md")]
    assert len(live) == 1
    assert live[0]["deleted_at"] is None
    assert live[0]["state"] == "indexed"
    assert run["files_new"] == 1


# --- changed content --------------------------------------------------------


def test_editing_a_file_at_the_source_reindexes_it(alice, core_env, s3):
    repo = core_env["core.repositories"]
    from core.hashing import sha256_bytes

    _sync(core_env, "alice", alice["dir"]["id"])
    old_sha = sha256_bytes(corpus_bytes("alice/lore/rivendell.md"))

    s3.put_object(
        Bucket=BUCKET,
        Key="alice/lore/rivendell.md",
        Body=b"# Rivendell\n\nThe Last Homely House East of the Sea.\n",
    )
    run = _sync(core_env, "alice", alice["dir"]["id"])

    assert run["files_new"] == 1, "changed content is re-indexed, not skipped"
    row = next(
        f for f in repo.list_files("alice") if f["provider_key"].endswith("rivendell.md")
    )
    assert row["sha256"] != old_sha, "identity follows the bytes"
