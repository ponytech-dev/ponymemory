"""Tests for worker.py — PonyMemory v2 main loop."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# Ensure worker is importable from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import db as db_module
from worker import process_conversation


def _make_item(payload, project="test_project", session_id="sess_001", type_="conversation"):
    """Helper to build a fake queue item dict."""
    return {
        "id": 1,
        "session_id": session_id,
        "project": project,
        "type": type_,
        "payload": json.dumps(payload) if not isinstance(payload, str) else payload,
        "status": "processing",
        "created_at": 1000.0,
        "claimed_at": 1001.0,
        "error_message": None,
        "retry_count": 0,
    }


def _make_conn():
    """Create a real in-memory SQLite db for testing store_raw_observation etc."""
    return db_module.init_db(":memory:")


class TestProcessConversationSkipsShort(unittest.TestCase):
    """payload text < 50 chars → extract_facts must NOT be called."""

    def test_process_conversation_skips_short(self):
        conn = _make_conn()
        # A conversation that formats to less than 50 chars total
        payload = [
            {
                "type": "user",
                "message": {"content": [{"type": "text", "text": "Hi"}]},
            }
        ]
        item = _make_item(payload)

        with patch("worker.extract_facts") as mock_extract:
            process_conversation(item, conn)
            mock_extract.assert_not_called()


class TestProcessConversationStoresQualityFact(unittest.TestCase):
    """High-quality fact (score >= 0.6) → store_qdrant_memory + write_obsidian_entry called."""

    def test_process_conversation_stores_quality_fact(self):
        conn = _make_conn()
        payload = [
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "We decided to use Qdrant as the vector store for all long-term memory storage.",
                        }
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Confirmed. Qdrant will be the primary vector store. This decision is final.",
                        }
                    ]
                },
            },
        ]
        item = _make_item(payload)

        quality_fact = {
            "text": "Use Qdrant as the vector store for all long-term memory storage.",
            "memory_type": "decision",
            "tags": ["qdrant", "memory"],
            "quality_score": 0.9,
        }
        fake_vector = [0.1] * 10

        with patch("worker.extract_facts", return_value=[quality_fact]) as mock_extract, \
             patch("worker.filter_by_quality", return_value=([quality_fact], [])) as mock_filter, \
             patch("worker.embed_text", return_value=fake_vector) as mock_embed, \
             patch("worker.search_qdrant", return_value=[]) as mock_search, \
             patch("worker.store_qdrant_memory") as mock_store, \
             patch("worker.write_obsidian_entry") as mock_obsidian:

            process_conversation(item, conn)

            mock_extract.assert_called_once()
            mock_store.assert_called_once_with(quality_fact, fake_vector)
            mock_obsidian.assert_called_once_with("test_project", quality_fact)


class TestProcessConversationDedupSkips(unittest.TestCase):
    """If search_qdrant returns a result with score > 0.9, store_qdrant_memory must NOT be called."""

    def test_process_conversation_dedup_skips(self):
        conn = _make_conn()
        payload = [
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "We decided to use Qdrant as the vector store for all long-term memory storage.",
                        }
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Confirmed. Qdrant will be the primary vector store. This decision is final.",
                        }
                    ]
                },
            },
        ]
        item = _make_item(payload)

        quality_fact = {
            "text": "Use Qdrant as the vector store.",
            "memory_type": "decision",
            "tags": ["qdrant"],
            "quality_score": 0.85,
        }
        fake_vector = [0.1] * 10
        # Simulate a very similar existing memory
        similar_result = {"score": 0.95, "payload": {"text": "Use Qdrant for memory."}}

        with patch("worker.extract_facts", return_value=[quality_fact]), \
             patch("worker.filter_by_quality", return_value=([quality_fact], [])), \
             patch("worker.embed_text", return_value=fake_vector), \
             patch("worker.search_qdrant", return_value=[similar_result]) as mock_search, \
             patch("worker.store_qdrant_memory") as mock_store, \
             patch("worker.write_obsidian_entry") as mock_obsidian:

            process_conversation(item, conn)

            # Dedup: should NOT store
            mock_store.assert_not_called()
            mock_obsidian.assert_not_called()


class TestProcessConversationLowQualityToRaw(unittest.TestCase):
    """Low-quality fact (score < 0.6) → store_raw_observation called."""

    def test_process_conversation_low_quality_to_raw(self):
        conn = _make_conn()
        payload = [
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "We decided to use Qdrant as the vector store for all long-term memory storage.",
                        }
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Confirmed. Qdrant will be the primary vector store. This decision is final.",
                        }
                    ]
                },
            },
        ]
        item = _make_item(payload)

        low_quality_fact = {
            "text": "Something vague was mentioned.",
            "memory_type": "finding",
            "tags": [],
            "quality_score": 0.3,
        }

        with patch("worker.extract_facts", return_value=[low_quality_fact]), \
             patch("worker.filter_by_quality", return_value=([], [low_quality_fact])), \
             patch("worker.embed_text") as mock_embed, \
             patch("worker.store_qdrant_memory") as mock_store, \
             patch("worker.write_obsidian_entry") as mock_obsidian:

            process_conversation(item, conn)

            # Low quality: should NOT touch Qdrant or Obsidian
            mock_embed.assert_not_called()
            mock_store.assert_not_called()
            mock_obsidian.assert_not_called()

            # But raw_observations table should have an entry
            cursor = conn.execute("SELECT COUNT(*) AS cnt FROM raw_observations")
            count = cursor.fetchone()["cnt"]
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
