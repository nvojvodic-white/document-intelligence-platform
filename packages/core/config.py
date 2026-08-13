"""Paths, versions, and provider settings. All env-overridable.

The version constants are cache keys: rows carry the version they were produced
at and lookups filter on the current one, so a parser or model change
invalidates cached work without dropping a table.
"""
import os
from pathlib import Path

# One shared volume in compose, mounted by API and worker.
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))

DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "platform.db")))
CHROMA_DIR = Path(os.getenv("CHROMA_DIR", str(DATA_DIR / "chroma")))
TEXT_STORE_DIR = Path(os.getenv("TEXT_STORE_DIR", str(DATA_DIR / "text")))

# Bump on parser changes; older blobs are re-extracted rather than reused.
EXTRACTION_VERSION = "1"

# Bump when chunking changes, since chunks are what gets embedded.
CHUNKING_VERSION = "1"

# Encodes the model, since that is what invalidates a vector. Old vectors are
# ignored rather than deleted, so a rollback re-reads them instead of paying
# to recompute.
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_VERSION = f"{EMBEDDING_MODEL}/c{CHUNKING_VERSION}"

# 800/120 from the pre-fork retriever comparison: balances definitional
# coherence (200-400 char spans) against narrative continuity (~600-1000).
CHUNK_SIZE = 800
CHUNK_OVERLAP = 120
CHUNK_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]

# Long enough that a slow file cannot trip it, short enough that a killed
# worker frees the directory while someone is still watching.
HEARTBEAT_TIMEOUT_SEC = 120

# Identity comes from the verified token, so this key is the trust boundary.
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-not-for-production")
JWT_ALGORITHM = "HS256"
JWT_TTL_SEC = 12 * 3600
