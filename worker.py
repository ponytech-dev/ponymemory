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
from obsidian_writer import write_obsidian_entry

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


def _dispatch(item: dict, conn: sqlite3.Connection) -> None:
    item_type = item.get("type", "")
    if item_type == "conversation":
        process_conversation(item, conn)
    elif item_type in ("tool_event", "mcp_download"):
        pass  # Phase 2 — will be handled by process_tool_event / process_mcp_download
    else:
        logger.warning("Unknown queue item type=%s — skipping", item_type)


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
                backfill_fallback()
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
