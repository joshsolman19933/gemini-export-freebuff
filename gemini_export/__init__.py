"""
Gemini Chat Exporter — Core package.
=====================================
Refactored from the monolithic export.py into focused modules.
"""

from gemini_export.cli import main, parse_args
from gemini_export.config import load_config
from gemini_export.cookie_store import (
    KEYRING_AVAILABLE,
    delete_cookies,
    get_cookies,
    set_cookies,
)
from gemini_export.exporter import _export_single_chat, export_all_chats
from gemini_export.formatters import (
    export_chat_to_csv,
    export_chat_to_html,
    export_chat_to_json,
    export_chat_to_markdown,
    generate_all_chats_html,
)
from gemini_export.image_utils import _download_turn_images
from gemini_export.manifest import (
    _init_manifest,
    _manifest_get_stats,
    _manifest_mark_exported,
    _manifest_mark_failed,
    _manifest_needs_export,
)
from gemini_export.pagination import _fetch_chats_paginated, _retry_read_chat
from gemini_export.pdf_formatter import export_chat_to_pdf, generate_all_chats_pdf

# Re-export commonly used utilities for backward compatibility
from gemini_export.search import (
    _add_tags,
    _browse_chats,
    _ensure_metadata_row,
    _index_chat_for_search,
    _list_tags,
    _reindex_all_chats,
    _resolve_chat_id,
    _search_chats,
    _set_project,
    _toggle_favorite,
)
from gemini_export.utils import (
    _extract_image_metadata,
    _guess_image_ext,
    already_exported,
    extract_turn_data,
    filter_chats,
    format_timestamp,
    list_chats_only,
    parse_date,
    sanitize_filename,
)

__all__ = [
    "_download_turn_images",
    "_add_tags",
    "_browse_chats",
    "_ensure_metadata_row",
    "_index_chat_for_search",
    "_list_tags",
    "_reindex_all_chats",
    "_resolve_chat_id",
    "_search_chats",
    "_set_project",
    "_toggle_favorite",
    "_fetch_chats_paginated",
    "_retry_read_chat",
    "main",
    "parse_args",
    "_export_single_chat",
    "export_all_chats",
    "load_config",
    "export_chat_to_csv",
    "export_chat_to_html",
    "export_chat_to_json",
    "export_chat_to_markdown",
    "generate_all_chats_html",
    "export_chat_to_pdf",
    "generate_all_chats_pdf",
    "_init_manifest",
    "_manifest_get_stats",
    "_manifest_mark_exported",
    "_manifest_mark_failed",
    "_manifest_needs_export",
    "_extract_image_metadata",
    "_guess_image_ext",
    "already_exported",
    "extract_turn_data",
    "filter_chats",
    "format_timestamp",
    "list_chats_only",
    "parse_date",
    "sanitize_filename",
    "get_cookies",
    "set_cookies",
    "delete_cookies",
    "KEYRING_AVAILABLE",
]

# Search and metadata functions will be re-exported from their modules
# as they are extracted in later phases.
