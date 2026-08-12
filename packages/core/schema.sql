-- Platform schema.
--
-- Three concepts are kept deliberately apart, and that separation is what
-- makes a shared cache safe in a multi-tenant system:
--
--   identity     = blobs.sha256      the bytes themselves, global, no owner
--   attribution  = files             who saw which bytes, where, under what name
--   possession   = user_blobs        which bytes are in whose knowledge base
--
-- A cache hit crosses tenants only on identity. Nothing about another user's
-- attribution is reachable from a sha256.

CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS datasources (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    kind        TEXT NOT NULL,              -- 's3'
    name        TEXT NOT NULL,
    -- Non-secret connection settings only (bucket, endpoint, region).
    config      TEXT NOT NULL,              -- JSON
    -- The NAME of a credential, never a credential. Resolved at use time from
    -- the process environment, so secrets stay out of the database and out of
    -- any API response.
    secret_ref  TEXT,
    created_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_datasources_user ON datasources(user_id);

-- A datasource is identified by where it points, not by when it was created.
-- Without this, clicking "connect" twice made two rows for one bucket, while
-- registering the same directory twice was already idempotent - the same
-- action, two different behaviours.
--
-- Indexed on the extracted bucket and endpoint rather than the whole config
-- blob, so a cosmetic change (a new key, a different key order) does not make
-- the same datasource look like a new one. COALESCE because a NULL endpoint
-- means "real AWS" and NULLs are all distinct to a unique index, which would
-- let unlimited duplicates through for exactly the common case.
CREATE UNIQUE INDEX IF NOT EXISTS ux_datasources_identity ON datasources(
    user_id,
    kind,
    json_extract(config, '$.bucket'),
    COALESCE(json_extract(config, '$.endpoint_url'), '')
);

CREATE TABLE IF NOT EXISTS directories (
    id             TEXT PRIMARY KEY,
    -- Denormalised from datasources so every scoped read filters on user_id
    -- directly instead of trusting a join to carry the scope.
    user_id        TEXT NOT NULL REFERENCES users(id),
    datasource_id  TEXT NOT NULL REFERENCES datasources(id),
    path           TEXT NOT NULL,           -- provider prefix, e.g. 'alice/lore/'
    status         TEXT NOT NULL,           -- mirrors the latest run's state
    created_at     REAL NOT NULL,
    UNIQUE (user_id, datasource_id, path)
);

CREATE INDEX IF NOT EXISTS ix_directories_user ON directories(user_id);

-- Global and content-addressed. Deliberately has no user_id: the same bytes
-- are the same blob no matter who supplied them.
CREATE TABLE IF NOT EXISTS blobs (
    sha256              TEXT PRIMARY KEY,
    byte_size           INTEGER NOT NULL,
    extracted_text_ref  TEXT,               -- path in the text store
    extraction_version  TEXT,               -- NULL until extracted
    embedding_version   TEXT,               -- NULL until embedded
    created_at          REAL NOT NULL
);

-- The attribution layer: a name and a place for bytes someone holds.
CREATE TABLE IF NOT EXISTS files (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL REFERENCES users(id),
    datasource_id  TEXT NOT NULL REFERENCES datasources(id),
    directory_id   TEXT NOT NULL REFERENCES directories(id),
    provider_key   TEXT NOT NULL,           -- full object key at the provider
    -- The cheap-path fingerprint. When all four still match, the object is
    -- unchanged and is never downloaded.
    etag           TEXT,
    size           INTEGER,
    mtime          REAL,
    sha256         TEXT REFERENCES blobs(sha256),   -- NULL if never downloaded
    state          TEXT NOT NULL,           -- indexed | failed | deleted
    error          TEXT,
    deleted_at     REAL,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    UNIQUE (user_id, datasource_id, provider_key)
);

CREATE INDEX IF NOT EXISTS ix_files_user_dir ON files(user_id, directory_id);
-- Supports the removal rule: are there other live rows for this user holding
-- these same bytes?
CREATE INDEX IF NOT EXISTS ix_files_user_sha ON files(user_id, sha256);

-- Possession: these bytes are in this user's knowledge base. Separate from
-- files because one user can hold the same bytes under several names.
CREATE TABLE IF NOT EXISTS user_blobs (
    user_id     TEXT NOT NULL REFERENCES users(id),
    sha256      TEXT NOT NULL REFERENCES blobs(sha256),
    created_at  REAL NOT NULL,
    PRIMARY KEY (user_id, sha256)
);

-- Chunk text, keyed by content. Global for the same reason blobs is: the
-- chunking of a given blob does not depend on who owns it. Reads go through a
-- user-scoped join against user_blobs.
CREATE TABLE IF NOT EXISTS chunks (
    sha256   TEXT NOT NULL REFERENCES blobs(sha256),
    ordinal  INTEGER NOT NULL,
    text     TEXT NOT NULL,
    PRIMARY KEY (sha256, ordinal)
);

-- The embedding cache. Vectors are stored once per (content, version) and
-- COPIED into each user's collection - never shared at read time. The saving
-- is the embedding API call, not the storage.
CREATE TABLE IF NOT EXISTS embedding_cache (
    sha256             TEXT NOT NULL,
    ordinal            INTEGER NOT NULL,
    embedding_version  TEXT NOT NULL,
    vector             BLOB NOT NULL,       -- float32, little-endian
    dim                INTEGER NOT NULL,
    created_at         REAL NOT NULL,
    PRIMARY KEY (sha256, ordinal, embedding_version)
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL REFERENCES users(id),
    directory_id   TEXT NOT NULL REFERENCES directories(id),
    state          TEXT NOT NULL,           -- queued|running|succeeded|partial|failed
    files_seen     INTEGER NOT NULL DEFAULT 0,
    files_new      INTEGER NOT NULL DEFAULT 0,
    files_skipped  INTEGER NOT NULL DEFAULT 0,
    files_failed   INTEGER NOT NULL DEFAULT 0,
    files_deleted  INTEGER NOT NULL DEFAULT 0,
    -- The dedup receipt. chunks_reused counts embeddings that were served from
    -- the cache instead of being paid for, so the saving is a number in the
    -- run record rather than a claim in the README.
    chunks_embedded INTEGER NOT NULL DEFAULT 0,
    chunks_reused   INTEGER NOT NULL DEFAULT 0,
    bytes_downloaded INTEGER NOT NULL DEFAULT 0,
    error          TEXT,
    heartbeat_at   REAL,
    started_at     REAL,
    finished_at    REAL,
    created_at     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_sync_runs_dir ON sync_runs(directory_id, created_at DESC);

-- Double-click protection, enforced by the database rather than by a check in
-- the handler. A second POST cannot create a second active run for the same
-- directory; the insert fails and the caller is handed the run already in
-- flight. This is also what makes single-worker claiming safe under SQLite.
CREATE UNIQUE INDEX IF NOT EXISTS ux_sync_runs_active
    ON sync_runs(directory_id)
    WHERE state IN ('queued', 'running');
