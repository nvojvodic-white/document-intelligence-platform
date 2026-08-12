# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this repo is

A fork of `agent-platform` (agentic RAG over a Tolkien lore corpus) repurposed as a take-home
submission: a **multi-tenant document intelligence platform**. A user connects external storage,
registers a directory, syncs it into their own knowledge base, and chats over it.

The corpus stays Tolkien. It is seeded into LocalStack S3 as objects rather than committed as
loose files, so "connect a datasource" is a real path and not a fixture pretending to be one.

## Hard constraints

- **8 hours of actual work.** At 8 hours, stop and write up what is missing. Stopping mid-feature
  is an accepted outcome; leaving something silently broken is not.
- **Monorepo**, runs from a clean clone with `docker compose up`.
- **One path must work end to end**: connect a datasource, sync a directory, ask a question, get an
  answer grounded in those documents, with citations.
- **Commit as you go.** Small commits with real messages. A single squashed drop is scored down.
- Graded on: scoping, correctness, design judgement, data safety, code quality, communication.
  UI polish is explicitly not scored. Do not spend time on it.
- LLM calls go through **OpenRouter**. Ping the reviewers before starting the LLM-dependent work so
  they can top up credits.

## Inherited vs new

Keep from `agent-platform`, do not rewrite:

- FastAPI service skeleton, config and env handling, logging
- LangGraph retrieval graph and the SSE streaming chat endpoint
- Chroma vector store wrapper
- SQLite conversation memory
- React/TypeScript frontend shell
- Tolkien corpus

Remove or park:

- RAGAS evaluation harness. No time. Mention it in the write-up as a deliberate cut.
- OpenTelemetry/Jaeger. Leave it in only if it costs nothing. Do not debug it.
- **Tavily web search: delete the route.** Grounding and isolation claims are unfalsifiable if the
  agent can answer from outside the indexed corpus. This is a correctness requirement, not cleanup.

New surface to build:

- S3 datasource connect (LocalStack)
- Directory browse and register
- Sync worker plus a persisted state machine
- Content-addressed deduplication layer
- Per-user isolation on every read path
- Citations back to the source file

## Stack deviations from the brief

The brief suggests Pinecone plus Postgres/NeonDB. This build uses **Chroma plus SQLite**, because
both are already wired and run offline inside compose with no external accounts, which protects the
"clean clone, one command" requirement.

Cost of that choice, and it must be stated in the README rather than glossed:

- SQLite is weak under concurrent writers. The sync worker is therefore single-process, WAL is
  enabled, and run claiming is a single conditional `UPDATE` guarded by a partial unique index.
- Postgres is the first thing to swap in if a second worker is ever needed. Say so.

## Layout

```
apps/web/           React/TS frontend (inherited shell)
services/api/       FastAPI: auth, datasources, directories, sync trigger, chat
services/worker/    sync worker entrypoint (same Python package, separate process)
packages/core/      hashing, extraction, chunking, embedding cache, repositories
infra/              docker-compose, LocalStack seed script
corpus/tolkien/     source files, uploaded into LocalStack at boot
```

API and worker share one codebase and two entrypoints. The split exists so a long sync cannot take
the request path down with it and so the worker can be restarted independently. That is the reason
to give when asked why it was split there; do not split further without a similar reason.

## Data model

This is the load-bearing part of the submission. Get it right before anything else.

- `users`
- `datasources` (user_id, kind, config, secret reference)
- `directories` (datasource_id, path, status)
- `blobs` (**sha256 primary key**, byte_size, extracted_text_ref, extraction_version,
  embedding_version). Global and content-addressed. **No user_id on this table.**
- `files` (user_id, directory_id, provider_key, etag, size, mtime, sha256 FK, state, deleted_at).
  Unique on (user_id, datasource_id, provider_key). This is the attribution layer.
- `user_blobs` (user_id, sha256), unique. Means "this content is in this user's knowledge base".
- `chunks` (sha256, ordinal, text)
- `sync_runs` (directory_id, state, counters, heartbeat_at, started_at, finished_at)

Vector store: **one Chroma collection per user**, named `kb_user_{id}`, constructed only inside a
repository function that takes user_id. No code path may accept a caller-supplied collection name.

## Deduplication

**"The same file" means the same bytes: sha256 of the raw object after download.** Path and name
are attribution, not identity.

Cheap path first: skip the download entirely when (provider_key, etag, size, mtime) matches the
recorded row. A re-sync of an unchanged directory is one LIST call, zero downloads, zero
extractions, zero embedding calls.

Then, in order:

1. sha256 already in `blobs` at the current `extraction_version`: reuse extracted text and chunks.
2. (user_id, sha256) already in `user_blobs`: insert the new `files` row only. Nothing touches the
   vector store. This covers the same file appearing in a second directory.
3. New for this user but known globally: **copy** cached embedding vectors, keyed by
   (sha256, ordinal, embedding_version), into that user's collection. Do not recompute, do not
   share rows. The saving is the embedding API call; the vectors still live separately per user.

`extraction_version` and `embedding_version` exist so a model or parser change invalidates the cache
without dropping tables.

Note for the README, because reviewers will push on it: if user B syncs bytes identical to user A's
file, B gets a cache hit on text and vectors. That is not a leak, because B already possesses those
bytes. The cache is keyed by content B supplied, and nothing about A's attribution, filename, or
directory is reachable from it.

## Isolation

Non-negotiable rules:

1. `user_id` is derived server-side from the verified token. Never read from a body, query param, or
   client-controlled header.
2. Every repository method takes `user_id` as its first argument. There are no unscoped queries.
3. Collection-per-user **and** a `user_id` metadata filter on every retrieval. Two independent
   mechanisms, on purpose.
4. Retrieval returns chunk ids; the answer builder resolves text through a user-scoped lookup, so a
   stray vector cannot become visible text.
5. Committed test: two users, one byte-identical shared file, one unique file each. Ask user A about
   user B's unique file and assert no citation and no leaked content. This test is the evidence for
   the data safety score, so write it early rather than last.

## Sync state machine

States: `idle`, `queued`, `running`, `succeeded`, `partial`, `failed`. Every transition is a
database write. No in-memory state machine.

- **Double click**: partial unique index on `sync_runs(directory_id)` where state is `queued` or
  `running`. The second POST returns the existing run and reports "already in progress".
- **Refresh mid-run**: counters (files_seen, files_new, files_skipped, files_failed) are written per
  file, so progress is read from the database, not from process memory.
- **Dead worker**: running runs write `heartbeat_at`. A run whose heartbeat is older than two
  minutes is reclaimed as `failed` and may be restarted.
- **One bad file**: per-file failures increment a counter and do not abort the run. The run ends
  `partial`.
- **Deleted at source**: after listing, any non-deleted file row absent from the listing is soft
  deleted and its vectors dropped from that user's collection. Same code path as manual removal.
- **Nothing new**: a `succeeded` run with files_new = 0. Honest reporting falls out of the states
  rather than being special-cased in the UI.

## Removal

Soft delete the `files` row. Drop that content's chunks from the user's collection only when no
other non-deleted file row of the same user references the same sha256. `blobs` and the embedding
cache persist; they are content, not attribution. Answers stop citing the file immediately.

## Demo seed

LocalStack bucket `tolkien-corpus` with prefixes:

- `alice/` a set of Tolkien files, one of them repeated under a second prefix to demonstrate
  within-user dedup
- `bob/` different files plus one byte-identical copy of an alice file to demonstrate cross-user
  dedup with isolation intact

Two hardcoded users. A dev login endpoint issues a signed JWT; user identity is read from the token
only.

Walkthrough order: login as alice, connect S3, browse, register a directory, sync and watch counters
move, ask a question and show the citation, re-sync and show near-zero cost, log in as bob, ask
about alice's unique file and show nothing comes back, remove a file and show the answer changes.

## Working rules

- The timebox is the product. If a task runs roughly 30 minutes past estimate, cut it.
- Maintain `CUTS.md` and append the moment something is cut, with a one-line reason. Do not
  reconstruct it at the end from memory.
- Do not refactor inherited code that works.
- No new dependency unless it saves more than an hour.
- Every non-obvious trade-off gets one line in the README, not a paragraph.
- A narrow working path beats broad stubs, every time.

## Deliverables

1. Plan sent before any code is written (half a page: build, cut, why the kept items matter,
   ambiguities).
2. Repository with incremental commits.
3. README: setup, environment variables, clean-clone run instructions, stack deviations, trade-offs.
4. Diagram: architecture, data flow, sync lifecycle.
5. One-page write-up: what was built and cut, whether the plan survived the code, deduplication,
   isolation, next eight hours.
6. Five to ten minute walkthrough.

## Default cuts

Cut unless the core path is finished early, each with a one-line reason in the write-up:
Clerk and Ory auth, Google Drive and Azure Blob providers, multiple chats, context compaction,
RAGAS evaluation, tracing dashboards, deployment, any UI work beyond usable.
