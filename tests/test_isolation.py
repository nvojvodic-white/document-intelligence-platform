"""Two users, one shared file, one unique file each.

This is the evidence for the data-safety claim, so it asserts the property from
several directions rather than once: no citation, no leaked text, no reachable
vector, no cross-tenant row, and no resolvable chunk id even when the id is
handed over directly.

The shared file matters as much as the unique ones. It proves the isolation is
not simply "users never overlap" - alice and bob hold byte-identical content,
share a cache entry for it, and still cannot see each other's anything.
"""
from __future__ import annotations

import pytest

from tests.conftest import corpus_bytes

ALICE_ONLY = "tom-bombadil.md"       # alice has it, bob does not
BOB_ONLY = "shelob.md"               # bob has it, alice does not
SHARED = "smaug.md"                  # byte-identical in both prefixes


@pytest.fixture()
def two_synced_users(s3, core_env, datasource_row):
    """Alice and bob, each with a synced directory, sharing one file's bytes."""
    repo = core_env["core.repositories"]
    sync = core_env["core.sync"]

    users = {}
    for user_id in ("alice", "bob"):
        repo.ensure_user(user_id, f"{user_id}@example.com")
        ds, _ = repo.create_datasource(
            user_id, "s3", "corpus", datasource_row["config"], None
        )
        directory, _ = repo.create_directory(user_id, ds["id"], f"{user_id}/lore/")
        repo.enqueue_run(user_id, directory["id"])
        users[user_id] = {"ds": ds, "dir": directory}

    # Drain the queue the way the worker would.
    while sync.poll_once() is not None:
        pass
    return users


def _sha(relative: str) -> str:
    from core.hashing import sha256_bytes

    return sha256_bytes(corpus_bytes(relative))


def test_shared_content_is_deduplicated_across_users(two_synced_users, core_env):
    """Both users hold the same bytes, and the second sync paid nothing."""
    repo = core_env["core.repositories"]
    shared = _sha(f"alice/lore/{SHARED}")

    assert repo.user_has_blob("alice", shared)
    assert repo.user_has_blob("bob", shared)

    # One blob row for content held by two users - identity is global.
    blob = repo.get_blob(shared)
    assert blob is not None
    assert "user_id" not in blob

    # Whichever user synced second reused vectors rather than recomputing them.
    runs = [
        repo.list_runs(u, two_synced_users[u]["dir"]["id"], limit=1)[0]
        for u in ("alice", "bob")
    ]
    assert sum(r["chunks_reused"] for r in runs) > 0, (
        "the second user's sync should have reused cached vectors for the "
        "byte-identical file"
    )


def test_each_user_sees_only_their_own_files(two_synced_users, core_env):
    repo = core_env["core.repositories"]

    alice_keys = {f["provider_key"] for f in repo.list_files("alice")}
    bob_keys = {f["provider_key"] for f in repo.list_files("bob")}

    assert any(k.endswith(ALICE_ONLY) for k in alice_keys)
    assert not any(k.endswith(ALICE_ONLY) for k in bob_keys)

    assert any(k.endswith(BOB_ONLY) for k in bob_keys)
    assert not any(k.endswith(BOB_ONLY) for k in alice_keys)

    # No key from one user's prefix appears in the other's attribution.
    assert not any(k.startswith("bob/") for k in alice_keys)
    assert not any(k.startswith("alice/") for k in bob_keys)


def test_retrieval_never_returns_the_other_users_content(
    two_synced_users, core_env
):
    """Ask each user about the other's unique document."""
    vectors = core_env["core.vectors"]
    alice_only_sha = _sha(f"alice/lore/{ALICE_ONLY}")
    bob_only_sha = _sha(f"bob/lore/{BOB_ONLY}")

    # Bob asks about Bombadil. He may get hits - nearest-neighbour search
    # always returns something from his own collection - but none of them may
    # be alice's content.
    bob_hits = vectors.query("bob", "Who is Tom Bombadil?", k=5)
    assert all(h["sha256"] != alice_only_sha for h in bob_hits)

    alice_hits = vectors.query("alice", "Tell me about Shelob the spider", k=5)
    assert all(h["sha256"] != bob_only_sha for h in alice_hits)


def test_chunk_ids_do_not_resolve_across_users(two_synced_users, core_env):
    """Isolation mechanism four, tested by handing over the id directly.

    Even a caller who already knows another user's chunk id gets nothing back,
    because text resolution joins user_blobs. This is what stops a stray vector
    from becoming visible text in an answer.
    """
    repo = core_env["core.repositories"]
    alice_only_sha = _sha(f"alice/lore/{ALICE_ONLY}")
    ids = [f"{alice_only_sha}:{i}" for i in range(5)]

    assert repo.get_chunk_texts("alice", ids), "alice should resolve her own chunks"
    assert repo.get_chunk_texts("bob", ids) == {}, (
        "bob must resolve none of alice's chunks, even given the exact ids"
    )


def test_shared_content_resolves_for_both_users(two_synced_users, core_env):
    """The flip side: possession, not ownership, is what grants access.

    Both users possess the shared bytes, so both resolve them. If this test
    failed the isolation would be over-broad - dedup would be pointless because
    the second user could never read what they synced.
    """
    repo = core_env["core.repositories"]
    shared = _sha(f"alice/lore/{SHARED}")
    ids = [f"{shared}:0"]

    assert repo.get_chunk_texts("alice", ids)
    assert repo.get_chunk_texts("bob", ids)


def test_scoped_reads_hide_other_users_rows(two_synced_users, core_env):
    """A known id belonging to another user reads as absent, not forbidden."""
    repo = core_env["core.repositories"]
    alice_dir = two_synced_users["alice"]["dir"]
    alice_ds = two_synced_users["alice"]["ds"]

    assert repo.get_directory("bob", alice_dir["id"]) is None
    assert repo.get_datasource("bob", alice_ds["id"]) is None
    assert repo.list_files("bob", alice_dir["id"]) == []

    runs = repo.list_runs("bob", alice_dir["id"], limit=5)
    assert runs == []


def test_removal_is_scoped_to_one_user(two_synced_users, core_env):
    """Alice removing the shared file must not disturb bob's copy."""
    repo = core_env["core.repositories"]
    vectors = core_env["core.vectors"]
    shared = _sha(f"alice/lore/{SHARED}")

    bob_before = vectors.collection_size("bob")
    alice_file = next(
        f for f in repo.list_files("alice") if f["sha256"] == shared
    )

    repo.soft_delete_file("alice", alice_file["id"])
    if not repo.user_still_references_blob("alice", shared):
        vectors.drop_blob_for_user("alice", shared)
        repo.remove_user_blob("alice", shared)

    assert not repo.user_has_blob("alice", shared)
    assert repo.user_has_blob("bob", shared), "bob still holds his own copy"
    assert vectors.collection_size("bob") == bob_before
    assert repo.get_chunk_texts("bob", [f"{shared}:0"]), "bob can still read it"

    # The content itself survives: it is content, not attribution.
    assert repo.get_blob(shared) is not None
    assert repo.get_chunks(shared)
