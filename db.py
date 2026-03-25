"""SQLite queue database layer for PonyMemory v2.

Tables:
  queue             — pending/processing/failed/done memory operations
  raw_observations  — fallback raw text storage
  exec_log          — audit trail for hook executions
  maintenance_log   — housekeeping task records
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path.home() / ".claude" / ".ponymemory.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    project TEXT NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at REAL NOT NULL,
    claimed_at REAL,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS raw_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    project TEXT,
    text TEXT NOT NULL,
    source TEXT,
    created_at REAL NOT NULL,
    processed INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS exec_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    hook TEXT NOT NULL,
    session_id TEXT,
    lines_captured INTEGER,
    queue_written INTEGER,
    error TEXT
);

CREATE TABLE IF NOT EXISTS maintenance_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    task TEXT NOT NULL,
    items_processed INTEGER,
    items_removed INTEGER,
    duration_seconds REAL
);
"""


def init_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Create (or open) the SQLite database and ensure all tables exist.

    Args:
        db_path: Path to the .db file. Defaults to ~/.claude/.ponymemory.db.

    Returns:
        An open sqlite3.Connection with row_factory set to sqlite3.Row.
    """
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


# ── Queue operations ───────────────────────────────────────────────────────────

def write_to_queue(
    conn: sqlite3.Connection,
    session_id: str,
    project: str,
    queue_type: str,
    payload: object,
) -> int:
    """Insert a new pending item into the queue.

    Args:
        conn: Open database connection.
        session_id: Claude session identifier.
        project: Project name (e.g. 'ponymemory').
        queue_type: Operation type (e.g. 'memory_write').
        payload: Arbitrary dict/list/scalar; stored as JSON.

    Returns:
        The row id of the inserted item.
    """
    payload_json = json.dumps(payload, ensure_ascii=False)
    cursor = conn.execute(
        """
        INSERT INTO queue (session_id, project, type, payload, status, created_at)
        VALUES (?, ?, ?, ?, 'pending', ?)
        """,
        (session_id, project, queue_type, payload_json, time.time()),
    )
    conn.commit()
    return cursor.lastrowid


def claim_next_item(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    """Atomically claim the oldest pending queue item.

    Uses a single UPDATE…WHERE id=(subquery) RETURNING * to avoid races.

    Returns:
        A sqlite3.Row for the claimed item (status='processing'), or None if
        the queue contains no pending items.
    """
    cursor = conn.execute(
        """
        UPDATE queue
        SET status = 'processing',
            claimed_at = ?
        WHERE id = (
            SELECT id FROM queue
            WHERE status = 'pending'
            ORDER BY created_at
            LIMIT 1
        )
        RETURNING *
        """,
        (time.time(),),
    )
    row = cursor.fetchone()  # fetch before commit — avoids "SQL statements in progress"
    conn.commit()
    return row


def delete_queue_item(conn: sqlite3.Connection, item_id: int) -> None:
    """Remove a processed item from the queue.

    Args:
        conn: Open database connection.
        item_id: Primary key of the item to delete.
    """
    conn.execute("DELETE FROM queue WHERE id = ?", (item_id,))
    conn.commit()


def mark_failed(
    conn: sqlite3.Connection,
    item_id: int,
    error_message: str,
) -> None:
    """Mark a queue item as failed and record the error reason.

    Args:
        conn: Open database connection.
        item_id: Primary key of the item.
        error_message: Human-readable error description.
    """
    conn.execute(
        """
        UPDATE queue
        SET status = 'failed',
            error_message = ?,
            retry_count = retry_count + 1
        WHERE id = ?
        """,
        (error_message, item_id),
    )
    conn.commit()


def reset_stuck_records(
    conn: sqlite3.Connection,
    threshold_seconds: int = 120,
) -> int:
    """Reset queue items stuck in 'processing' state back to 'pending'.

    An item is considered stuck when its claimed_at timestamp is older than
    threshold_seconds ago.

    Args:
        conn: Open database connection.
        threshold_seconds: Age in seconds beyond which a processing item is
            considered stuck. Defaults to 120.

    Returns:
        Number of items reset.
    """
    cutoff = time.time() - threshold_seconds
    cursor = conn.execute(
        """
        UPDATE queue
        SET status = 'pending',
            claimed_at = NULL
        WHERE status = 'processing'
          AND claimed_at < ?
        """,
        (cutoff,),
    )
    conn.commit()
    return cursor.rowcount


# ── Raw observations ───────────────────────────────────────────────────────────

def store_raw_observation(
    conn: sqlite3.Connection,
    session_id: Optional[str],
    project: Optional[str],
    text: str,
    source: Optional[str],
) -> int:
    """Store a raw text observation (fallback storage).

    Args:
        conn: Open database connection.
        session_id: Claude session identifier (may be None).
        project: Project name (may be None).
        text: Raw observation text.
        source: Origin of the observation (e.g. 'stop_hook').

    Returns:
        The row id of the inserted observation.
    """
    cursor = conn.execute(
        """
        INSERT INTO raw_observations (session_id, project, text, source, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session_id, project, text, source, time.time()),
    )
    conn.commit()
    return cursor.lastrowid


# ── Audit logging ──────────────────────────────────────────────────────────────

def log_exec(
    conn: sqlite3.Connection,
    hook: str,
    session_id: Optional[str],
    lines_captured: int,
    queue_written: int,
    error: Optional[str] = None,
) -> int:
    """Insert an audit record for a hook execution.

    Args:
        conn: Open database connection.
        hook: Name of the hook (e.g. 'stop_hook').
        session_id: Claude session identifier (may be None).
        lines_captured: Number of transcript lines read.
        queue_written: Number of queue items written in this run.
        error: Error message if the hook failed, otherwise None.

    Returns:
        The row id of the inserted log entry.
    """
    cursor = conn.execute(
        """
        INSERT INTO exec_log (timestamp, hook, session_id, lines_captured,
                              queue_written, error)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (time.time(), hook, session_id, lines_captured, queue_written, error),
    )
    conn.commit()
    return cursor.lastrowid


# ── Queries ────────────────────────────────────────────────────────────────────

def count_by_status(conn: sqlite3.Connection, status: str) -> int:
    """Count queue items with the given status.

    Args:
        conn: Open database connection.
        status: One of 'pending', 'processing', 'failed', 'done'.

    Returns:
        Item count.
    """
    cursor = conn.execute(
        "SELECT COUNT(*) AS cnt FROM queue WHERE status = ?",
        (status,),
    )
    return cursor.fetchone()["cnt"]
