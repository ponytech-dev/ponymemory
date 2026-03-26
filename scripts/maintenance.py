#!/usr/bin/env python3
"""
PonyMemory Maintenance Script
Run via crontab for periodic cleanup, dedup, and health checks.

Usage:
  python3 maintenance.py [--cleanup] [--health] [--all]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import requests
from qdrant_client import QdrantClient

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
EMBED_URL = os.environ.get("EMBED_URL", "http://localhost:8999/embed")
LOG_DIR = os.path.expanduser("~/pony/ponymemory/logs")

client = QdrantClient(url=QDRANT_URL)


def log(msg: str):
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    log_file = os.path.join(LOG_DIR, f"maintenance-{datetime.now().strftime('%Y-%m')}.log")
    with open(log_file, "a") as f:
        f.write(line + "\n")


def health_check():
    """Check all services are running."""
    checks = {}

    # Qdrant
    try:
        resp = requests.get(f"{QDRANT_URL}/collections", timeout=5)
        collections = resp.json().get("result", {}).get("collections", [])
        checks["qdrant"] = {
            "status": "ok",
            "collections": len(collections),
            "names": [c["name"] for c in collections],
        }
    except Exception as e:
        checks["qdrant"] = {"status": "error", "error": str(e)}

    # Embedding service
    try:
        resp = requests.post(
            EMBED_URL, json={"texts": ["health check"]}, timeout=10
        )
        dim = len(resp.json()["embeddings"][0])
        checks["embedding"] = {"status": "ok", "dim": dim}
    except Exception as e:
        checks["embedding"] = {"status": "error", "error": str(e)}

    # Obsidian vault
    vault = os.path.expanduser("~/pony/obsidian-vault/")
    checks["obsidian_vault"] = {
        "status": "ok" if os.path.isdir(vault) else "missing",
        "path": vault,
    }

    log(f"Health check: {json.dumps(checks, ensure_ascii=False)}")
    return checks


def cleanup_session_memories():
    """Remove session memories older than 90 days with low relevance."""
    collection = "session_memories"
    try:
        client.get_collection(collection)
    except Exception:
        log(f"Collection {collection} not found, skipping cleanup")
        return

    cutoff = (datetime.now() - timedelta(days=90)).isoformat()
    try:
        points, _ = client.scroll(
            collection_name=collection,
            limit=1000,
            with_payload=True,
        )
        old_points = []
        for p in points:
            ts = (p.payload or {}).get("timestamp", "")
            if ts and ts < cutoff:
                old_points.append(p.id)

        if old_points:
            log(f"Cleanup: found {len(old_points)} session memories older than 90 days")
            # Don't auto-delete, just report
            log(f"  IDs (first 10): {old_points[:10]}")
        else:
            log("Cleanup: no old session memories to clean")
    except Exception as e:
        log(f"Cleanup error: {e}")


def collection_stats():
    """Report collection sizes and health."""
    try:
        resp = requests.get(f"{QDRANT_URL}/collections", timeout=5)
        collections = resp.json().get("result", {}).get("collections", [])
        for c in collections:
            name = c["name"]
            try:
                info = client.get_collection(name)
                log(f"Collection '{name}': {info.points_count} points, status={info.status}")
            except Exception as e:
                log(f"Collection '{name}': error getting info - {e}")
    except Exception as e:
        log(f"Stats error: {e}")


def main():
    parser = argparse.ArgumentParser(description="PonyMemory maintenance")
    parser.add_argument("--cleanup", action="store_true", help="Run cleanup tasks")
    parser.add_argument("--health", action="store_true", help="Run health checks")
    parser.add_argument("--stats", action="store_true", help="Report collection stats")
    parser.add_argument("--all", action="store_true", help="Run all tasks")
    args = parser.parse_args()

    if not any([args.cleanup, args.health, args.stats, args.all]):
        args.all = True

    log("=== PonyMemory Maintenance Start ===")

    if args.health or args.all:
        health_check()

    if args.stats or args.all:
        collection_stats()

    if args.cleanup or args.all:
        cleanup_session_memories()

    log("=== PonyMemory Maintenance End ===")


if __name__ == "__main__":
    main()
