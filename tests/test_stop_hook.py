"""Tests for hooks/stop.py (v3) — transcript incremental reader."""
import json
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure project root is on path so we can import hooks/stop
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the function under test
from hooks.stop import read_transcript_incremental


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_jsonl(lines: list[dict]) -> bytes:
    """Encode a list of dicts to JSONL bytes."""
    return b"\n".join(json.dumps(d).encode() for d in lines) + b"\n"


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_read_transcript_incremental_first_read():
    """On first call with no cursor, all lines in the transcript are returned."""
    with tempfile.TemporaryDirectory() as tmpdir:
        transcript = Path(tmpdir) / "transcript.jsonl"
        cursor_dir = Path(tmpdir) / "cursors"
        cursor_dir.mkdir()

        data = [
            {"type": "user", "content": "hello"},
            {"type": "assistant", "content": "hi there"},
            {"type": "system", "content": "should be ignored"},
        ]
        transcript.write_bytes(_make_jsonl(data))

        result = read_transcript_incremental(
            str(transcript), cursor_dir=str(cursor_dir)
        )

        # Only user and assistant lines returned
        assert len(result) == 2
        types = [r["type"] for r in result]
        assert "user" in types
        assert "assistant" in types
        assert "system" not in types


def test_read_transcript_incremental_second_read_empty():
    """On second call with no new data, returns empty list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        transcript = Path(tmpdir) / "transcript.jsonl"
        cursor_dir = Path(tmpdir) / "cursors"
        cursor_dir.mkdir()

        data = [
            {"type": "user", "content": "hello"},
            {"type": "assistant", "content": "hi"},
        ]
        transcript.write_bytes(_make_jsonl(data))

        # First read — consume all content
        first = read_transcript_incremental(
            str(transcript), cursor_dir=str(cursor_dir)
        )
        assert len(first) == 2

        # Second read — no new bytes
        second = read_transcript_incremental(
            str(transcript), cursor_dir=str(cursor_dir)
        )
        assert second == []


def test_read_transcript_incremental_appended():
    """After appending new lines, only the new lines are returned."""
    with tempfile.TemporaryDirectory() as tmpdir:
        transcript = Path(tmpdir) / "transcript.jsonl"
        cursor_dir = Path(tmpdir) / "cursors"
        cursor_dir.mkdir()

        initial = [
            {"type": "user", "content": "first message"},
            {"type": "assistant", "content": "first reply"},
        ]
        transcript.write_bytes(_make_jsonl(initial))

        # First read
        first = read_transcript_incremental(
            str(transcript), cursor_dir=str(cursor_dir)
        )
        assert len(first) == 2

        # Append new lines
        appended = [
            {"type": "user", "content": "second message"},
            {"type": "assistant", "content": "second reply"},
        ]
        with transcript.open("ab") as f:
            f.write(_make_jsonl(appended))

        # Second read — only appended lines
        second = read_transcript_incremental(
            str(transcript), cursor_dir=str(cursor_dir)
        )
        assert len(second) == 2
        contents = [r["content"] for r in second]
        assert "second message" in contents
        assert "second reply" in contents
        # Old lines not re-returned
        assert "first message" not in contents
        assert "first reply" not in contents
