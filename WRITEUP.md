# Write-up

## What was built

The one path works end to end: log in, connect S3, browse, register a directory, sync it into a
per-user knowledge base, ask a question, get an answer grounded in those documents with citations
back to the source file. Re-sync is near-free, removal takes effect immediately, and the second
user sees none of the first user's documents.

Underneath that: a content-addressed data model (identity / attribution / possession in three
separate tables), a four-rung dedup ladder, a persisted sync state machine, four independent
isolation mechanisms, and 26 tests that need neither API keys nor network.

## What was cut

Full log with reasons in [CUTS.md](CUTS.md), written as each cut was made rather than reconstructed
at the end. The ones worth defending:

- **Tavily web search, `execute_code`, `read_file`.** Deleted, not parked. Grounding and isolation
  claims are unfalsifiable if the agent can answer from outside the indexed corpus, and on a
  multi-tenant box `read_file` serves one tenant's bytes to another while `execute_code` is plain
  RCE. Deleting them took the generic agent-session surface with it, since that existed only to
  host them.
- **A semantic response cache.** Not in the brief's cut list — I found it in the inherited code. It
  keyed answers on question text with no tenant scoping at all, so a hit would have served user A's
  answer to user B. The same bug shape appeared again in a retrieval `lru_cache` keyed on
  `(question, kind, k)`; both are gone.
- **RAGAS.** No room, and it scored a global corpus that is now per-user.
- **`semantic` / `pdr` / `turbovec` retrievers.** Each needs a second prebuilt index, so per-user
  routing would mean a second embedding pass per tenant at sync time.
- **Deployment, dashboards, pre-fork demo scripts.** All targeted a single-tenant service that no
  longer exists. Leaving them would have meant shipping config that deploys something this codebase
  cannot build.

## Did the plan survive the code?

Mostly, with three corrections worth naming.

**The plan was wrong about the corpus.** I wrote that the scraped files existed only on the build
machine. They are also published as a GitHub release asset — I found the fetch logic in the demo
script afterwards. The decision (commit a curated slice) did not change, because a clean clone
should not need a network round trip, but the stated reason did.

**Three decisions were taken the expensive way, and I flagged the cost up front.** The layout move,
the per-user routing rebuild, and two providers added roughly two hours to an eight-hour budget. I
named the fallback levers before starting — collapse routing to dense, stop the layout move where
it stands — and in the event did not need to pull either. The layout move came in inside its
estimate because doing it *before* writing new code made it a rename rather than a refactor.

**OpenRouter was reversed mid-build** on instruction; the inherited `ChatAnthropic` wiring stayed.
That removed a migration from the budget and absorbed some of the overrun above.

The one thing the plan understated: how much of the inherited retrieval stack was structurally
single-tenant. `get_vectorstore()` was an `lru_cache(maxsize=1)` singleton and every non-dense
retriever loaded one global pickle. "Keep the LangGraph graph" survived — the graph is intact, and
the classify → retrieve → grade → rewrite shape is unchanged — but everything under it was
rewritten.

## Deduplication

Identity is the sha256 of the raw bytes. Path and name are attribution, not identity.

The ladder takes the cheapest rung that applies: unchanged fingerprint means no download at all;
known bytes mean reused text and chunks; already-possessed bytes mean a new `files` row and nothing
else; new-to-this-user-but-known-globally means cached vectors are **copied** into that user's
collection rather than recomputed.

Measured by the tests, not asserted: a re-sync of an unchanged six-file directory reports 6 skipped,
0 bytes downloaded, 0 embedding calls. The same file registered under a second directory adds one
`files` row, 0 embeddings, and leaves the collection size unchanged. A second user syncing
byte-identical content embeds 0 chunks and copies 24 — and still ends up with 24 vectors of their
own.

The question a reviewer should push on is whether the shared cache leaks. It does not. If user B
syncs bytes identical to user A's file, B gets a cache hit on content **B supplied**. The cache is
keyed purely by content; nothing about A's attribution, filename, directory, or existence is
reachable from a sha256. Vectors are copied into B's own collection, never shared at read time. The
saving is the embedding API call, not the storage.

## Isolation

Four mechanisms, independent on purpose, because each is a single point of failure alone:

1. `user_id` is derived server-side from the verified token, in one dependency. Never from a body,
   query param, or header. The dev-login allowlist is fixed, so login cannot mint a token for an
   arbitrary string — otherwise every test below would be meaningless.
2. Every repository method takes `user_id` first and puts it in the WHERE clause. The property is
   checkable by reading signatures rather than auditing call sites. Another user's id reads as
   *absent*, not *forbidden*.
3. One Chroma collection per user, with the name built in exactly one function that accepts no
   caller-supplied name — **plus** a `user_id` metadata filter on every query, which still holds if
   the collection split is ever defeated.
4. Retrieval returns chunk ids; text resolves only through a `user_blobs` join. A stray vector
   cannot become visible text.

The evidence is `tests/test_isolation.py` and `tests/test_api_isolation.py`, written before the UI
rather than last. They assert from both directions (neither user can see the other's), at both
layers (repository and HTTP with real bearer tokens), and include the case that makes it meaningful:
alice and bob hold byte-identical content, share a cache entry for it, and still see nothing of each
other's. The suite runs with no keys and no network, because a data-safety test that only runs when
secrets are present is one that quietly stops running.

## What I did not verify

**Docker is not installed on this machine**, so `docker compose up` was never executed. The compose
file is written from the service contracts and validated as YAML, but the first real run is
unproven. Everything below it is exercised: the S3 provider runs against `moto` in CI rather than a
hand-written fake, so the boto3 code path itself is under test.

If one thing breaks on a reviewer's first run, my money is on service startup ordering or the web
container's API base URL — not on the sync or isolation logic, which the tests cover directly.

## The next eight hours

In priority order:

1. **Run the compose stack and fix what falls over.** It is the one unverified deliverable.
2. **Postgres.** The single-writer constraint is the ceiling on everything else. Claiming already
   has the right shape; the change is the driver plus the ability to run more than one worker.
3. **A concurrency test for claiming.** Two workers racing the same queued run is currently
   argued-for rather than proven, and it cannot be properly proven on SQLite anyway.
4. **PDF and DOCX extraction.** The seam exists and `extraction_version` already invalidates the
   cache correctly; this is a branch and a version bump.
5. **Chunk-level provenance in the answer.** Citations name the file today; they should carry the
   offset so a reviewer can jump to the exact span.
6. **Bring back an evaluation harness**, per-user this time, so retrieval quality changes are
   measured rather than felt.
7. **Real auth.** Replace `verify_token()` with an IdP verifier — deliberately a one-function
   change.
