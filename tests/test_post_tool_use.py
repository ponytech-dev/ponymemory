"""Tests for hooks/post_tool_use.py — file event and MCP download capture."""
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import init_db, count_by_status
from hooks.post_tool_use import main, get_project_name


# ── Helpers ────────────────────────────────────────────────────────────────────

def _run_main_with_stdin(payload: dict, db_path: str) -> str:
    """Run main() with the given payload as stdin, using a temp DB.

    Returns the captured stdout.
    """
    stdin_data = json.dumps(payload)
    with patch("hooks.post_tool_use.init_db", return_value=init_db(db_path)):
        with patch("sys.stdin", io.StringIO(stdin_data)):
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                main()
    return captured.getvalue()


def _queue_rows(conn, status="pending"):
    cursor = conn.execute("SELECT * FROM queue WHERE status = ?", (status,))
    return cursor.fetchall()


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_write_tool_queues_event():
    """Write tool with a real file path should write a tool_event to the queue."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        conn = init_db(db_path)

        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/Users/jiajun-agent/pony/test.md",
                "content": "hello world",
            },
            "session_id": "test-session-write",
        }

        stdin_data = json.dumps(payload)
        with patch("hooks.post_tool_use.init_db", return_value=init_db(db_path)):
            with patch("sys.stdin", io.StringIO(stdin_data)):
                captured = io.StringIO()
                with patch("sys.stdout", captured):
                    main()

        stdout = captured.getvalue().strip()
        assert stdout == "{}", f"Expected empty JSON, got: {stdout!r}"

        rows = _queue_rows(conn)
        assert len(rows) == 1
        row = rows[0]
        assert row["type"] == "tool_event"
        assert row["session_id"] == "test-session-write"

        payload_data = json.loads(row["payload"])
        assert payload_data["tool"] == "Write"
        assert payload_data["file_path"] == "/Users/jiajun-agent/pony/test.md"
        assert payload_data["content_preview"] == "hello world"

        conn.close()


def test_edit_tool_queues_event():
    """Edit tool with a real file path should also write a tool_event."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        conn = init_db(db_path)

        payload = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/Users/jiajun-agent/pony/ponymemory/db.py",
                "old_string": "old",
                "new_string": "new",
            },
            "session_id": "test-session-edit",
        }

        stdin_data = json.dumps(payload)
        with patch("hooks.post_tool_use.init_db", return_value=init_db(db_path)):
            with patch("sys.stdin", io.StringIO(stdin_data)):
                captured = io.StringIO()
                with patch("sys.stdout", captured):
                    main()

        rows = _queue_rows(conn)
        assert len(rows) == 1
        payload_data = json.loads(rows[0]["payload"])
        assert payload_data["tool"] == "Edit"
        assert payload_data["file_path"] == "/Users/jiajun-agent/pony/ponymemory/db.py"
        # Edit has no "content" key — preview should be empty string
        assert payload_data["content_preview"] == ""

        conn.close()


def test_ignored_paths_skipped():
    """Write tool with a /tmp/ path should NOT write anything to the queue."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        conn = init_db(db_path)

        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/tmp/screenshot.png",
                "content": "binary data",
            },
            "session_id": "test-session-ignored",
        }

        stdin_data = json.dumps(payload)
        with patch("hooks.post_tool_use.init_db", return_value=init_db(db_path)):
            with patch("sys.stdin", io.StringIO(stdin_data)):
                captured = io.StringIO()
                with patch("sys.stdout", captured):
                    main()

        stdout = captured.getvalue().strip()
        assert stdout == "{}"

        rows = _queue_rows(conn)
        assert len(rows) == 0, f"Expected no queue entries, got {len(rows)}"

        conn.close()


def test_ignored_paths_node_modules():
    """Write tool with node_modules in path should be skipped."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        conn = init_db(db_path)

        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/Users/jiajun-agent/pony/spaflow/node_modules/pkg/index.js",
                "content": "module.exports = {}",
            },
            "session_id": "test-session-nm",
        }

        stdin_data = json.dumps(payload)
        with patch("hooks.post_tool_use.init_db", return_value=init_db(db_path)):
            with patch("sys.stdin", io.StringIO(stdin_data)):
                captured = io.StringIO()
                with patch("sys.stdout", captured):
                    main()

        rows = _queue_rows(conn)
        assert len(rows) == 0

        conn.close()


def test_mcp_tool_queues_download():
    """MCP tool in WATCHED_MCP should write a mcp_download entry to the queue."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        conn = init_db(db_path)

        payload = {
            "tool_name": "mcp__gmail-agent__get_email",
            "tool_input": {"message_id": "abc123"},
            "tool_response": {"subject": "Test email", "body": "Hello"},
            "session_id": "test-session-mcp",
        }

        stdin_data = json.dumps(payload)
        with patch("hooks.post_tool_use.init_db", return_value=init_db(db_path)):
            with patch("sys.stdin", io.StringIO(stdin_data)):
                captured = io.StringIO()
                with patch("sys.stdout", captured):
                    main()

        stdout = captured.getvalue().strip()
        assert stdout == "{}"

        rows = _queue_rows(conn)
        assert len(rows) == 1
        row = rows[0]
        assert row["type"] == "mcp_download"
        assert row["session_id"] == "test-session-mcp"

        payload_data = json.loads(row["payload"])
        assert payload_data["tool"] == "mcp__gmail-agent__get_email"
        assert payload_data["input"] == {"message_id": "abc123"}
        assert payload_data["response"] == {"subject": "Test email", "body": "Hello"}

        conn.close()


def test_mcp_google_workspace_drive_queues_download():
    """Google Workspace Drive MCP tool should also write mcp_download."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        conn = init_db(db_path)

        payload = {
            "tool_name": "mcp__google-workspace__get_drive_file_content",
            "tool_input": {"file_id": "1xyz"},
            "tool_response": {"content": "file content here"},
            "session_id": "test-session-drive",
        }

        stdin_data = json.dumps(payload)
        with patch("hooks.post_tool_use.init_db", return_value=init_db(db_path)):
            with patch("sys.stdin", io.StringIO(stdin_data)):
                captured = io.StringIO()
                with patch("sys.stdout", captured):
                    main()

        rows = _queue_rows(conn)
        assert len(rows) == 1
        assert rows[0]["type"] == "mcp_download"

        conn.close()


def test_unrelated_tool_no_queue_entry():
    """Unrelated tool name (not Write/Edit/MCP) should produce no queue entry."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        conn = init_db(db_path)

        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
            "session_id": "test-session-bash",
        }

        stdin_data = json.dumps(payload)
        with patch("hooks.post_tool_use.init_db", return_value=init_db(db_path)):
            with patch("sys.stdin", io.StringIO(stdin_data)):
                captured = io.StringIO()
                with patch("sys.stdout", captured):
                    main()

        stdout = captured.getvalue().strip()
        assert stdout == "{}"

        rows = _queue_rows(conn)
        assert len(rows) == 0

        conn.close()


def test_content_preview_truncated_at_500():
    """Content longer than 500 chars should be truncated in the preview."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        conn = init_db(db_path)

        long_content = "x" * 1000
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/Users/jiajun-agent/pony/test_large.md",
                "content": long_content,
            },
            "session_id": "test-session-trunc",
        }

        stdin_data = json.dumps(payload)
        with patch("hooks.post_tool_use.init_db", return_value=init_db(db_path)):
            with patch("sys.stdin", io.StringIO(stdin_data)):
                captured = io.StringIO()
                with patch("sys.stdout", captured):
                    main()

        rows = _queue_rows(conn)
        assert len(rows) == 1
        payload_data = json.loads(rows[0]["payload"])
        assert len(payload_data["content_preview"]) == 500

        conn.close()


def test_get_project_name_from_cwd():
    """get_project_name extracts project from CWD env var."""
    with patch.dict("os.environ", {"CWD": "/Users/jiajun-agent/pony/ponymemory/hooks"}):
        name = get_project_name()
    assert name == "ponymemory"


def test_get_project_name_fallback():
    """get_project_name returns 'pony' when CWD is outside ~/pony/."""
    with patch.dict("os.environ", {"CWD": "/Users/jiajun-agent/other-project"}):
        name = get_project_name()
    assert name == "pony"
