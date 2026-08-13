"""SQLite connections and schema bootstrap.

The mitigations live here rather than scattered: WAL so API readers never block
behind the worker, busy_timeout so a contended write waits instead of raising,
and foreign_keys ON (off per-connection by default, which would make every
REFERENCES clause decoration).

Postgres is the first swap if a second worker is ever needed.
"""
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from core.config import DB_PATH

log = logging.getLogger(__name__)

_SCHEMA = Path(__file__).parent / "schema.sql"

# Runs once per process; the lock stops two request threads racing it.
_init_lock = threading.Lock()
_initialised = False


def _configure(con: sqlite3.Connection) -> None:
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA foreign_keys=ON")
    # Durable under app crashes; only risks the last commits on power loss,
    # which is fine for a sync that can be re-run.
    con.execute("PRAGMA synchronous=NORMAL")


def _dedupe_datasources(con: sqlite3.Connection) -> int:
    """Collapse duplicate datasources before the unique index is created.

    CREATE UNIQUE INDEX fails outright on a table that already violates it, and
    volumes from before the constraint do. Keeps the oldest row of each group
    and repoints whatever referenced the others.
    """
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='datasources'"
    ).fetchone()
    if not exists:
        return 0  # fresh database; nothing to migrate

    duplicates = con.execute(
        """
        SELECT d.id, (
            SELECT k.id FROM datasources k
            WHERE k.user_id = d.user_id AND k.kind = d.kind
              AND json_extract(k.config, '$.bucket') = json_extract(d.config, '$.bucket')
              AND COALESCE(json_extract(k.config, '$.endpoint_url'), '')
                = COALESCE(json_extract(d.config, '$.endpoint_url'), '')
            ORDER BY k.created_at, k.id LIMIT 1
        ) AS keeper
        FROM datasources d
        """
    ).fetchall()
    remap = [(row[0], row[1]) for row in duplicates if row[1] and row[0] != row[1]]
    if not remap:
        return 0

    for dead, keeper in remap:
        con.execute(
            "UPDATE directories SET datasource_id = ? WHERE datasource_id = ?",
            (keeper, dead),
        )
        # OR IGNORE: the survivor may already hold the same
        # (user_id, datasource_id, provider_key); keep its attribution.
        con.execute(
            "UPDATE OR IGNORE files SET datasource_id = ? WHERE datasource_id = ?",
            (keeper, dead),
        )
        con.execute("DELETE FROM files WHERE datasource_id = ?", (dead,))
        con.execute("DELETE FROM datasources WHERE id = ?", (dead,))

    return len(remap)


def init(force: bool = False) -> None:
    """Create the schema if absent. Idempotent."""
    global _initialised
    with _init_lock:
        if _initialised and not force:
            return
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(DB_PATH))
        try:
            _configure(con)
            collapsed = _dedupe_datasources(con)
            if collapsed:
                log.info("collapsed %s duplicate datasource(s)", collapsed)
            con.executescript(_SCHEMA.read_text(encoding="utf-8"))
            con.commit()
        finally:
            con.close()
        _initialised = True


@contextmanager
def connect():
    """Configured connection; commits on clean exit, rolls back on error.

    Per-operation rather than long-lived, so WAL readers stay short and never
    starve the writer.
    """
    init()
    con = sqlite3.connect(str(DB_PATH))
    _configure(con)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


@contextmanager
def transaction():
    """Explicit write transaction, for a read that must not be overtaken before
    its matching write lands (claiming a run, checking last-reference before
    dropping vectors). IMMEDIATE takes the lock up front rather than upgrading
    mid-transaction, where SQLite would raise."""
    init()
    con = sqlite3.connect(str(DB_PATH), isolation_level=None)
    _configure(con)
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()
