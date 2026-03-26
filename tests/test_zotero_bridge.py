from unittest.mock import patch, MagicMock
import json

def test_find_zotero_db_not_found():
    from zotero_bridge import find_zotero_db
    with patch("os.path.exists", return_value=False):
        assert find_zotero_db() is None

def test_list_recent_items_no_db():
    from zotero_bridge import list_recent_items
    with patch("zotero_bridge.find_zotero_db", return_value=None):
        assert list_recent_items() == []

def test_index_zotero_item_short_text():
    from zotero_bridge import index_zotero_item_to_qdrant
    result = index_zotero_item_to_qdrant({"title": "", "abstract": ""})
    assert result is None

def test_index_zotero_item_success():
    from zotero_bridge import index_zotero_item_to_qdrant
    mock_embed_resp = MagicMock()
    mock_embed_resp.read.return_value = json.dumps({"embeddings": [[0.1]*10]}).encode()
    mock_embed_resp.__enter__ = lambda s: s
    mock_embed_resp.__exit__ = MagicMock(return_value=False)

    mock_qdrant_resp = MagicMock()
    mock_qdrant_resp.__enter__ = lambda s: s
    mock_qdrant_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", side_effect=[mock_embed_resp, mock_qdrant_resp]):
        result = index_zotero_item_to_qdrant({
            "title": "Mass Spectrometry Analysis of Metabolites",
            "abstract": "This paper presents a novel approach to metabolomics.",
            "doi": "10.1234/test",
            "key": "ABC123",
        })
        assert result is not None  # Should return a UUID
