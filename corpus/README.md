# Corpus

A curated slice of Tolkien lore, seeded into LocalStack S3 at boot as bucket
`tolkien-corpus`. Committed rather than downloaded so a clean clone can run
`docker compose up` with no network access and no release-asset fetch.

Provenance: scraped from Tolkien Gateway and Wikipedia by the pre-fork
ingestion scripts, rendered to markdown as `# {title}` / `Source: {url}` /
body. The full 631-article scrape is not committed - it is gitignored under
`data/raw/` and published separately as a release asset. This slice is what the
walkthrough needs, not the whole corpus.

## Layout, and why these files

The prefixes map to S3 keys: `corpus/tolkien/alice/lore/` becomes
`s3://tolkien-corpus/alice/lore/`.

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
