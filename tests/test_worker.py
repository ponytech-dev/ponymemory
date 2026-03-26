"""Tests for worker.py — PonyMemory v2 main loop."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# Ensure worker is importable from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import db as db_module
from worker import process_conversation, process_tool_event, process_mcp_download


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


class TestAppendPendingRule(unittest.TestCase):
    """append_pending_rule writes a correctly-formatted entry to the target file."""

    def test_append_pending_rule(self):
        from worker import append_pending_rule
        import tempfile

        with tempfile.NamedTemporaryFile(mode="r", suffix=".md", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            with patch("worker.Path") as mock_path_cls:
                # Make Path.home() / ... resolve to our temp file
                mock_path_cls.home.return_value.__truediv__.return_value.__truediv__.return_value.__truediv__.return_value = tmp_path

                # Call the real function but redirect the path
                # Easier: just call directly with a known path via monkeypatching open
                pass

            # Direct approach: patch the open call inside append_pending_rule
            with patch("builtins.open", unittest.mock.mock_open()) as mock_open, \
                 patch("worker.Path") as mock_path_cls:
                mock_path_cls.home.return_value.__truediv__.return_value.__truediv__.return_value.__truediv__.return_value = tmp_path
                append_pending_rule("test_project", "Always use parameter queries for SQL.")

            # Verify open was called for writing (append mode)
            mock_open.assert_called_once()
            call_args = mock_open.call_args
            # Second positional arg or 'mode' kwarg should be "a"
            mode = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("mode", "")
            self.assertEqual(mode, "a")

            # Verify the written content contains project name and rule text
            handle = mock_open()
            written = "".join(c.args[0] for c in handle.write.call_args_list)
            self.assertIn("test_project", written)
            self.assertIn("Always use parameter queries for SQL.", written)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_append_pending_rule_writes_to_real_temp_file(self):
        """Integration test: write to a real temp file and verify content."""
        from worker import append_pending_rule
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            with patch("worker.Path") as mock_path_cls:
                # Construct the chained __truediv__ path mock
                mock_home = MagicMock()
                mock_path_cls.home.return_value = mock_home
                # home() / "pony" / "ponymemory" / "pending_rules.md"
                mock_home.__truediv__.return_value.__truediv__.return_value.__truediv__.return_value = tmp_path

                append_pending_rule("myproject", "Use snake_case for all variable names.")

            content = tmp_path.read_text(encoding="utf-8")
            self.assertIn("myproject", content)
            self.assertIn("Use snake_case for all variable names.", content)
            # Should contain a timestamp header like "## 2026-"
            self.assertIn("##", content)
        finally:
            tmp_path.unlink(missing_ok=True)


class TestMaybeRunMaintenanceRespectsInterval(unittest.TestCase):
    """maybe_run_maintenance should not re-run tasks before their interval elapses."""

    def setUp(self):
        # Reset _last_maintenance before each test
        import worker
        for k in worker._last_maintenance:
            worker._last_maintenance[k] = 0.0

    def test_tasks_run_when_interval_elapsed(self):
        import worker
        with patch("worker.run_consolidation") as mock_consolidate, \
             patch("worker.backfill_fallback") as mock_backfill, \
             patch("worker.time") as mock_time:
            # Simulate time far in the future so all intervals are elapsed
            mock_time.time.return_value = 999999.0
            mock_time.strftime = time.strftime
            worker.maybe_run_maintenance()

        mock_consolidate.assert_called_once()
        mock_backfill.assert_called_once()

    def test_tasks_do_not_run_before_interval(self):
        import worker
        now = time.time()

        with patch("worker.run_consolidation") as mock_consolidate, \
             patch("worker.backfill_fallback") as mock_backfill, \
             patch("worker.time") as mock_time:
            mock_time.time.return_value = now
            mock_time.strftime = time.strftime

            # First call sets _last_maintenance[task] = now
            worker.maybe_run_maintenance()

            # Second call with same timestamp — interval not elapsed
            worker.maybe_run_maintenance()

        # Each task should only be called once despite two maybe_run_maintenance calls
        self.assertEqual(mock_consolidate.call_count, 1)
        self.assertEqual(mock_backfill.call_count, 1)

    def test_consolidate_not_called_before_daily_interval(self):
        import worker
        base_time = 100000.0
        worker._last_maintenance["consolidate"] = base_time
        worker._last_maintenance["backfill"] = 0.0  # backfill can run

        with patch("worker.run_consolidation") as mock_consolidate, \
             patch("worker.backfill_fallback") as mock_backfill, \
             patch("worker.time") as mock_time:
            # Only 1 hour elapsed since consolidate last ran (interval is 86400s)
            mock_time.time.return_value = base_time + 3600
            mock_time.strftime = time.strftime
            worker.maybe_run_maintenance()

        mock_consolidate.assert_not_called()
        mock_backfill.assert_called_once()


class TestProcessToolEventIgnoresPlans(unittest.TestCase):
    """tool_event with a plans/ path → classify_file returns 'ignore', no indexing."""

    def test_process_tool_event_ignores_plans(self):
        conn = _make_conn()
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tmp:
            tmp.write(b"Some plan content that is long enough to pass the 50-char check.")
            tmp_path = tmp.name

        try:
            # Construct a path that matches "*/plans/*" in router rules
            plans_path = "/some/project/plans/my_task_plan.md"
            item = _make_item(
                {"file_path": plans_path},
                project="test_project",
                type_="tool_event",
            )

            with patch("worker.embed_text") as mock_embed, \
                 patch("worker.store_qdrant_memory") as mock_store, \
                 patch("worker.write_obsidian_entry") as mock_obsidian, \
                 patch("worker.write_obsidian_milestone") as mock_milestone:

                process_tool_event(item, conn)

                mock_embed.assert_not_called()
                mock_store.assert_not_called()
                mock_obsidian.assert_not_called()
                mock_milestone.assert_not_called()
        finally:
            Path(tmp_path).unlink(missing_ok=True)


class TestProcessToolEventIndexesSpec(unittest.TestCase):
    """tool_event with specs/ path → classify_file returns 'spec',
    embed_text + store_qdrant_memory called with collection='documents'."""

    def test_process_tool_event_indexes_spec(self):
        conn = _make_conn()
        # Create a real temp file with enough content
        content = "This is a spec document. " * 10  # >50 chars, >50 bytes
        with tempfile.NamedTemporaryFile(
            suffix=".md", delete=False, mode="w", encoding="utf-8"
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            # Use a path matching "*/docs/superpowers/specs/*"
            specs_path = f"/project/docs/superpowers/specs/{Path(tmp_path).name}"

            # Patch _read_file_safe so it reads from our real temp file regardless of path
            item = _make_item(
                {"file_path": specs_path},
                project="test_project",
                type_="tool_event",
            )

            fake_vector = [0.42] * 10

            with patch("worker._read_file_safe", return_value=content) as mock_read, \
                 patch("worker.embed_text", return_value=fake_vector) as mock_embed, \
                 patch("worker.store_qdrant_memory") as mock_store, \
                 patch("worker.write_obsidian_milestone") as mock_milestone, \
                 patch("worker.write_obsidian_entry") as mock_obsidian:

                process_tool_event(item, conn)

                mock_embed.assert_called_once()
                mock_store.assert_called_once()
                # Verify collection='documents' was passed
                call_kwargs = mock_store.call_args
                self.assertEqual(call_kwargs.kwargs.get("collection") or call_kwargs[1].get("collection"), "documents")
                mock_milestone.assert_called_once()
        finally:
            Path(tmp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
