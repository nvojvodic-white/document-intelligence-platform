"""SQLite connection handling and schema bootstrap.

SQLite is the stack deviation with the sharpest edge, so the mitigations are
here in one place rather than scattered:

  - WAL, so readers (the API) never block behind the writer (the worker).
  - busy_timeout, so a contended write waits instead of raising
    'database is locked' at whichever caller lost the race.
  - foreign_keys ON, which SQLite leaves OFF per-connection by default. The
    schema is full of REFERENCES clauses that would otherwise be decoration.
  - One writing process. Run claiming is a single conditional UPDATE guarded by
    a partial unique index, so even the double-click case is settled by the
    database rather than by application logic.

Postgres is the first thing to swap in if a second worker is ever needed.
"""
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from core.config import DB_PATH

_SCHEMA = Path(__file__).parent / "schema.sql"

# Schema bootstrap runs once per process. The lock keeps two threads in the
# API worker pool from racing each other through it on the first request.
_init_lock = threading.Lock()
_initialised = False


def _configure(con: sqlite3.Connection) -> None:
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA foreign_keys=ON")
    # NORMAL is durable under application crashes and only risks the last
    # commits under OS-level power loss, which is the right trade for a sync
    # run that can simply be re-run.
    con.execute("PRAGMA synchronous=NORMAL")


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
            con.executescript(_SCHEMA.read_text(encoding="utf-8"))
            con.commit()
        finally:
            con.close()
        _initialised = True


@contextmanager
def connect():
    """A configured connection, committed on clean exit and rolled back on error.

    Deliberately not a long-lived shared connection: the API and the worker are
    separate processes, and a per-operation connection keeps WAL readers short
    so the writer is never starved.
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
    """An explicit write transaction (BEGIN IMMEDIATE).

    Used where a read must not be overtaken by another writer before the
    matching write lands - claiming a run, or deciding whether a user still
    holds other references to a blob before dropping its vectors. IMMEDIATE
    takes the write lock up front rather than upgrading mid-transaction, which
    is where SQLite would otherwise raise a deadlock error.
    """
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
