#!/usr/bin/env python3
"""
PonyMemory Stop Hook (v3 — transcript to SQLite queue)

Reads new transcript lines since last run, writes user/assistant messages
to the SQLite queue for downstream processing. No AI API calls. No additionalContext.
"""
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional

# Allow importing db.py from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import init_db, write_to_queue, log_exec

DEFAULT_CURSOR_DIR = os.path.expanduser("~/.claude/.ponymemory_cursors")


def get_project_name() -> str:
    """Extract project name from the CWD env var."""
    cwd = os.environ.get("CWD", os.getcwd())
    pony_dir = os.path.expanduser("~/pony/")
    if cwd.startswith(pony_dir):
        relative = cwd[len(pony_dir):]
        parts = relative.strip("/").split("/")
        if parts and parts[0]:
            return parts[0]
    return "pony"


def _cursor_path(transcript_path: str, cursor_dir: str) -> Path:
    """Return the cursor file path for a given transcript."""
    key = hashlib.md5(transcript_path.encode()).hexdigest()[:8]
    return Path(cursor_dir) / f"{key}.cursor"


def read_transcript_incremental(
    transcript_path: str,
    cursor_dir: Optional[str] = None,
) -> list[dict]:
    """Read new lines from a JSONL transcript since the last cursor position.

    Args:
        transcript_path: Absolute path to the JSONL transcript file.
        cursor_dir: Directory for cursor files. Defaults to DEFAULT_CURSOR_DIR.

    Returns:
        List of parsed JSON objects with type in ('user', 'assistant').
    """
    if cursor_dir is None:
        cursor_dir = DEFAULT_CURSOR_DIR

    Path(cursor_dir).mkdir(parents=True, exist_ok=True)
    cursor_file = _cursor_path(transcript_path, cursor_dir)

    # Read last known byte offset
    last_offset = 0
    if cursor_file.exists():
        try:
            last_offset = int(cursor_file.read_text().strip())
        except (ValueError, OSError):
            last_offset = 0

    # Read new bytes from transcript
    try:
        with open(transcript_path, "rb") as f:
            f.seek(last_offset)
            new_bytes = f.read()
            new_offset = f.tell()
    except OSError:
        return []

    # Persist updated cursor
    cursor_file.write_text(str(new_offset))

    if not new_bytes:
        return []

    # Decode and parse JSONL lines
    results = []
    for raw_line in new_bytes.decode("utf-8", errors="replace").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") in ("user", "assistant"):
            results.append(obj)

    return results


def main() -> None:
    # Read stdin JSON payload from Claude Code
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        payload = {}

    # Guard: prevent infinite loop
    if payload.get("stop_hook_active"):
        print(json.dumps({}))
        return

    session_id: str = payload.get("session_id", "unknown")
    transcript_path: Optional[str] = payload.get("transcript_path")
    project = get_project_name()

    lines_captured = 0
    queue_written = 0
    error_msg: Optional[str] = None

    conn = None
    try:
        conn = init_db()

        if transcript_path:
            new_lines = read_transcript_incremental(transcript_path)
            lines_captured = len(new_lines)

            for line in new_lines:
                write_to_queue(
                    conn,
                    session_id=session_id,
                    project=project,
                    queue_type="conversation_line",
                    payload=line,
                )
                queue_written += 1

        log_exec(
            conn,
            hook="stop_hook_v3",
            session_id=session_id,
            lines_captured=lines_captured,
            queue_written=queue_written,
        )

    except Exception as exc:
        error_msg = str(exc)
        print(f"[PonyMemory] stop hook error: {exc}", file=sys.stderr)
        if conn is not None:
            try:
                log_exec(
                    conn,
                    hook="stop_hook_v3",
                    session_id=session_id,
                    lines_captured=lines_captured,
                    queue_written=queue_written,
                    error=error_msg,
                )
            except Exception:
                pass
    finally:
        if conn is not None:
            conn.close()

    print(json.dumps({}))


if __name__ == "__main__":
    main()
