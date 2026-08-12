"""Invariants of the committed demo corpus.

The walkthrough's claims are properties of the corpus, not of the code: within-
user dedup needs byte-identical copies under two prefixes, and the isolation
probe needs a topic that exists for exactly one user. Both are easy to break by
adding or renaming a document, and neither failure is visible until someone
runs the demo and finds it no longer demonstrates anything.

These read the files directly. No database, no S3, no embedder.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict

import pytest

from tests.conftest import DEMO_CORPUS

LIBRARY = DEMO_CORPUS / "library"
ARCHIVE = DEMO_CORPUS / "library-archive"
ALICE_PRIVATE = DEMO_CORPUS / "alice-private"
BOB_PRIVATE = DEMO_CORPUS / "bob-private"


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _title(path) -> str:
    """The '# Title' line each document starts with."""
    return path.read_text(encoding="utf-8").split("\n", 1)[0].lstrip("# ").strip()


@pytest.fixture(scope="module")
def library_hashes() -> set[str]:
    return {_sha(p) for p in LIBRARY.rglob("*.md")}


@pytest.fixture(scope="module")
def library_titles() -> set[str]:
    return {_title(p) for p in LIBRARY.rglob("*.md")}


def test_the_corpus_is_actually_present():
    assert DEMO_CORPUS.is_dir(), "the demo corpus is missing"
    assert sum(1 for _ in LIBRARY.rglob("*.md")) > 2000, (
        "library should hold the bulk of the scrape"
    )


def test_archive_is_byte_identical_to_library(library_hashes):
    """Within-user dedup: registering the archive must cost zero embeddings."""
    archive = list(ARCHIVE.glob("*.md"))
    assert archive, "the archive prefix is empty"
    for path in archive:
        assert _sha(path) in library_hashes, (
            f"{path.name} is not byte-identical to any library document, so "
            "registering the archive would re-embed it instead of deduplicating"
        )


def test_private_documents_are_absent_from_the_shared_library(library_titles):
    """Isolation probes only work if the topic exists for exactly one user."""
    for private in (ALICE_PRIVATE, BOB_PRIVATE):
        documents = list(private.glob("*.md"))
        assert documents, f"{private.name} is empty"
        for path in documents:
            assert _title(path) not in library_titles, (
                f"{path.name} also exists in library/, so both users can answer "
                "about it and the isolation probe proves nothing"
            )


def test_the_two_private_sets_do_not_overlap():
    alice = {_title(p) for p in ALICE_PRIVATE.glob("*.md")}
    bob = {_title(p) for p in BOB_PRIVATE.glob("*.md")}
    assert alice and bob
    assert not (alice & bob), f"a document cannot be private to both: {alice & bob}"


def test_marquee_topics_stay_in_the_shared_library(library_titles):
    """A headline subject held out of library/ makes the shared knowledge base
    look arbitrarily incomplete - Sauron in particular, which is the first
    thing anyone asks about."""
    for title in ("Sauron", "Gandalf", "Aragorn", "Smaug"):
        assert title in library_titles, f"{title} should be answerable by both users"


def test_documents_are_utf8_with_a_title_and_a_body():
    """Extraction rejects empty or non-UTF-8 documents; a corpus that trips
    that would sync to a pile of per-file failures."""
    sampled = sorted(LIBRARY.rglob("*.md"))[::200] + list(ALICE_PRIVATE.glob("*.md"))
    for path in sampled:
        raw = path.read_bytes()
        text = raw.decode("utf-8")  # raises if the corpus is not valid UTF-8
        assert text.startswith("# "), f"{path} has no title line"
        assert len(text) > 200, f"{path} is too short to be worth indexing"
        assert b"\r\n" not in raw, (
            f"{path} has CRLF line endings; the same document would then hash "
            "differently on Windows and Linux and dedup would silently stop working"
        )


def test_no_duplicate_filenames_within_a_prefix():
    """Slugified titles can collide once accents are stripped; a collision
    would mean one document silently overwriting another."""
    for directory in (LIBRARY, ARCHIVE, ALICE_PRIVATE, BOB_PRIVATE):
        by_parent = defaultdict(list)
        for path in directory.rglob("*.md"):
            by_parent[path.parent].append(path.name)
        for parent, names in by_parent.items():
            assert len(names) == len(set(names)), f"duplicate filenames in {parent}"
