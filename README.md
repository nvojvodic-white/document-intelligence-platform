# Document Intelligence Platform

Multi-tenant document intelligence. A user connects external storage, registers a directory, syncs
it into their own knowledge base, and chats over it with citations back to the source file.

Forked from `agent-platform` (agentic RAG over a Tolkien lore corpus). The corpus stays Tolkien, but
it now lives in S3 as objects rather than on disk as fixtures, so "connect a datasource" is a real
path rather than a fixture pretending to be one.

- [PLAN.md](PLAN.md) — the plan, written before any code
- [WRITEUP.md](WRITEUP.md) — what was built, what was cut, whether the plan survived
- [CUTS.md](CUTS.md) — every cut, logged when it was made
- [docs/architecture.md](docs/architecture.md) — architecture, data flow, sync lifecycle

## Run it

```bash
git clone <repo> && cd agent-platform
cp .env.example .env        # then add your two API keys
docker compose up --build
```

Then open <http://localhost:5173>.

Compose brings up five services: LocalStack (S3), a one-shot seed that uploads `corpus/tolkien`
into the bucket, the API, the sync worker, and the web UI.

> **Not verified end to end.** Docker is not installed on the machine this was built on, so the
> compose stack is written from the service contracts rather than confirmed against a live run.
> Everything below the compose layer *is* exercised: 26 tests cover the sync engine, the dedup
> ladder, and isolation, including the S3 provider itself via `moto`. Treat first `docker compose
> up` as the one unproven step.

### Environment

| Variable | Required | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Synthesis, route classifier, relevance grader, HyDE. |
| `OPENAI_API_KEY` | yes | Embeddings only (`text-embedding-3-small`), at sync and query time. |
| `JWT_SECRET` | recommended | Signs dev-login tokens. Identity is read from the verified token, so this is the whole trust boundary. |
| `PLATFORM_API_KEY` | no | Optional static gate in front of `/api/*`, on top of per-user tokens. |
| `EMBEDDING_PROVIDER` | no | `openai` (default) or `hash`. **`hash` is a deterministic offline embedder for tests only.** |

Two providers, because they do two different things: chat completions go to Claude, embeddings go
to OpenAI. There is no single vendor here that does both well, so the environment needs both keys.

### Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

No keys and no network required — the suite uses the deterministic embedder and `moto` for S3.
That is deliberate: the isolation test is the evidence for the data-safety claim, and a test that
only runs when secrets are present is a test that quietly stops running.

## The walkthrough

1. Log in as **alice**. Connect S3 (bucket `tolkien-corpus`).
2. Browse to `alice/lore/` and register it.
3. Sync. Counters move: 6 seen, 6 new, ~150 chunks embedded.
4. Ask *"Who is Tom Bombadil?"* — the answer cites `alice/lore/tom-bombadil.md`.
5. Sync again. **6 skipped, 0 bytes downloaded, 0 embedding calls.**
6. Register `alice/archive/` and sync. One new file row, **0 embeddings** — same bytes as
   `alice/lore/mithril.md`.
7. Log in as **bob**, connect, register `bob/lore/`, sync. His `smaug.md` is byte-identical to
   alice's, so it is **reused from cache, not re-embedded** — and still written into bob's own
   collection.
8. Ask bob *"Who is Tom Bombadil?"* — nothing comes back. That document is alice's.
9. Remove a file as alice and re-ask. The answer changes and the citation is gone.

## Architecture

```
apps/web/           React UI (one screen, walkthrough order)
services/api/       FastAPI: auth, datasources, directories, sync trigger, chat
services/worker/    sync worker entrypoint
packages/core/      hashing, extraction, chunking, embedding cache, repositories
infra/              LocalStack seed
corpus/tolkien/     committed corpus slice, uploaded into S3 at boot
```

API and worker are two entrypoints over one codebase. They are split so a long sync cannot take
the request path down with it, and so the worker can be restarted independently. Dependencies run
one way only: `services/*` may import `core`, `core` never imports `services`.

See [docs/architecture.md](docs/architecture.md) for the diagrams.

## Deduplication

**"The same file" means the same bytes** — the sha256 of the raw object after download. Path and
name are attribution, not identity.

The ladder, cheapest rung first:

| Rung | Condition | Cost |
|---|---|---|
| 0 | `(provider_key, etag, size, mtime)` unchanged | One LIST call. No download at all. |
| 1 | sha256 known globally at the current extraction version | Reuse text and chunks. No parsing. |
| 2 | `(user_id, sha256)` already in `user_blobs` | Insert the `files` row only. Vector store untouched. |
| 3 | New to this user, known globally | **Copy** cached vectors into this user's collection. No embedding call. |

`extraction_version` and `embedding_version` exist so a parser or model change invalidates the
cache without anyone dropping a table — rows carry the version they were produced at, and lookups
filter on the current one.

**"Isn't a shared cache a leak?"** No, and this is the question worth being precise about. If user
B syncs bytes identical to user A's file, B gets a cache hit on text and vectors. B already
possesses those bytes — they supplied them. The cache is keyed purely by content, and nothing about
A's attribution, filename, directory, or existence is reachable from a sha256. The vectors are
*copied* into B's own collection, never shared at read time. What is saved is the embedding API
call, not the storage.

## Isolation

Four independent mechanisms, because each is a single point of failure alone:

1. **`user_id` comes only from the verified token.** Never from a body, query param, or header.
   One dependency (`app/deps.py::current_user_id`) is the only place the API learns who is calling.
2. **Every repository method takes `user_id` first**, and it reaches the WHERE clause. The claim is
   checkable by reading signatures in `packages/core/repositories.py` rather than auditing call
   sites. A scoped read of someone else's id returns *absent*, not *forbidden*, so ids do not leak
   existence.
3. **One Chroma collection per user** (`kb_user_{id}`), and the name is built in exactly one
   function. No code path accepts a caller-supplied collection name. **Plus** a `user_id` metadata
   filter on every query, which still holds if the first mechanism is ever defeated.
4. **Retrieval returns chunk ids, not text.** Text is resolved by `get_chunk_texts(user_id, ids)`,
   which joins `user_blobs`. A vector that somehow surfaced from another tenant resolves to nothing
   and is dropped before it can reach a prompt.

`tests/test_isolation.py` and `tests/test_api_isolation.py` assert this from both the repository
layer and over HTTP with real bearer tokens.

Three things were deleted rather than fixed, because they made the grounding claim unfalsifiable:
the Tavily web search route, the `execute_code`/`read_file` agent tools (arbitrary RCE and
arbitrary path reads on a multi-tenant box), and a semantic response cache keyed on question text
with no tenant scoping at all.

## Stack deviations, and what they cost

The brief suggests Pinecone plus Postgres/NeonDB. This uses **Chroma plus SQLite**, because both
run offline inside compose with no external accounts, which is what protects the "clean clone, one
command" requirement.

That choice is not free:

- **SQLite is weak under concurrent writers.** The sync worker is therefore single-process, WAL is
  enabled so readers never block behind the writer, and run claiming is a single conditional
  `UPDATE` guarded by a partial unique index.
- **Postgres is the first thing to swap in if a second worker is ever needed.** Claiming already
  has the right shape for it; what changes is the driver and the ability to run more than one.
- Chroma is a local persistent client on a shared volume. It is fine for a demo-sized corpus and
  would be the second thing replaced.

## Other trade-offs

- **Per-user routing rebuild.** The pre-fork build had seven retriever kinds; `semantic`, `pdr`,
  and `turbovec` each needed a second prebuilt index, which per-user means a second embedding pass
  per tenant at sync time. Kept: dense, sparse (BM25 over the user's own chunk rows), hybrid (RRF),
  and HyDE — routing variety at no extra embedding cost.
- **Extraction is markdown and text only.** PDF/DOCX parsing would have cost an hour to serve
  documents this build does not have. The seam is in `extract()`, so a new type is a branch plus a
  version bump.
- **Dev login, not Clerk/Ory.** What is not cut is the property they provide: identity travels in a
  verified signed token. Swapping in a real IdP means replacing `verify_token()`.
- **The corpus is committed, not fetched.** A curated 12-file slice lives in `corpus/tolkien` so a
  clean clone runs offline. The full 631-article scrape stays gitignored.
- **RAGAS evaluation is cut.** It scored a global corpus that is now per-user, so the stored
  history would not be comparable anyway.
