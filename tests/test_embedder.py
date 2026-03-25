"""Tests for embedder.py — BGE-M3 + Qdrant writer.

All tests use unittest.mock; no real services are called.
"""

import json
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch


class TestEmbedText(unittest.TestCase):
    def _make_response(self, payload: dict, status: int = 200):
        """Build a mock HTTP response object."""
        body = json.dumps(payload).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.status = status
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_embed_text_returns_vector(self):
        """embed_text should return the first embedding on success."""
        expected_vector = [0.1, 0.2, 0.3]
        mock_resp = self._make_response({"embeddings": [expected_vector]})

        with patch("urllib.request.urlopen", return_value=mock_resp):
            from embedder import embed_text
            result = embed_text("hello world")

        self.assertEqual(result, expected_vector)

    def test_embed_text_returns_none_on_failure(self):
        """embed_text should return None when urlopen raises an exception."""
        import urllib.error

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
            from embedder import embed_text
            result = embed_text("hello world")

        self.assertIsNone(result)

    def test_embed_text_returns_none_on_bad_json(self):
        """embed_text should return None when response JSON is malformed."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not-json"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            from embedder import embed_text
            result = embed_text("hello world")

        self.assertIsNone(result)


class TestCheckQdrantHealth(unittest.TestCase):
    def _make_response(self, body: bytes = b"ok", status: int = 200):
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.status = status
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_check_qdrant_health_ok(self):
        """check_qdrant_health should return 'ok' when the endpoint responds."""
        mock_resp = self._make_response(b'{"title":"qdrant"}')

        with patch("urllib.request.urlopen", return_value=mock_resp):
            from embedder import check_qdrant_health
            result = check_qdrant_health()

        self.assertEqual(result, "ok")

    def test_check_qdrant_health_down(self):
        """check_qdrant_health should return 'down' on connection error."""
        import urllib.error

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            from embedder import check_qdrant_health
            result = check_qdrant_health()

        self.assertEqual(result, "down")


class TestCheckBgeM3Health(unittest.TestCase):
    def test_check_bge_m3_health_ok(self):
        """check_bge_m3_health should return 'ok' when embed succeeds."""
        with patch("embedder.embed_text", return_value=[0.0, 0.1]):
            from embedder import check_bge_m3_health
            result = check_bge_m3_health()
        self.assertEqual(result, "ok")

    def test_check_bge_m3_health_down(self):
        """check_bge_m3_health should return 'down' when embed returns None."""
        with patch("embedder.embed_text", return_value=None):
            from embedder import check_bge_m3_health
            result = check_bge_m3_health()
        self.assertEqual(result, "down")


class TestSearchQdrant(unittest.TestCase):
    def _make_response(self, payload):
        body = json.dumps(payload).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_search_qdrant_returns_results(self):
        """search_qdrant should return the result list on success."""
        fake_results = [{"id": "abc", "score": 0.9, "payload": {"text": "foo"}}]
        mock_resp = self._make_response({"result": fake_results})

        with patch("urllib.request.urlopen", return_value=mock_resp):
            from embedder import search_qdrant
            result = search_qdrant([0.1, 0.2, 0.3], project="test_project")

        self.assertEqual(result, fake_results)

    def test_search_qdrant_returns_empty_on_error(self):
        """search_qdrant should return [] on connection error."""
        import urllib.error

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            from embedder import search_qdrant
            result = search_qdrant([0.1, 0.2, 0.3], project="test_project")

        self.assertEqual(result, [])


class TestStoreQdrantMemory(unittest.TestCase):
    def _make_response(self, payload):
        body = json.dumps(payload).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_store_qdrant_memory_returns_point_id(self):
        """store_qdrant_memory should return a UUID string on success."""
        mock_resp = self._make_response({"result": {"operation_id": 1, "status": "completed"}})

        with patch("urllib.request.urlopen", return_value=mock_resp):
            from embedder import store_qdrant_memory
            point_id = store_qdrant_memory(
                fact={"text": "test fact", "project": "ponymemory"},
                vector=[0.1, 0.2, 0.3],
            )

        self.assertIsNotNone(point_id)
        # Should be a valid UUID string
        import uuid
        uuid.UUID(point_id)  # raises ValueError if invalid

    def test_store_qdrant_memory_returns_none_on_error(self):
        """store_qdrant_memory should return None on connection error."""
        import urllib.error

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            from embedder import store_qdrant_memory
            result = store_qdrant_memory(
                fact={"text": "test fact", "project": "ponymemory"},
                vector=[0.1, 0.2, 0.3],
            )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
