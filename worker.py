"""PonyMemory v2 Worker — main processing loop.

Reads items from the SQLite queue, dispatches by type, and persists facts to
Qdrant + Obsidian.
"""

import json
import logging
import os
import signal
import sqlite3
import time
from pathlib import Path

from db import (
    init_db,
    claim_next_item,
    delete_queue_item,
    mark_failed,
    reset_stuck_records,
    store_raw_observation,
)
from extractor import format_conversation, extract_facts, filter_by_quality
from embedder import embed_text, search_qdrant, store_qdrant_memory, check_qdrant_health
from obsidian_writer import write_obsidian_entry, write_obsidian_milestone

# ---------------------------------------------------------------------------
# Fallback file for when Qdrant is unreachable
# ---------------------------------------------------------------------------

FALLBACK_FILE = Path.home() / ".claude" / ".ponymemory_fallback.jsonl"


def write_fallback(fact: dict, vector: list | None) -> None:
    """Write fact to fallback JSONL when Qdrant is unreachable."""
    FALLBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "fact": fact,
        "vector": vector,
        "timestamp": time.time(),
    }
    with open(FALLBACK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.info("Fact written to fallback file (Qdrant unreachable)")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PID file
# ---------------------------------------------------------------------------

PID_FILE = Path.home() / ".claude" / ".ponymemory_worker.pid"

_shutdown = False


def write_pid_file() -> None:
    """Write current PID to PID_FILE."""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    logger.info("PID file written: %s (pid=%d)", PID_FILE, os.getpid())


def remove_pid_file() -> None:
    """Remove PID_FILE if it exists."""
    try:
        PID_FILE.unlink(missing_ok=True)
        logger.info("PID file removed: %s", PID_FILE)
    except Exception as exc:
        logger.warning("Could not remove PID file: %s", exc)


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def _handle_signal(signum, frame):
    global _shutdown
    logger.info("Signal %d received — initiating graceful shutdown", signum)
    _shutdown = True


# ---------------------------------------------------------------------------
# Conversation processor
# ---------------------------------------------------------------------------

def process_conversation(item: dict, db_conn: sqlite3.Connection) -> None:
    """Process a single 'conversation' queue item.

    Steps:
        1. Parse payload (JSON string → list of transcript dicts).
        2. format_conversation → conversation_text.
        3. Skip if conversation_text < 50 chars.
        4. extract_facts → facts list.
        5. filter_by_quality → passed / failed.
        6. failed facts → store_raw_observation.
        7. passed facts → embed → dedup check → store_qdrant_memory + write_obsidian_entry.
    """
    session_id = item.get("session_id")
    project = item.get("project", "")

    # 1. Parse payload
    raw_payload = item.get("payload", "[]")
    if isinstance(raw_payload, str):
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON payload for item id=%s", item.get("id"))
            payload = []
    else:
        payload = raw_payload

    # 2. Format conversation
    conversation_text = format_conversation(payload)

    # 3. Skip short conversations
    if len(conversation_text) < 50:
        logger.info("Conversation too short (%d chars) — skipping", len(conversation_text))
        return

    # 4. Extract facts
    facts = extract_facts(conversation_text, project)
    if not facts:
        logger.info("No facts extracted from conversation (project=%s)", project)
        return

    # 5. Filter by quality
    passed, failed = filter_by_quality(facts)

    # 6. Store failed (low-quality) facts as raw observations
    for fact in failed:
        store_raw_observation(
            db_conn,
            session_id=session_id,
            project=project,
            text=fact.get("text", ""),
            source="worker_low_quality",
        )
        logger.debug("Low-quality fact stored as raw observation (score=%.2f)", fact.get("quality_score", 0))

    # 7. Process passed facts
    for fact in passed:
        # a. Embed
        vector = embed_text(fact["text"])
        if vector is None:
            logger.warning("embed_text returned None for fact — storing as raw observation")
            store_raw_observation(
                db_conn,
                session_id=session_id,
                project=project,
                text=fact.get("text", ""),
                source="worker_embed_failed",
            )
            continue

        # b. Dedup search
        similar = search_qdrant(vector, project, top_k=3)
        if similar and similar[0].get("score", 0) > 0.9:
            logger.info(
                "Dedup: skipping fact (score=%.3f) — too similar to existing memory",
                similar[0]["score"],
            )
            continue

        # c. Store in Qdrant (with fallback) and Obsidian
        point_id = store_qdrant_memory(fact, vector)
        if point_id is None:
            write_fallback(fact, vector)
        write_obsidian_entry(project, fact)
        logger.info(
            "Stored memory: type=%s score=%.2f project=%s",
            fact.get("memory_type", "?"),
            fact.get("quality_score", 0),
            project,
        )


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

def backfill_fallback() -> None:
    """Re-import entries from fallback.jsonl into Qdrant if it's back online."""
    if not FALLBACK_FILE.exists():
        return
    if check_qdrant_health() != "ok":
        return

    entries = []
    with open(FALLBACK_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not entries:
        return

    logger.info("Backfilling %d entries from fallback file", len(entries))
    remaining = []
    for entry in entries:
        fact = entry.get("fact", {})
        vector = entry.get("vector")
        if vector:
            point_id = store_qdrant_memory(fact, vector)
            if point_id is None:
                remaining.append(entry)
                break  # Qdrant went down again
        else:
            remaining.append(entry)

    # Rewrite file with remaining entries (or delete if empty)
    if remaining:
        with open(FALLBACK_FILE, "w", encoding="utf-8") as f:
            for entry in remaining:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    else:
        FALLBACK_FILE.unlink(missing_ok=True)
        logger.info("Fallback file cleared — all entries backfilled")


# ---------------------------------------------------------------------------
# Tool event and MCP download processors (Phase 2)
# ---------------------------------------------------------------------------

def _read_file_safe(path: str, max_chars: int = 4000) -> str | None:
    """Read a file and return its content truncated to max_chars, or None on error."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()[:max_chars]
    except Exception:
        return None


def _index_text_file(file_path: str, collection: str, project: str) -> None:
    """Read file, embed, store in Qdrant under the given collection."""
    content = _read_file_safe(file_path)
    if not content or len(content) < 50:
        return
    vector = embed_text(content[:2000])
    if vector is None:
        return
    fact = {
        "text": content[:500],  # Store truncated preview
        "memory_type": "document",
        "project": project,
        "source_path": file_path,
        "tags": [os.path.splitext(file_path)[1]],
    }
    store_qdrant_memory(fact, vector, collection=collection)


def _store_as_memory(content: str, memory_type: str, project: str, source_path: str) -> None:
    """Summarize content with Haiku and store as memory in Qdrant + Obsidian."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": f"用50-200字总结以下内容的要点：\n\n{content[:3000]}"}],
        )
        summary = resp.content[0].text.strip()
    except Exception:
        summary = content[:200]

    vector = embed_text(summary)
    if vector:
        fact = {
            "text": summary,
            "memory_type": memory_type,
            "project": project,
            "source_path": source_path,
        }
        store_qdrant_memory(fact, vector)
        write_obsidian_entry(project, fact)


def process_tool_event(item: dict, db_conn: sqlite3.Connection) -> None:
    """Process a 'tool_event' queue item (Write/Edit file captured by PostToolUse Hook).

    Classifies the file via router.classify_file and indexes or summarizes it
    into Qdrant / Obsidian based on the route.
    """
    payload = json.loads(item["payload"]) if isinstance(item["payload"], str) else item["payload"]
    file_path = payload.get("file_path", "")
    project = item.get("project", "")

    from router import classify_file
    route = classify_file(file_path)

    if route == "ignore":
        logger.info("tool_event: ignoring file %s (route=ignore)", file_path)
        return

    if route == "spec":
        # Index spec/plan document to Qdrant documents collection
        _index_text_file(file_path, "documents", project)
        write_obsidian_milestone(project, f"Document: {os.path.basename(file_path)}")

    elif route == "iterative_report":
        # Summarize and store as episodic memory
        content = _read_file_safe(file_path)
        if content:
            _store_as_memory(content, "milestone", project, file_path)

    elif route == "paper":
        # Index to papers collection
        _index_text_file(file_path, "papers", project)

    elif route == "document":
        _index_text_file(file_path, "documents", project)

    logger.info("tool_event: processed file %s (route=%s, project=%s)", file_path, route, project)


def process_mcp_download(item: dict, db_conn: sqlite3.Connection) -> None:
    """Process an 'mcp_download' queue item — log download event as a finding."""
    payload = json.loads(item["payload"]) if isinstance(item["payload"], str) else item["payload"]
    tool_name = payload.get("tool", "")
    project = item.get("project", "")
    response = payload.get("response", {})

    description = f"Downloaded via {tool_name}"
    if response.get("filename"):
        description += f": {response['filename']}"

    write_obsidian_entry(project, {
        "text": description,
        "memory_type": "finding",
    })
    logger.info("mcp_download: logged finding for project=%s tool=%s", project, tool_name)


def _dispatch(item: dict, conn: sqlite3.Connection) -> None:
    item_type = item.get("type", "")
    if item_type == "conversation":
        process_conversation(item, conn)
    elif item_type == "tool_event":
        process_tool_event(item, conn)
    elif item_type == "mcp_download":
        process_mcp_download(item, conn)
    else:
        logger.warning("Unknown queue item type=%s — skipping", item_type)


# ---------------------------------------------------------------------------
# Consolidation
# ---------------------------------------------------------------------------

MAINTENANCE_INTERVALS = {
    "consolidate": 86400,  # daily
    "backfill": 3600,      # hourly
}
_last_maintenance: dict[str, float] = {k: 0.0 for k in MAINTENANCE_INTERVALS}


def append_pending_rule(project: str, rule_text: str) -> None:
    """Append a candidate rule to pending_rules.md for user confirmation."""
    pending_path = Path.home() / "pony" / "ponymemory" / "pending_rules.md"
    timestamp = time.strftime("%Y-%m-%d %H:%M")
    entry = f"\n## {timestamp} [{project}]\n{rule_text}\n"
    with open(pending_path, "a", encoding="utf-8") as f:
        f.write(entry)
    logger.info("Pending rule written for project %s", project)


def run_consolidation() -> None:
    """Analyze recent corrections, extract patterns, write to pending_rules.md."""
    from embedder import search_qdrant, embed_text

    # Search for recent corrections
    query_vector = embed_text("correction user feedback error fix")
    if query_vector is None:
        logger.warning("run_consolidation: embed_text returned None — skipping")
        return

    results = search_qdrant(query_vector, project=None, top_k=50, collection="session_memories")

    # Filter to corrections only, last 30 days
    corrections = []
    cutoff = time.time() - 30 * 86400
    for r in results:
        payload = r.get("payload", {})
        if payload.get("memory_type") == "correction":
            ts = payload.get("timestamp", "")
            # Include if timestamp is missing (can't verify age) or within cutoff
            try:
                ts_float = float(ts) if ts else 0.0
            except (ValueError, TypeError):
                ts_float = 0.0
            if ts_float == 0.0 or ts_float >= cutoff:
                corrections.append(payload)

    if not corrections:
        logger.info("run_consolidation: no recent corrections found")
        return

    # Group by project
    by_project: dict[str, list[dict]] = {}
    for c in corrections:
        proj = c.get("project", "unknown")
        by_project.setdefault(proj, []).append(c)

    for project, items in by_project.items():
        if len(items) < 3:
            continue

        # Call Haiku to find patterns
        texts = "\n".join(f"- {item.get('text', '')}" for item in items)
        try:
            import anthropic
            client = anthropic.Anthropic()
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[{
                    "role": "user",
                    "content": (
                        f"以下是项目 {project} 的多次纠正记录。"
                        "分析是否有共性模式，如果有，提炼为一条通用规则（一句话）。"
                        "如果没有明显共性，回复 NONE。\n\n"
                        f"{texts}"
                    ),
                }],
            )
            pattern = response.content[0].text.strip()
            if pattern and pattern != "NONE":
                append_pending_rule(project, pattern)
        except Exception as e:
            logger.error("Consolidation Haiku call failed: %s", e)


def maybe_run_maintenance() -> None:
    """Run scheduled maintenance tasks when their intervals have elapsed."""
    now = time.time()
    for task, interval in MAINTENANCE_INTERVALS.items():
        if now - _last_maintenance[task] >= interval:
            _last_maintenance[task] = now
            if task == "consolidate":
                run_consolidation()
            elif task == "backfill":
                backfill_fallback()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main_loop() -> None:
    """Start the worker and process queue items indefinitely."""
    global _shutdown

    conn = init_db()
    write_pid_file()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info("PonyMemory worker started (pid=%d)", os.getpid())

    try:
        while not _shutdown:
            reset_stuck_records(conn, 120)

            item = claim_next_item(conn)
            if item is None:
                time.sleep(1)
                maybe_run_maintenance()
                continue

            item_dict = dict(item)
            logger.info("Processing item id=%s type=%s", item_dict.get("id"), item_dict.get("type"))

            try:
                _dispatch(item_dict, conn)
                delete_queue_item(conn, item_dict["id"])
            except Exception as exc:
                logger.error("Failed to process item id=%s: %s", item_dict.get("id"), exc)
                mark_failed(conn, item_dict["id"], str(exc))

    finally:
        remove_pid_file()
        logger.info("PonyMemory worker stopped")


if __name__ == "__main__":
    main_loop()
