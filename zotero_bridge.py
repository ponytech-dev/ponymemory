"""Zotero bridge for PonyMemory — sync Zotero items to Qdrant papers collection.

This module does NOT use pyzotero directly. Instead, it provides utilities
that work with the Zotero MCP tools already configured in Claude Code.
The actual Zotero interaction happens through Claude Code's MCP calls.

For batch indexing, it reads Zotero's local SQLite database directly.
"""
import json
import os
import sqlite3
import urllib.request

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
EMBED_URL = os.environ.get("EMBED_URL", "http://localhost:8999")
ZOTERO_DATA_DIR = os.path.expanduser("~/Zotero")  # Default Zotero data directory


def find_zotero_db():
    """Find the Zotero SQLite database."""
    db_path = os.path.join(ZOTERO_DATA_DIR, "zotero.sqlite")
    if os.path.exists(db_path):
        return db_path
    # Try alternate location
    alt_path = os.path.expanduser("~/Library/Application Support/Zotero/zotero.sqlite")
    if os.path.exists(alt_path):
        return alt_path
    return None


def list_recent_items(days=30, limit=50):
    """List recent Zotero items directly from the database.

    Returns list of dicts with: key, title, creators, date, itemType
    """
    db_path = find_zotero_db()
    if not db_path:
        return []

    try:
        # Zotero locks its DB when running, use immutable mode
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        cursor = conn.execute("""
            SELECT i.key, i.itemTypeID,
                   MAX(CASE WHEN f.fieldName = 'title' THEN iv.value END) as title,
                   MAX(CASE WHEN f.fieldName = 'date' THEN iv.value END) as date,
                   MAX(CASE WHEN f.fieldName = 'DOI' THEN iv.value END) as doi,
                   MAX(CASE WHEN f.fieldName = 'abstractNote' THEN iv.value END) as abstract
            FROM items i
            LEFT JOIN itemData id ON i.itemID = id.itemID
            LEFT JOIN itemDataValues iv ON id.valueID = iv.valueID
            LEFT JOIN fields f ON id.fieldID = f.fieldID
            WHERE i.itemTypeID NOT IN (1, 14)  -- Exclude notes and attachments
            GROUP BY i.itemID
            ORDER BY i.dateModified DESC
            LIMIT ?
        """, (limit,))

        items = []
        for row in cursor:
            items.append({
                "key": row["key"],
                "title": row["title"] or "",
                "date": row["date"] or "",
                "doi": row["doi"] or "",
                "abstract": row["abstract"] or "",
            })

        conn.close()
        return items
    except Exception:
        return []


def index_zotero_item_to_qdrant(item):
    """Index a single Zotero item to Qdrant papers collection.

    Args:
        item: dict with title, abstract, doi, key

    Returns:
        point_id or None
    """
    text = f"{item.get('title', '')}. {item.get('abstract', '')}"
    if len(text.strip()) < 20:
        return None

    # Embed
    try:
        payload = json.dumps({"texts": [text[:2000]]}).encode("utf-8")
        req = urllib.request.Request(
            f"{EMBED_URL}/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            vector = result.get("embeddings", [None])[0]
    except Exception:
        return None

    if not vector:
        return None

    # Store in Qdrant
    import uuid
    point_id = str(uuid.uuid4())
    qdrant_payload = {
        "text": text[:500],
        "memory_type": "paper",
        "project": "zotero",
        "tags": ["zotero", item.get("doi", "")],
        "source_path": f"zotero://{item.get('key', '')}",
        "zotero_key": item.get("key", ""),
        "doi": item.get("doi", ""),
    }

    body = json.dumps({
        "points": [{"id": point_id, "vector": vector, "payload": qdrant_payload}]
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{QDRANT_URL}/collections/papers/points",
            data=body,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        urllib.request.urlopen(req, timeout=5)
        return point_id
    except Exception:
        return None


def sync_recent_to_qdrant(days=30, limit=50):
    """Sync recent Zotero items to Qdrant. Returns count of newly indexed items."""
    items = list_recent_items(days=days, limit=limit)
    indexed = 0
    for item in items:
        if item.get("title"):
            point_id = index_zotero_item_to_qdrant(item)
            if point_id:
                indexed += 1
    return indexed
