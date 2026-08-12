# Demo corpus

The full Tolkien scrape, ~2,300 documents / 8.8 MB, seeded into LocalStack S3
at boot as bucket `tolkien-corpus`. Committed rather than downloaded so a clean
clone runs with no network access.

Provenance: Tolkien Gateway, the LotR Fandom wiki, and Wikipedia's Tolkien
articles, scraped by the pre-fork ingestion scripts and rendered to markdown as
`# {title}` / `Source: {url}` / body.

This is verbatim third-party wiki text under copyleft licences. See
[NOTICE.md](../NOTICE.md) for attribution and licence status — the Tolkien
Gateway terms in particular are unverified, and it is the largest share of the
corpus.

## Layout

```
library/                  2,284 documents, in tolkien_gateway/ fandom_lotr/ wikipedia/
library-archive/          20 byte-identical copies of library documents
alice-private/            6 documents held OUT of library
bob-private/              6 documents held OUT of library
```

**Both users register `library/`.** That is the point of the shape: one copy on
disk, two knowledge bases. Whoever syncs second reuses ~11,000 cached vectors
instead of paying to embed them again — the dedup claim at a scale where it
actually matters, rather than one shared file.

The 328 titles that appear in both Tolkien Gateway and Fandom are kept as two
documents under `library/{source}/`, because they are different bytes. Same
topic, different content, two blobs — which is correct: identity is the sha256,
not the title.

## What each prefix is for

| Prefix | Role |
|---|---|
| `library/` | The shared bulk. Registered by both users, so the second sync demonstrates cross-user dedup at ~11k chunks. |
| `library-archive/` | **Within-user dedup.** Byte-identical copies, so registering it as a second directory adds attribution rows and zero embeddings. |
| `alice-private/` | **Isolation probe.** Shelob, Galadriel, Moria, Rivendell, Celeborn, Elrond — held out of `library/`, so only alice can answer about them. |
| `bob-private/` | The same in the other direction: Gollum, Treebeard, Faramir, Beregond, Morgoth, Witch-king. |

A probe is only meaningful if the topic exists for *exactly one* user, which is
why the private sets are drawn only from single-source titles. A topic present
in two sources would leave its other copy in `library/`, both users could answer,
and the probe would prove nothing. Marquee subjects — Sauron, Gandalf, Aragorn,
Smaug and others — are deliberately pinned into `library/` so the shared
knowledge base does not look arbitrarily incomplete.

`tests/test_demo_corpus.py` asserts all of this, because these are properties of
the files rather than of the code and are easy to break by adding or renaming a
document.

## Costs, measured

From an actual run against the compose stack:

| Run | seen | new | embedded | reused | downloaded |
|---|---:|---:|---:|---:|---:|
| alice `library/` | 2284 | 2284 | **15,262** | 0 | 7.9 MB |
| alice `library/` again | 2284 | 0 | 0 | 0 | **0 bytes** |
| alice `library-archive/` | 16 | 16 | **0** | 1,213 | 0.7 MB |
| bob `library/` | 2284 | 2284 | **0** | **15,262** | 7.9 MB |

Alice's first sync took ~12 minutes and about $0.05 of `text-embedding-3-small`;
the worker processes one document at a time. Bob's sync of the same 2,284
documents embedded nothing at all.

Bob still downloads the 7.9 MB, because the bytes have to be fetched before they
can be hashed — the saving is the embedding call, which is the expensive part,
not the transfer.

`.gitattributes` marks `corpus/** -text` so git never rewrites line endings
underneath these files; without it the same document would hash differently on
Windows and Linux and dedup would silently stop working.

## Not the test corpus

`tests/fixtures/corpus/` is a separate 12-file set with the same properties in
miniature. Tests use that one: seeding 2,300 documents into a mock S3 for every
test would turn a 45-second suite into a several-minute one, and every expected
counter would churn whenever a document was added here.
