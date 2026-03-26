#!/usr/bin/env python3
"""PonyMemory PostToolUse Hook — capture file writes and MCP downloads."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import init_db, write_to_queue, log_exec

WATCHED_TOOLS = {"Write", "Edit"}
WATCHED_MCP = {
    "mcp__gmail-agent__get_email",
    "mcp__google-workspace__download_chat_attachment",
    "mcp__google-workspace__get_drive_file_content",
}
IGNORED_PATHS = ["/tmp/", "_debug", "_temp", "node_modules", ".git/", "__pycache__"]


def get_project_name():
    cwd = os.environ.get("CWD", os.getcwd())
    pony_dir = os.path.expanduser("~/pony/")
    if cwd.startswith(pony_dir):
        relative = cwd[len(pony_dir):]
        parts = relative.strip("/").split("/")
        if parts and parts[0]:
            return parts[0]
    return "pony"


def main():
    data = json.loads(sys.stdin.read())
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    session_id = data.get("session_id", "")

    if tool_name in WATCHED_TOOLS:
        file_path = tool_input.get("file_path", "")
        if any(p in file_path for p in IGNORED_PATHS):
            print(json.dumps({}))
            return
        try:
            conn = init_db()
            write_to_queue(conn, session_id, get_project_name(), "tool_event", {
                "tool": tool_name,
                "file_path": file_path,
                "content_preview": tool_input.get("content", "")[:500],
            })
            log_exec(conn, "post_tool_use", session_id, 0, 1)
            conn.close()
        except Exception as e:
            print(f"[PonyMemory] post_tool_use failed: {e}", file=sys.stderr)

    elif tool_name in WATCHED_MCP:
        try:
            conn = init_db()
            write_to_queue(conn, session_id, get_project_name(), "mcp_download", {
                "tool": tool_name,
                "input": tool_input,
                "response": data.get("tool_response", {}),
            })
            log_exec(conn, "post_tool_use", session_id, 0, 1)
            conn.close()
        except Exception as e:
            print(f"[PonyMemory] post_tool_use mcp failed: {e}", file=sys.stderr)

    print(json.dumps({}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[PonyMemory] post_tool_use fatal: {e}", file=sys.stderr)
        print(json.dumps({}))
