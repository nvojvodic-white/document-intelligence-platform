"""Paths, versions, and provider settings, all env-overridable.

The two version constants are the cache invalidation keys. They exist so a
parser or model change invalidates cached work without anyone having to drop a
table: rows carry the version they were produced at, and lookups filter on the
current one, so stale entries are simply never read.
"""
import os
from pathlib import Path

# One shared volume in compose, mounted by both the API and the worker.
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))

DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "platform.db")))
CHROMA_DIR = Path(os.getenv("CHROMA_DIR", str(DATA_DIR / "chroma")))
TEXT_STORE_DIR = Path(os.getenv("TEXT_STORE_DIR", str(DATA_DIR / "text")))

# Bump when the extraction path changes shape (new parser, different
# normalisation). Blobs extracted at an older version are re-extracted rather
# than reused.
EXTRACTION_VERSION = "1"

# Bump when chunking changes, since chunks are what gets embedded.
CHUNKING_VERSION = "1"

# Encodes the model, because that is what actually invalidates a vector. A
# model swap changes this string and every cached vector for the old model is
# ignored, not deleted - so a rollback re-reads the old cache instead of paying
# to recompute it.
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_VERSION = f"{EMBEDDING_MODEL}/c{CHUNKING_VERSION}"

# 800/120 with these separators came out of the pre-fork retriever comparison:
# it balances definitional coherence (entity definitions cluster in 200-400
# char spans) against event-narrative continuity (paragraphs run ~600-1000
# chars). Carried forward rather than re-derived.
CHUNK_SIZE = 800
CHUNK_OVERLAP = 120
CHUNK_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]

# A run whose heartbeat is older than this is presumed dead and reclaimable.
# Two minutes is long enough that a slow file (large download plus embedding)
# cannot trip it, short enough that a killed worker unblocks the directory
# while someone is still watching the UI.
HEARTBEAT_TIMEOUT_SEC = 120

# Dev login only. Identity is read from the verified token, so the signing key
# is the whole trust boundary; compose sets it explicitly.
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-not-for-production")
JWT_ALGORITHM = "HS256"
JWT_TTL_SEC = 12 * 3600
