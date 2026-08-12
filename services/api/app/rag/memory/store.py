"""Conversation memory store: SQLite, session-scoped, append-only with TTL eviction.

Multi-turn support. Each conversation is a session_id; turns are stored
append-only with a per-session monotonic turn_index. Reads use a sliding window
(last N turns) for the synthesize prompt + coref rewriter. Old data ages out
via prune_expired() as a maintenance task (no implicit deletion on writes).

Path is env-overridable (CONV_DB_PATH) so test/eval scripts can use a
throwaway DB. Matches the pattern of app/agent/store.py's DB_PATH so the two
SQLite stores behave consistently.
"""
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass

DB_PATH = os.getenv("CONV_DB_PATH", "data/conversations.db")

# 30 days. Bounded growth without aggressive expiry. prune_expired() is an
# explicit maintenance call, not implicit on every write, so this constant is
# only consulted by callers who choose to prune.
DEFAULT_TTL_SEC = 30 * 24 * 3600


@dataclass
class Turn:
    session_id: str
    turn_index: int
    role: str            # "user" | "assistant"
    content: str
    timestamp: float

    def to_dict(self) -> dict:
        # Shape the prompt + history wire format expects.
        return {"role": self.role, "content": self.content}


@contextmanager
def _conn():
    dirname = os.path.dirname(DB_PATH)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS turns (
                session_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL NOT NULL,
                PRIMARY KEY (session_id, turn_index)
            )
            """
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_ts "
            "ON turns(session_id, timestamp)"
        )


def append_turn(session_id: str, role: str, content: str) -> int:
    """Append a turn and return its turn_index."""
    with _conn() as c:
        row = c.execute(
            "SELECT COALESCE(MAX(turn_index), -1) + 1 AS next "
            "FROM turns WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        idx = int(row["next"])
        c.execute(
            "INSERT INTO turns (session_id, turn_index, role, content, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, idx, role, content, time.time()),
        )
        return idx


def get_recent_turns(session_id: str, n: int = 6) -> list[Turn]:
    """Return last n turns for a session, oldest-first."""
    with _conn() as c:
        rows = c.execute(
            "SELECT session_id, turn_index, role, content, timestamp "
            "FROM turns WHERE session_id = ? "
            "ORDER BY turn_index DESC LIMIT ?",
            (session_id, n),
        ).fetchall()
    return [Turn(**dict(r)) for r in reversed(rows)]


def clear_session(session_id: str) -> int:
    """Delete all turns for a session. Returns rows removed."""
    with _conn() as c:
        cur = c.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
        return cur.rowcount


def prune_expired(ttl_sec: int = DEFAULT_TTL_SEC) -> int:
    """Maintenance: drop turns older than ttl_sec. Returns rows removed."""
    cutoff = time.time() - ttl_sec
    with _conn() as c:
        cur = c.execute("DELETE FROM turns WHERE timestamp < ?", (cutoff,))
        return cur.rowcount


init()
