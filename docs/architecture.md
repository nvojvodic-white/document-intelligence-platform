# Architecture

Three diagrams: what runs, how a document becomes an answer, and how a sync run moves between
states.

## Services

Two entrypoints over one codebase. The split exists so a long sync cannot take the request path
down with it, and so the worker can be restarted independently — not because the two are different
applications.

```mermaid
flowchart LR
    subgraph browser[Browser]
        UI[apps/web<br/>React]
    end

    subgraph api_svc[services/api]
        API[FastAPI<br/>auth · datasources · directories · chat]
    end

    subgraph worker_svc[services/worker]
        W[sync worker<br/>single process]
    end

    subgraph core_pkg[packages/core - shared]
        REPO[(repositories<br/>user_id first)]
        VEC[vectors<br/>kb_user_id]
        PIPE[hash · extract · chunk]
    end

    subgraph state[shared volume]
        DB[(SQLite<br/>WAL)]
        CH[(Chroma<br/>one collection per user)]
        TS[(text store)]
    end

    S3[(LocalStack S3<br/>tolkien-corpus)]
    LLM[Claude<br/>synthesis · classify · grade]
    EMB[OpenAI<br/>embeddings]

    UI -->|Bearer token| API
    API --> REPO
    API --> VEC
    API --> LLM
    W --> REPO
    W --> PIPE
    W --> VEC
    W -->|LIST · GET| S3
    VEC --> EMB
    REPO --> DB
    VEC --> CH
    PIPE --> TS

    API -.->|enqueue run| DB
    DB -.->|claim run| W
```

The API never calls the worker and the worker never calls the API. They meet only in the database:
the API writes a `queued` run, the worker claims it with a conditional `UPDATE`. Dependencies run
one way — `services/*` may import `core`, `core` never imports `services`.

## Data flow: object to citation

```mermaid
flowchart TD
    OBJ[S3 object] --> CHEAP{etag · size · mtime<br/>unchanged?}
    CHEAP -->|yes| SKIP[skipped<br/>no download at all]
    CHEAP -->|no| DL[download bytes]
    DL --> SHA[sha256 = identity]

    SHA --> KNOWN{blob known at current<br/>extraction_version?}
    KNOWN -->|yes| REUSE[reuse text + chunks]
    KNOWN -->|no| EXTRACT[extract → chunk → store]

    REUSE --> HAS
    EXTRACT --> HAS
    HAS{user already<br/>possesses sha256?}
    HAS -->|yes| ATTRIB[insert files row only<br/>vector store untouched]
    HAS -->|no| CACHE{vectors cached for<br/>sha256 + version?}

    CACHE -->|yes| COPY[copy vectors into<br/>this user's collection]
    CACHE -->|no| EMBED[embed, then cache<br/>for future users]

    COPY --> OWN
    EMBED --> OWN
    OWN[(kb_user_id<br/>this user's vectors)]

    OWN --> Q[question]
    Q --> SEARCH[search → chunk ids + scores<br/>NO text]
    SEARCH --> RESOLVE[get_chunk_texts user_id, ids<br/>joins user_blobs]
    RESOLVE --> ANSWER[answer + citation<br/>to the source file]

    ATTRIB -.-> RESOLVE
```

The two-stage read is the point of the bottom half. Search returns ids; text only ever comes from a
user-scoped lookup. A vector that somehow surfaced from another tenant's collection resolves to
nothing and drops out before it can reach a prompt.

### The three tables that carry the model

```mermaid
erDiagram
    blobs ||--o{ chunks : "chunked into"
    blobs ||--o{ user_blobs : "possessed by"
    blobs ||--o{ files : "named by"
    users ||--o{ files : owns
    users ||--o{ user_blobs : holds
    directories ||--o{ files : contains
    datasources ||--o{ directories : has
    directories ||--o{ sync_runs : "synced by"

    blobs {
        text sha256 PK "no user_id - global"
        text extraction_version
        text embedding_version
    }
    files {
        text user_id FK "attribution"
        text provider_key
        text etag_size_mtime "cheap path"
        text sha256 FK
        real deleted_at "soft delete"
    }
    user_blobs {
        text user_id PK "possession"
        text sha256 PK
    }
```

`blobs` is identity, `files` is attribution, `user_blobs` is possession. Keeping the three apart is
what makes a cross-tenant cache safe: a hit crosses tenants only on identity, and nothing about
another user's attribution is reachable from a sha256.

## Sync lifecycle

Every transition is a database write. There is no in-memory state machine, which is what makes each
awkward case answerable by reading a row.

```mermaid
stateDiagram-v2
    [*] --> idle
    idle --> queued : POST /sync
    queued --> queued : second POST<br/>(index rejects it,<br/>caller gets the run in flight)
    queued --> running : worker claims<br/>(conditional UPDATE)
    running --> running : heartbeat + per-file counters

    running --> succeeded : no failures
    running --> partial : some files failed
    running --> failed : listing failed<br/>(datasource unreachable)
    running --> failed : heartbeat stale > 2min<br/>(worker died, directory freed)

    succeeded --> queued : re-sync
    partial --> queued : re-sync
    failed --> queued : re-sync
```

| Case | What happens | Test |
|---|---|---|
| Double click | Partial unique index on `sync_runs(directory_id)` where state is queued/running rejects the second insert; that caller is handed the run already in flight. | `test_second_sync_request_returns_the_run_already_in_flight` |
| Refresh mid-run | Counters are written per file, so progress is read from the database, not process memory. | `test_counters_are_readable_from_the_database_during_a_run` |
| Dead worker | `heartbeat_at` older than two minutes is reclaimed as `failed`, freeing the directory. | `test_a_run_whose_worker_died_is_reclaimed_and_the_directory_freed` |
| One bad file | Per-file failure increments a counter and continues; the run ends `partial`. | `test_one_unreadable_file_does_not_abort_the_run` |
| Deleted at source | Anything absent from the listing is soft deleted through the same path as manual removal. | `test_a_file_removed_at_the_source_is_soft_deleted_and_unindexed` |
| Nothing new | A `succeeded` run with `files_new = 0`. Falls out of the states rather than being special-cased. | `test_resync_of_an_unchanged_directory_downloads_nothing` |

## Removal

Soft delete the `files` row. Drop that content's vectors from the user's collection **only when no
other live file row of the same user references the same sha256** — the same content can arrive
under several names or in several directories. `blobs`, `chunks`, and the embedding cache persist:
they are content, not attribution. Answers stop citing the file immediately.

The sync worker's "deleted at source" path and the manual `DELETE /files/{id}` route run the same
rule, so the two cannot drift apart.
