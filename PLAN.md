# Plan

Multi-tenant document intelligence platform, forked from `agent-platform`. Sent before code.

## The one path that must work

Log in as alice → connect S3 → browse → register a directory → sync → ask a question → get an
answer grounded in alice's documents, with citations back to the source file. Everything else is
negotiable against that.

## Build

1. **Data model first.** `users`, `datasources`, `directories`, `blobs` (sha256 PK, no user_id),
   `files` (attribution: user_id, directory_id, provider_key, etag, size, mtime, sha256 FK),
   `user_blobs`, `chunks`, `sync_runs`. Every repository method takes `user_id` first.
2. **Auth.** Dev login issues a signed JWT. `user_id` is derived from the verified token only —
   never from a body, query param, or header.
3. **Isolation test, written early.** Two users, one byte-identical shared file, one unique file
   each. Ask A about B's unique file; assert no citation, no leaked content. This is the evidence
   for the data-safety score, so it lands before the pipeline it tests.
4. **S3 datasource** against LocalStack: connect, browse prefixes, register a directory.
5. **Sync worker**, separate process, same codebase. Persisted state machine
   (`idle→queued→running→succeeded|partial|failed`), single conditional `UPDATE` for claiming,
   heartbeat reclaim at 2 minutes, per-file counters written as it goes.
6. **Dedup ladder.** Cheap path first: (provider_key, etag, size, mtime) match ⇒ no download at
   all. Then sha256 in `blobs` ⇒ reuse text and chunks; (user_id, sha256) in `user_blobs` ⇒ insert
   the `files` row only; known globally but new to this user ⇒ *copy* cached vectors into that
   user's collection. Identity is bytes, not path.
7. **Per-user retrieval.** One Chroma collection per user, `kb_user_{id}`, built only inside a
   repository function that takes user_id, plus a `user_id` metadata filter on every read. Two
   mechanisms on purpose. Retrieval returns chunk ids; text is resolved through a user-scoped
   lookup so a stray vector cannot become visible text.
8. **Citations** back to the source file, and a minimal UI to drive the walkthrough.

## Cut

- **RAGAS harness** — no time, and it measures a corpus that is now per-user.
- **Tavily web search route** — deleted, not parked. Grounding and isolation claims are
  unfalsifiable if the agent can answer from outside the indexed corpus.
- **`execute_code` and `read_file` tools** — not in the brief's cut list, but `read_file` reads
  any path on disk and `execute_code` is raw `exec`. On a multi-tenant platform they are a worse
  hole than Tavily. Deleted with it.
- OpenTelemetry/Jaeger stays only while it costs nothing; it will not be debugged.
- Clerk/Ory, Google Drive and Azure providers, multiple chats, context compaction, deployment,
  any UI beyond usable.

## Why the kept items matter

The graded axes are data safety and design judgement, and both live in the same place: identity is
content (sha256), attribution is a separate table (`files`), and possession is a third
(`user_blobs`). Keeping those three apart is what makes it possible to share a cache across tenants
without sharing anything a tenant did not already supply. The sync state machine is persisted for
the same reason — every question a reviewer will ask (double click, refresh mid-run, dead worker,
one bad file, deleted at source, nothing new) is answered by reading a row, not by trusting process
memory.

## Ambiguities, resolved

- **OpenRouter has no embeddings API.** Decision: chat LLM calls go through OpenRouter; embeddings
  stay on OpenAI `text-embedding-3-small`. Cost: a clean clone needs two keys, not one. Stated in
  the README rather than glossed.
- **The corpus is not in the repo.** `data/` is gitignored, so the 631 scraped Tolkien files exist
  only on the machine that scraped them. A curated slice is committed to `corpus/tolkien/` and
  seeded into LocalStack at boot, including the two files the dedup demo needs: one repeated under
  a second alice prefix, one byte-identical across alice and bob.
- **Inherited retrieval is structurally single-tenant.** `get_vectorstore()` is a singleton over
  one collection and the sparse/hyde/pdr/semantic retrievers all load one global chunks pickle.
  Decision: rebuild the routing per-user rather than collapse to dense. Cost: the sync worker now
  owns a second per-user index and its invalidation. This is the largest schedule risk in the plan;
  if it runs 30 minutes past estimate it falls back to per-user dense only, logged in `CUTS.md`.

## Schedule risk, stated up front

The three decisions above (spec layout move, per-user routing rebuild, two providers) add roughly
two hours to an eight-hour budget. The fallback levers, in the order they will be pulled: routing
collapses to dense; the layout move stops where it stands; the UI stays at the minimum that drives
the walkthrough. The core path ships regardless — a narrow working path beats broad stubs.
