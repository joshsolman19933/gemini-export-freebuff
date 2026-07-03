"""Flask API endpoint tests."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app import DEFAULT_OUTPUT, _init_manifest, app


@pytest.fixture
def client():
    """Flask test client with a temporary exports directory."""
    original_output = DEFAULT_OUTPUT
    with tempfile.TemporaryDirectory() as tmpdir:
        # Override DEFAULT_OUTPUT
        import app as app_module
        app_module.DEFAULT_OUTPUT = tmpdir

        # Initialize manifest with test data
        conn = _init_manifest(Path(tmpdir))
        _seed_test_data(conn)
        conn.close()

        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

        # Restore
        app_module.DEFAULT_OUTPUT = original_output


def _seed_test_data(conn):
    """Seed the manifest DB with test data."""
    from gemini_export.search import (
        _add_tags,
        _index_chat_for_search,
        _set_project,
        _toggle_favorite,
    )
    from gemini_export.utils import format_timestamp

    now = format_timestamp()
    conn.execute("""
        INSERT INTO exports (chat_id, title, last_exported_at, message_count, status, image_count)
        VALUES
            ('aaa111bbb222ccc333ddd444eee555ff', 'Python project chat', ?, 15, 'ok', 0),
            ('zzz999yyy888xxx777www666vvv555uu', 'Machine Learning', ?, 42, 'ok', 3),
            ('mmm333nnn444ooo555ppp666qqq777rr', 'Test failed chat', ?, 0, 'failed', 0)
    """, (now, now, now))
    conn.commit()

    _index_chat_for_search(conn, 'aaa111bbb222ccc333ddd444eee555ff', 'Python project chat', [
        {"role": "user", "text": "How to use asyncio?"},
        {"role": "model", "text": "asyncio is a library for async programming in Python."},
    ])

    _add_tags(conn, 'aaa111bbb222ccc333ddd444eee555ff', ['python', 'coding'])
    _set_project(conn, 'aaa111bbb222ccc333ddd444eee555ff', 'MyProject')
    _toggle_favorite(conn, 'zzz999yyy888xxx777www666vvv555uu')


class TestDashboardAPI:
    """Test the dashboard API endpoints."""

    def test_dashboard_page(self, client):
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert b"Gemini Tud" in resp.data

    def test_index_page(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Gemini Chat Exporter" in resp.data


class TestChatsAPI:
    """Test the /api/chats endpoint."""

    def test_chats_list(self, client):
        resp = client.get("/api/chats")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "chats" in data
        assert "total" in data
        assert data["total"] == 3
        assert len(data["chats"]) == 3

    def test_chats_pagination(self, client):
        resp = client.get("/api/chats?offset=0&limit=1")
        data = json.loads(resp.data)
        assert len(data["chats"]) == 1
        assert data["total"] == 3

    def test_chats_pagination_offset(self, client):
        resp = client.get("/api/chats?offset=2&limit=2")
        data = json.loads(resp.data)
        assert len(data["chats"]) == 1  # Only 1 remaining

    def test_chats_limit_max(self, client):
        resp = client.get("/api/chats?limit=1000")
        data = json.loads(resp.data)
        assert len(data["chats"]) == 3  # Capped at 500 but we only have 3


class TestSearchAPI:
    """Test the /api/search endpoint."""

    def test_search(self, client):
        resp = client.get("/api/search?q=asyncio")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) == 1
        assert data[0]["title"] == "Python project chat"

    def test_search_no_results(self, client):
        resp = client.get("/api/search?q=nonexistent12345")
        data = json.loads(resp.data)
        assert len(data) == 0


class TestTagsAPI:
    """Test the /api/tags endpoint."""

    def test_tags_list(self, client):
        resp = client.get("/api/tags")
        data = json.loads(resp.data)
        assert "python" in data
        assert "coding" in data
        assert len(data) == 2


class TestStatsAPI:
    """Test the /api/stats endpoints."""

    def test_stats(self, client):
        resp = client.get("/api/stats")
        data = json.loads(resp.data)
        assert data["total_chats"] == 3
        assert data["ok"] == 2
        assert data["failed"] == 1
        assert data["total_messages"] == 57
        assert data["favorite_count"] == 1

    def test_stats_history(self, client):
        resp = client.get("/api/stats/history")
        data = json.loads(resp.data)
        assert "export_timeline" in data
        assert "top_tags" in data
        assert "message_histogram" in data
        assert len(data["top_tags"]) == 2  # python, coding
