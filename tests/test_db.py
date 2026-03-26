"""Tests for db.py — SQLite queue database layer."""
import json
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import (
    init_db,
    write_to_queue,
    claim_next_item,
    delete_queue_item,
    mark_failed,
    reset_stuck_records,
    store_raw_observation,
    log_exec,
    count_by_status,
)


@pytest.fixture
def tmp_db():
    """Create a fresh DB in a temp directory for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(db_path=str(db_path))
        yield conn
        conn.close()


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_init_db_creates_tables(tmp_db):
    """init_db must create all 4 required tables."""
    conn = tmp_db
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row["name"] for row in cursor.fetchall()}
    assert "queue" in tables
    assert "raw_observations" in tables
    assert "exec_log" in tables
    assert "maintenance_log" in tables


def test_write_and_claim(tmp_db):
    """write_to_queue inserts a pending item; claim_next_item atomically claims it."""
    conn = tmp_db
    payload = {"content": "hello world", "tags": ["test"]}
    write_to_queue(conn, session_id="sess-1", project="ponymemory",
                   queue_type="memory_write", payload=payload)

    item = claim_next_item(conn)
    assert item is not None
    assert item["session_id"] == "sess-1"
    assert item["project"] == "ponymemory"
    assert item["type"] == "memory_write"
    assert item["status"] == "processing"

    # payload round-trips correctly
    loaded = json.loads(item["payload"])
    assert loaded == payload

    # claimed_at is set
    assert item["claimed_at"] is not None


def test_claim_returns_none_when_empty(tmp_db):
    """claim_next_item returns None when queue is empty."""
    conn = tmp_db
    result = claim_next_item(conn)
    assert result is None


def test_claim_only_returns_pending(tmp_db):
    """Claiming twice: second claim returns None (first item now processing)."""
    conn = tmp_db
    write_to_queue(conn, "s1", "proj", "memory_write", {"x": 1})
    first = claim_next_item(conn)
    assert first is not None
    second = claim_next_item(conn)
    assert second is None


def test_delete_queue_item(tmp_db):
    """delete_queue_item removes the item from the queue."""
    conn = tmp_db
    write_to_queue(conn, "s1", "proj", "memory_write", {"x": 1})
    item = claim_next_item(conn)
    delete_queue_item(conn, item["id"])

    cursor = conn.execute("SELECT COUNT(*) as cnt FROM queue")
    assert cursor.fetchone()["cnt"] == 0


def test_mark_failed(tmp_db):
    """mark_failed sets status='failed' and records error_message."""
    conn = tmp_db
    write_to_queue(conn, "s1", "proj", "memory_write", {"x": 1})
    item = claim_next_item(conn)
    mark_failed(conn, item["id"], "something broke")

    cursor = conn.execute("SELECT status, error_message FROM queue WHERE id=?",
                          (item["id"],))
    row = cursor.fetchone()
    assert row["status"] == "failed"
    assert row["error_message"] == "something broke"


def test_reset_stuck(tmp_db):
    """reset_stuck_records resets items in 'processing' state older than threshold."""
    conn = tmp_db
    write_to_queue(conn, "s1", "proj", "memory_write", {"x": 1})
    item = claim_next_item(conn)

    # Manually backdate claimed_at to simulate stuck item
    old_time = time.time() - 200  # 200 seconds ago
    conn.execute("UPDATE queue SET claimed_at=? WHERE id=?", (old_time, item["id"]))
    conn.commit()

    reset_count = reset_stuck_records(conn, threshold_seconds=120)
    assert reset_count >= 1

    cursor = conn.execute("SELECT status FROM queue WHERE id=?", (item["id"],))
    row = cursor.fetchone()
    assert row["status"] == "pending"


def test_reset_stuck_leaves_fresh_items_alone(tmp_db):
    """reset_stuck_records does NOT reset recently-claimed items."""
    conn = tmp_db
    write_to_queue(conn, "s1", "proj", "memory_write", {"x": 1})
    claim_next_item(conn)  # claimed_at = now

    reset_count = reset_stuck_records(conn, threshold_seconds=120)
    assert reset_count == 0


def test_store_raw_observation(tmp_db):
    """store_raw_observation inserts a row into raw_observations."""
    conn = tmp_db
    store_raw_observation(conn, session_id="s1", project="proj",
                          text="user said something", source="stop_hook")

    cursor = conn.execute("SELECT * FROM raw_observations")
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["text"] == "user said something"
    assert rows[0]["source"] == "stop_hook"
    assert rows[0]["processed"] == 0


def test_log_exec(tmp_db):
    """log_exec inserts an audit record into exec_log."""
    conn = tmp_db
    log_exec(conn, hook="stop_hook", session_id="s1",
             lines_captured=42, queue_written=3)

    cursor = conn.execute("SELECT * FROM exec_log")
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["hook"] == "stop_hook"
    assert rows[0]["lines_captured"] == 42
    assert rows[0]["queue_written"] == 3
    assert rows[0]["error"] is None


def test_log_exec_with_error(tmp_db):
    """log_exec records error field when provided."""
    conn = tmp_db
    log_exec(conn, hook="stop_hook", session_id="s1",
             lines_captured=0, queue_written=0, error="connection refused")

    cursor = conn.execute("SELECT error FROM exec_log")
    row = cursor.fetchone()
    assert row["error"] == "connection refused"


def test_count_by_status(tmp_db):
    """count_by_status returns correct count for each status."""
    conn = tmp_db
    write_to_queue(conn, "s1", "proj", "memory_write", {"a": 1})
    write_to_queue(conn, "s2", "proj", "memory_write", {"b": 2})
    write_to_queue(conn, "s3", "proj", "memory_write", {"c": 3})

    assert count_by_status(conn, "pending") == 3
    assert count_by_status(conn, "processing") == 0

    claim_next_item(conn)
    assert count_by_status(conn, "pending") == 2
    assert count_by_status(conn, "processing") == 1


def test_payload_unicode(tmp_db):
    """Payload with non-ASCII characters round-trips correctly (ensure_ascii=False)."""
    conn = tmp_db
    payload = {"text": "你好世界 🧠 memory"}
    write_to_queue(conn, "s1", "proj", "memory_write", payload)
    item = claim_next_item(conn)
    loaded = json.loads(item["payload"])
    assert loaded["text"] == "你好世界 🧠 memory"


def test_log_maintenance(tmp_db):
    """log_maintenance inserts a record into maintenance_log."""
    from db import log_maintenance
    conn = tmp_db
    row_id = log_maintenance(conn, task="dedup", items_processed=50,
                              items_removed=3, duration_seconds=1.5)
    assert row_id is not None

    cursor = conn.execute("SELECT * FROM maintenance_log")
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["task"] == "dedup"
    assert rows[0]["items_processed"] == 50
    assert rows[0]["items_removed"] == 3
    assert rows[0]["duration_seconds"] == 1.5
