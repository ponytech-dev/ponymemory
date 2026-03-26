"""Tests for hooks/stop.py (v3) — transcript incremental reader."""
import json
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure project root is on path so we can import hooks/stop
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the functions under test
from hooks.stop import read_transcript_incremental, has_significant_content


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


# ── has_significant_content tests ──────────────────────────────────────────────

def _make_lines_with_text(text: str) -> list[dict]:
    """Build a minimal transcript line list containing the given text."""
    return [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": text}
                ]
            },
        }
    ]


def test_has_significant_content_with_keyword():
    """Returns True when a significance keyword appears in sufficient text."""
    long_text = "用户纠正了之前的分析结果，需要重新评估。" * 5  # >100 chars, contains 纠正
    lines = _make_lines_with_text(long_text)
    assert has_significant_content(lines) is True


def test_has_significant_content_no_keyword():
    """Returns False when text is long enough but contains no significance keyword."""
    long_text = "这是一段普通的对话内容，没有任何需要记忆的信息。" * 5  # >100 chars, no keywords
    lines = _make_lines_with_text(long_text)
    assert has_significant_content(lines) is False


def test_has_significant_content_too_short():
    """Returns False when total text is under 100 characters even with a keyword."""
    lines = _make_lines_with_text("决定")  # keyword present but too short
    assert has_significant_content(lines) is False


def test_has_significant_content_empty():
    """Returns False for an empty line list."""
    assert has_significant_content([]) is False
