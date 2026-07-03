import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gemini_export.manifest import _init_manifest
from gemini_export.search import (
    _add_tags,
    _ensure_metadata_row,
    _index_chat_for_search,
    _list_tags,
    _resolve_chat_id,
    _search_chats,
    _set_project,
    _toggle_favorite,
)


class TestFTS5Search:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.output_dir = Path(self.tmp)
        self.conn = _init_manifest(self.output_dir)

    def teardown_method(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_index_and_search(self):
        _index_chat_for_search(self.conn, "abc123", "Python project", [
            {"role": "user", "text": "How to use asyncio?"},
            {"role": "model", "text": "asyncio is a library for async programming."},
        ])
        results = _search_chats(self.conn, "asyncio")
        assert len(results) == 1
        assert results[0]["cid"] == "abc123"

    def test_search_no_results(self):
        results = _search_chats(self.conn, "nonexistent")
        assert len(results) == 0

    def test_search_title_only(self):
        _index_chat_for_search(self.conn, "xyz", "Machine Learning tips", [])
        results = _search_chats(self.conn, "machine")
        assert len(results) == 1


class TestMetadataCRUD:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.output_dir = Path(self.tmp)
        self.conn = _init_manifest(self.output_dir)

    def teardown_method(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_tags(self):
        _ensure_metadata_row(self.conn, "abc123")
        _add_tags(self.conn, "abc123", ["AI", "python"])
        _add_tags(self.conn, "abc123", ["coding", "AI"])
        tags = _list_tags(self.conn)
        assert "ai" in tags
        assert "python" in tags
        assert "coding" in tags
        assert len(tags) == 3

    def test_set_project(self):
        _ensure_metadata_row(self.conn, "abc123")
        _set_project(self.conn, "abc123", "MyProject")
        row = self.conn.execute(
            "SELECT project FROM chat_metadata WHERE chat_id = ?", ("abc123",)
        ).fetchone()
        assert row[0] == "MyProject"

    def test_toggle_favorite(self):
        _ensure_metadata_row(self.conn, "abc123")
        assert _toggle_favorite(self.conn, "abc123") == True
        assert _toggle_favorite(self.conn, "abc123") == False

    def test_empty_tags(self):
        tags = _list_tags(self.conn)
        assert tags == []


class TestResolveChatId:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.output_dir = Path(self.tmp)
        self.conn = _init_manifest(self.output_dir)
        self.conn.execute(
            "INSERT INTO exports (chat_id, title) VALUES (?, ?)",
            ("abc123def456ghi789jkl012mno345pqr", "Test Chat"),
        )
        self.conn.commit()

    def teardown_method(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_prefix_resolution(self):
        result = _resolve_chat_id(self.conn, "abc123de")
        assert result == "abc123def456ghi789jkl012mno345pqr"

    def test_no_match(self):
        result = _resolve_chat_id(self.conn, "zzzzzzzz")
        assert result is None

    def test_full_id(self):
        result = _resolve_chat_id(self.conn, "abc123def456ghi789jkl012mno345pqr")
        assert result == "abc123def456ghi789jkl012mno345pqr"
