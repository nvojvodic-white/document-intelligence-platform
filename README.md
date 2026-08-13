# Document Intelligence Platform

Multi-tenant document intelligence. You connect external storage, register a directory, sync it into
your own knowledge base, and then chat over it with citations pointing back at the source file.

Forked from `agent-platform`, which was agentic RAG over a Tolkien lore corpus. The corpus is still
Tolkien, but it lives in S3 as objects now instead of sitting on disk as fixtures, so connecting a
datasource is a real code path rather than a fixture pretending to be one.

- [docs/architecture.md](docs/architecture.md) for architecture, data flow and the sync lifecycle
- [NOTICE.md](NOTICE.md) for third-party corpus attribution and licence status

## Run it

```bash
git clone <repo> && cd document-intelligence-platform
cp .env.example .env        # then add your two API keys
docker compose up --build
```

Then open <http://localhost:5173>.

Compose starts six services: LocalStack standing in for S3, a one-shot seed that uploads
`corpus/tolkien` into the bucket, Chroma, the API, the sync worker and the web UI.

This was run from a clean build on Windows 11 with Docker Desktop 4.86 (engine 29.7.2). All four
images build, the seed uploads 2,312 objects, and the walkthrough below produces the numbers shown.

### Environment

| Variable | Required | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Synthesis, route classifier, relevance grader, HyDE. |
| `OPENAI_API_KEY` | yes | Embeddings only (`text-embedding-3-small`), at sync and query time. |
| `JWT_SECRET` | recommended | Signs dev-login tokens. Identity is read from the verified token, so this key is the whole trust boundary. |
| `PLATFORM_API_KEY` | no | Optional static gate in front of `/api/*`, on top of the per-user tokens. |
| `EMBEDDING_PROVIDER` | no | `openai` (default) or `hash`. **`hash` is a deterministic offline embedder for tests only.** |
| `DATASOURCE_PREFIXES` | no | Which prefixes a tenant may register. `{user_id}` expands per tenant; empty means unrestricted. |

Two providers because they do two different jobs. Chat completions go to Claude and embeddings go to
OpenAI. No single vendor here does both well, so you need both keys.

### Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

No keys and no network needed. The suite uses the deterministic embedder and `moto` for S3, which is
deliberate: the isolation test is the evidence behind the data-safety claim, and a test that only
runs when secrets happen to be present is a test that quietly stops running.

## The walkthrough

These numbers come from an actual run against the compose stack, not from estimates.

| # | Step | Result |
|---|---|---|
| 1 | Log in as **alice**, connect S3 (`tolkien-corpus`) | `201`, bucket probed before it's recorded |
| 2 | Browse | `/` shows `library/`, `library-archive/`, `alice-private/` |
| 3 | Register `library/` and sync | `succeeded`, seen 2284, new 2284, failed 0, **15,262 chunks embedded**, 7.9 MB, about 12 min |
| 4 | Ask *"Who was Sauron?"* | Cites `library/tolkien_gateway/sauron.md` and two others |
| 5 | Sync again | `succeeded`, **skipped 2284, 0 bytes, 0 embedding calls** |
| 6 | Register `library-archive/` and sync | new 16, **embedded 0, reused 1,213**, since those bytes are already in library |
| 7 | As **bob**, register `library/` and sync | new 2284, **embedded 0, reused 15,262** |
| 8 | Ask each about the other's private set | Neither one ever cites the other's `*-private/` documents |
| 9 | Bob hits alice's datasource, files or sync by id | `404`, `404`, `404` |
| 10 | Alice removes a document and asks again | Vectors dropped, citation gone |

Step 7 is the one to look at. Bob indexes the whole 2,284-document corpus without making a single
embedding call, because alice already paid for those vectors, and he still ends up with his own
private copy of all 15,262 of them. Step 6 is the same saving inside one account: the same bytes
under a second path cost sixteen attribution rows and nothing else.

Step 8 is worth reading slowly, because it's about documents and not about topics. Ask bob about
Shelob, which is an alice-private document, and he does answer. What he answers from is incidental
mentions in his own library documents, citing `frodo-baggins.md` and `shagrat.md`. He never touches
`alice-private/shelob.md`, and alice's answer to that same question cites the file directly and in
much more detail. That's the guarantee behaving correctly. Isolation is per document. Refusing to
use bob's own documents because they happen to mention something alice also holds would be censoring
his data, not protecting hers. At the 12-document scale the test fixture uses, the same probe comes
back with a flat "I don't have any documents covering that", which demos better but says less about
the actual property.

## API

| Method | Path | |
|---|---|---|
| POST | `/api/v1/auth/dev-login` | Issue a token for a fixed demo user |
| GET | `/api/v1/auth/me` | Identity derived from the token |
| POST | `/api/v1/datasources` | Connect S3. 201 when new, 200 if already connected |
| GET | `/api/v1/datasources/{id}/browse?path=` | One level of the bucket, read live |
| POST | `/api/v1/directories` | Register a prefix for syncing |
| POST | `/api/v1/directories/{id}/sync` | Queue a run, or return the one already in flight |
| GET | `/api/v1/runs/{id}` | Poll counters, read from the database |
| DELETE | `/api/v1/files/{id}` | Soft delete, dropping vectors on the last reference |
| POST | `/api/v1/rag/agent_query` | Ask, non-streaming |
| POST | `/api/v1/rag/agent_query_stream` | Ask, SSE, multi-turn |

Every route except dev-login takes the bearer token and derives `user_id` from it. Another user's id
returns 404.

## Architecture

```
apps/web/           React UI (one screen, in walkthrough order)
services/api/       FastAPI: auth, datasources, directories, sync trigger, chat
services/worker/    sync worker entrypoint
packages/core/      hashing, extraction, chunking, embedding cache, repositories
infra/              LocalStack seed
corpus/tolkien/     ~2,300 committed documents, uploaded into S3 at boot
```

The API and the worker are two entrypoints over one codebase. They're split so that a long sync
can't take the request path down with it, and so the worker can be restarted on its own.
Dependencies only run one way: `services/*` may import `core`, and `core` never imports `services`.

See [docs/architecture.md](docs/architecture.md) for the diagrams.

## Deduplication

"The same file" means the same bytes, specifically the sha256 of the raw object after download. Path
and name are attribution, not identity.

The ladder, cheapest rung first:

| Rung | Condition | Cost |
|---|---|---|
| 0 | `(provider_key, etag, size, mtime)` unchanged | One LIST call. Nothing is downloaded. |
| 1 | sha256 known globally at the current extraction version | Reuse text and chunks. No parsing. |
| 2 | `(user_id, sha256)` already in `user_blobs` | Insert the `files` row only. Vector store untouched. |
| 3 | New to this user, known globally | **Copy** cached vectors into this user's collection. No embedding call. |

`extraction_version` and `embedding_version` exist so that a parser or model change invalidates the
cache without anyone having to drop a table. Rows carry the version they were produced at, and
lookups filter on the current one.

**Isn't a shared cache a leak?** It's the right question to ask, and no. If user B syncs bytes that
are identical to user A's file, B gets a cache hit on the text and the vectors. B already possesses
those bytes, because B supplied them. The cache is keyed purely by content, and nothing about A's
attribution, filename, directory or existence can be reached from a sha256. The vectors get copied
into B's own collection and are never shared at read time. What's saved is the embedding API call,
not the storage.

## Isolation

Four independent mechanisms, since each one is a single point of failure on its own.

1. **`user_id` comes only from the verified token.** Never from a body, a query param or a header.
   One dependency (`app/deps.py::current_user_id`) is the only place the API learns who's calling.
2. **Every repository method takes `user_id` first**, and it reaches the WHERE clause. You can check
   the claim by reading the signatures in `packages/core/repositories.py` instead of auditing call
   sites. A scoped read of someone else's id comes back absent rather than forbidden, so ids don't
   leak existence.
3. **One Chroma collection per user** (`kb_user_{id}`), with the name built in exactly one function.
   No code path accepts a caller-supplied collection name. On top of that there's a `user_id`
   metadata filter on every query, which still holds if the first mechanism is ever defeated.
4. **Retrieval returns chunk ids, not text.** Text gets resolved by `get_chunk_texts(user_id, ids)`,
   which joins `user_blobs`. A vector that somehow surfaced from another tenant resolves to nothing
   and drops out before it can reach a prompt.

Datasource scope (`DATASOURCE_PREFIXES`) is a separate boundary. It limits which prefixes a tenant
can browse and register, and it's applied server-side at connect time. The four mechanisms above
keep knowledge bases apart from each other; scope decides what a tenant is allowed to pull in at
all. Without it, a single demo bucket with prefixes named after their intended owner is just a
naming convention. Alice could register `bob-private/` and those documents would then genuinely be
hers, which is correct behaviour but misleading in a system whose whole point is data safety.

`tests/test_isolation.py` and `tests/test_api_isolation.py` assert all of this, from the repository
layer and over HTTP with real bearer tokens.

Three things got deleted rather than fixed, because they made the grounding claim unfalsifiable: the
Tavily web search route, the `execute_code` and `read_file` agent tools (arbitrary RCE and arbitrary
path reads on a multi-tenant box), and a semantic response cache that keyed answers on question text
with no tenant scoping at all.

## Stack deviations, and what they cost

The brief suggests Pinecone with Postgres or NeonDB. This build uses **Chroma and SQLite**, because
both run offline inside compose without any external accounts, which is what protects the "clean
clone, one command" requirement.

That choice isn't free:

- **SQLite is weak under concurrent writers.** So the sync worker is single-process, WAL is enabled
  so readers never block behind the writer, and run claiming is a single conditional `UPDATE`
  guarded by a partial unique index.
- **Postgres is the first thing to swap in** if a second worker is ever needed. Claiming already has
  the right shape for it. What changes is the driver and the ability to run more than one.
- **Chroma runs as a service rather than a shared directory.** Its in-process client assumes one
  process owns the files, so the API reading while the worker was writing failed intermittently with
  `Error executing plan: Internal error: Error finding id`. It only happened during a sync, which is
  about the worst way for a bug to show up. `CHROMA_HOST` switches to the service, and on-disk mode
  is kept for tests and single-process runs.

## Other trade-offs

- **Per-user routing rebuild.** The pre-fork build had seven retriever kinds. `semantic`, `pdr` and
  `turbovec` each needed a second prebuilt index, which per-user means a second embedding pass per
  tenant at sync time. What's kept is dense, sparse (BM25 over the user's own chunk rows), hybrid
  (RRF) and HyDE, so there's still routing variety at no extra embedding cost.
- **Extraction handles markdown and text only.** PDF and DOCX parsing would have cost an hour to
  serve documents this build doesn't have. The seam is in `extract()`, so a new type is one branch
  plus a version bump.
- **Dev login rather than Clerk or Ory.** What isn't cut is the property they'd provide: identity
  travels in a verified signed token. Swapping in a real IdP means replacing `verify_token()`.
- **The corpus is committed rather than fetched**, roughly 2,300 documents in `corpus/tolkien`, so a
  clean clone runs offline. Tests use a separate 12-file fixture to stay fast.
- **RAGAS evaluation is cut.** It scored a global corpus that's now per-user, so the stored history
  wouldn't be comparable anyway.
- **Observability is removed rather than disabled.** The Prometheus counters measured agent sessions
  that no longer exist, and the traces went to a Jaeger that was deleted along with the dashboards.
  Dropping both took eleven dependencies out of the image. The time to add it back is when there's
  something worth watching.
