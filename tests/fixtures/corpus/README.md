# Test fixture corpus

Twelve documents, used only by the test suite. **This is not the demo corpus** -
that is `corpus/tolkien/`, ~2,300 documents seeded into LocalStack at boot.

Tests use this smaller set deliberately. Seeding 2,300 documents into a mock S3
for every test would turn a 45-second suite into a several-minute one, and every
expected counter in `test_sync.py` would churn whenever a document was added to
the demo corpus. This fixture carries the same properties in miniature, so the
assertions stay small, fast, and stable.

Provenance: scraped from Tolkien Gateway and Wikipedia by the pre-fork
ingestion scripts, rendered to markdown as `# {title}` / `Source: {url}` / body.

## Layout, and why these files

Seeded into a mock bucket by `tests/conftest.py`, mirroring the demo layout:
`alice/lore/` becomes `s3://tolkien-corpus/alice/lore/`.

```
alice/lore/      gandalf, frodo-baggins, tom-bombadil, mithril, rivendell, smaug
alice/archive/   mithril            <- same bytes as alice/lore/mithril.md
bob/lore/        aragorn, galadriel, shelob, moria, smaug
                                    <- smaug is the same bytes as alice's
```

Four of these placements are load bearing:

| File | Role in the demo |
|------|------------------|
| `alice/archive/mithril.md` | **Within-user dedup.** Byte-identical to `alice/lore/mithril.md`. Registering both directories yields two `files` rows, one `blobs` row, and zero extra embedding calls. |
| `bob/lore/smaug.md` | **Cross-user dedup.** Byte-identical to `alice/lore/smaug.md`. Bob's sync copies cached vectors instead of recomputing them - and still writes them into bob's own collection, so nothing is shared at read time. |
| `alice/lore/tom-bombadil.md` | **Isolation probe.** Alice has it, bob does not. Asking bob about Bombadil must return no citation and no leaked content. |
| `bob/lore/shelob.md` | The same probe in the other direction, so isolation is not accidentally one-way. |

Identity is the sha256 of the raw bytes, so the duplicate pairs are byte copies
rather than re-renders. `.gitattributes` marks `corpus/** -text` to stop git
rewriting line endings underneath them; without that the same document would
hash differently on Windows and Linux and the dedup demo would silently stop
demonstrating anything.
