"""BGE-M3 embedder and Qdrant writer for PonyMemory v2.

Uses only urllib.request — no third-party HTTP libraries required.
"""

import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

QDRANT_URL: str = os.environ.get("QDRANT_URL", "http://localhost:6333")
EMBED_URL: str = os.environ.get("EMBED_URL", "http://localhost:8999")
MEMORY_COLLECTION: str = "session_memories"

_DEFAULT_TIMEOUT = 10  # seconds


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: dict, timeout: int = _DEFAULT_TIMEOUT) -> Optional[dict]:
    """POST JSON to *url* and return the decoded response dict, or None on error."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _put_json(url: str, payload: dict, timeout: int = _DEFAULT_TIMEOUT) -> Optional[dict]:
    """PUT JSON to *url* and return the decoded response dict, or None on error."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def embed_text(text: str) -> Optional[list[float]]:
    """Embed *text* via the BGE-M3 service.

    POST to EMBED_URL/embed with {"texts": [text]}.
    Returns the first embedding vector, or None on any error.
    """
    url = f"{EMBED_URL}/embed"
    data = json.dumps({"texts": [text]}).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:
            body = json.loads(resp.read())
            return body["embeddings"][0]
    except Exception:
        return None


def search_qdrant(
    vector: list[float],
    project: Optional[str] = None,
    top_k: int = 3,
    collection: Optional[str] = None,
) -> list[dict]:
    """Search Qdrant for the nearest neighbours of *vector*.

    Optionally filter by *project*.  Returns a list of result dicts;
    returns [] on any error.
    """
    col = collection or MEMORY_COLLECTION
    url = f"{QDRANT_URL}/collections/{col}/points/search"

    payload: dict[str, Any] = {
        "vector": vector,
        "limit": top_k,
        "with_payload": True,
    }

    if project:
        payload["filter"] = {
            "must": [
                {
                    "key": "project",
                    "match": {"value": project},
                }
            ]
        }

    result = _post_json(url, payload)
    if result is None:
        return []
    return result.get("result", [])


def store_qdrant_memory(
    fact: dict,
    vector: list[float],
    collection: Optional[str] = None,
) -> Optional[str]:
    """Upsert a single memory point into Qdrant.

    *fact* is merged into the point payload alongside required metadata fields.
    A fresh UUID is generated for the point.

    Returns the point_id (UUID string) on success, or None on error.
    """
    col = collection or MEMORY_COLLECTION
    url = f"{QDRANT_URL}/collections/{col}/points"

    point_id = str(uuid.uuid4())

    payload: dict[str, Any] = {
        "text": fact.get("text", ""),
        "memory_type": fact.get("memory_type", "general"),
        "project": fact.get("project", ""),
        "tags": fact.get("tags", []),
        "source_path": fact.get("source_path", ""),
        "timestamp": fact.get("timestamp", datetime.now(timezone.utc).isoformat()),
    }
    # Carry over any extra keys the caller provides
    for k, v in fact.items():
        if k not in payload:
            payload[k] = v

    body = {
        "points": [
            {
                "id": point_id,
                "vector": vector,
                "payload": payload,
            }
        ]
    }

    result = _put_json(url, body)
    if result is None:
        return None
    return point_id


def check_qdrant_health() -> str:
    """Return 'ok' if Qdrant is reachable, otherwise 'down'."""
    url = f"{QDRANT_URL}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=_DEFAULT_TIMEOUT) as resp:
            resp.read()
            return "ok"
    except Exception:
        return "down"


def check_bge_m3_health() -> str:
    """Return 'ok' if the BGE-M3 embed service is responsive, otherwise 'down'."""
    result = embed_text("health check")
    return "ok" if result is not None else "down"
