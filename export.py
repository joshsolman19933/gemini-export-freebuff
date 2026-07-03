#!/usr/bin/env python3
"""
Gemini Chat Exporter
--------------------
Exportálja az ÖSSZES Gemini beszélgetést a gemini.google.com felületről
a gemini_webapi Python csomag segítségével.

Támogatott formátumok: JSON, Markdown, HTML, CSV

Használat:
    python export.py                        # Minden formátum
    python export.py --format json          # Csak JSON
    python export.py --format markdown      # Csak Markdown
    python export.py --format html          # Csak HTML
    python export.py --format csv           # Csak CSV
    python export.py --output ./my_exports  # Egyedi kimeneti könyvtár
    python export.py --delay 1.0            # 1 mp késleltetés a kérések között
    python export.py --no-resume            # Újrakezdi az exportot (nem hagyja ki a már létező fájlokat)
    python export.py --auto-cookies         # browser-cookie3 használata cookie-k automatikus importálásához
    python export.py --max-chats 500        # Maximum ennyi beszélgetést kér le (alapértelmezett: 2000)
"""

# Forward compatibility re-exports — minden, amit korábban innen importáltak
from gemini_export.cli import main, parse_args
from gemini_export.exporter import _export_single_chat, export_all_chats
from gemini_export.formatters import (
    _build_html_chat_content,
    _html_escape,
    _render_text_with_code_blocks,
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

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
