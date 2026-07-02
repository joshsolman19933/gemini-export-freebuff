import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from export import sanitize_filename, parse_date, filter_chats, _guess_image_ext, _extract_image_metadata
from datetime import datetime, timezone


class TestSanitizeFilename:
    def test_basic(self):
        assert sanitize_filename("Hello World") == "Hello World"

    def test_special_chars(self):
        result = sanitize_filename('test:file?name<>"|*')
        assert ":" not in result
        assert "?" not in result
        assert "<" not in result

    def test_long_name(self):
        long_name = "A" * 100
        result = sanitize_filename(long_name, max_length=80)
        assert len(result) <= 80

    def test_empty(self):
        assert sanitize_filename("") == "untitled"


class TestParseDate:
    def test_date_only(self):
        ts = parse_date("2024-01-15")
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15

    def test_datetime_t(self):
        ts = parse_date("2024-06-01T12:30:00")
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        assert dt.hour == 12
        assert dt.minute == 30

    def test_invalid(self):
        try:
            parse_date("not-a-date")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass


class TestFilterChats:
    class FakeChat:
        def __init__(self, title, timestamp):
            self.title = title
            self.timestamp = timestamp

    def test_no_filters(self):
        chats = [self.FakeChat("A", 100), self.FakeChat("B", 200)]
        result, stats = filter_chats(chats)
        assert len(result) == 2

    def test_keyword_filter(self):
        chats = [self.FakeChat("Python project", 100), self.FakeChat("Java code", 200)]
        result, stats = filter_chats(chats, keyword="python")
        assert len(result) == 1
        assert result[0].title == "Python project"

    def test_date_filter(self):
        chats = [self.FakeChat("Old", 50), self.FakeChat("New", 150)]
        result, stats = filter_chats(chats, from_ts=100)
        assert len(result) == 1
        assert result[0].title == "New"


class TestGuessImageExt:
    def test_png_url(self):
        assert _guess_image_ext("https://example.com/image.png") == ".png"

    def test_url_params(self):
        assert _guess_image_ext("https://example.com/photo.jpg?w=800") == ".jpg"

    def test_jpeg(self):
        assert _guess_image_ext("https://example.com/img.jpeg") == ".jpeg"

    def test_webp(self):
        assert _guess_image_ext("https://example.com/img.webp") == ".webp"

    def test_no_ext(self):
        result = _guess_image_ext("https://example.com/generated_image")
        assert result == ".png"  # default


class TestExtractImageMetadata:
    def test_dict_input(self):
        img = {"url": "https://example.com/img.png", "alt": "test image"}
        result = _extract_image_metadata(img)
        assert result["url"] == "https://example.com/img.png"
        assert result["alt"] == "test image"

    def test_dict_no_url(self):
        img = {"alt": "no url"}
        assert _extract_image_metadata(img) is None
