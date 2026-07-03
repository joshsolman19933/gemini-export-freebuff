#!/usr/bin/env python3
"""
Chat Import — Platform conversation importer
=============================================
ChatGPT és Claude export JSON fájlok importálása a Gemini Chat Exporter
formátumába, automatikus manifest DB indexeléssel.

Támogatott források:
    - chatgpt: OpenAI ChatGPT export (conversations.json)
    - claude: Anthropic Claude export (conversations.json)

Használat (CLI):
    python chat_import.py --source chatgpt conversations.json --output ./exports
    python chat_import.py --source claude conversations.json --output ./exports

Használat (API):
    from gemini_export.chat_import import import_chatgpt, import_claude
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from gemini_export.logging_config import get_logger
from gemini_export.manifest import _init_manifest, _manifest_mark_exported
from gemini_export.search import _index_chat_for_search, _ensure_metadata_row
from gemini_export.utils import format_timestamp, sanitize_filename

logger = get_logger(__name__)

# Default exports directory
DEFAULT_OUTPUT = os.getenv("EXPORT_OUTPUT_DIR", "./exports")


# ─── ChatGPT import ──────────────────────────────────────────────────────────


def _parse_chatgpt_conversation(conv: dict) -> dict | None:
    """Egy ChatGPT konverzáció átalakítása Gemini formátumra.

    A ChatGPT conversations.json formátuma:
        {
            "title": "...",
            "create_time": 1234567890,
            "current_node": "uuid",
            "mapping": {
                "uuid-1": {
                    "message": {"author": {"role": "user"}, "content": {"parts": ["text"]}},
                    "parent": null / "uuid-previous",
                    "children": ["uuid-2"]
                },
                ...
            }
        }

    Args:
        conv: Egy ChatGPT konverzáció objektum.

    Returns:
        Gemini formátumú dict, vagy None ha nincs értelmezhető üzenet.
    """
    mapping = conv.get("mapping", {})
    current_node = conv.get("current_node", "")
    title = conv.get("title", "Untitled")

    if not mapping or not current_node:
        return None

    # A mapping fa bejárása: current_node-tól visszafelé a parent-eken át a gyökérig
    turns = []
    seen = set()
    node_id = current_node

    while node_id and node_id not in seen:
        seen.add(node_id)
        node = mapping.get(node_id)
        if not node:
            break

        message = node.get("message")
        if message:
            author = (message.get("author") or {}).get("role", "")
            content = message.get("content", {})
            parts = content.get("parts", [])

            text = ""
            # A ChatGPT parts lehet string lista vagy multimodal (text + image_url objects)
            for part in parts:
                if isinstance(part, str):
                    text += part
                elif isinstance(part, dict) and "text" in part:
                    text += part["text"]
                # Képek kihagyása (a Gemini formátum eltérő képkezelést használ)

            if text.strip():
                # ChatGPT role mapping → Gemini role
                role_map = {
                    "user": "USER",
                    "assistant": "MODEL",
                    "system": "MODEL",  # system üzenetek model-ként
                    "tool": "MODEL",
                }
                role = role_map.get(author, "MODEL")
                turns.append({"role": role, "text": text.strip(), "images": []})

        node_id = node.get("parent")

    if not turns:
        return None

    # A mapping bejárása current_node → parent láncon newest→oldest sorrendben
    # történik, ami pont a Gemini formátum (formatters.py reversed() hívással
    # jeleníti meg időrendben). Nincs szükség további rendezésre.

    # Generálunk egyedi CID-t a chat-nek
    cid = str(uuid.uuid4()).replace("-", "")

    return {
        "cid": cid,
        "title": title,
        "exported_at": format_timestamp(),
        "turns": turns,
        "source": "chatgpt",
    }


def import_chatgpt_json(
    input_path: Path,
    output_dir: Path | None = None,
) -> dict:
    """ChatGPT conversations.json importálása Gemini formátumba.

    Args:
        input_path: A ChatGPT conversations.json fájl elérési útja.
        output_dir: Kimeneti könyvtár (alap: EXPORT_OUTPUT_DIR vagy ./exports).
                     Ha None, csak a parse eredményt adja vissza fájlba írás nélkül.

    Returns:
        {"total": int, "imported": int, "failed": int, "skipped": int}
    """
    if output_dir is None:
        output_dir = Path(DEFAULT_OUTPUT)

    if not input_path.exists():
        raise FileNotFoundError(f"A fájl nem található: {input_path}")

    raw = json.loads(input_path.read_text(encoding="utf-8"))
    conversations = raw if isinstance(raw, list) else [raw]

    output_dir.mkdir(parents=True, exist_ok=True)
    json_dir = output_dir / "json"
    json_dir.mkdir(parents=True, exist_ok=True)

    total = len(conversations)
    imported = 0
    failed = 0
    skipped = 0

    # Manifest DB inicializálása
    try:
        conn = _init_manifest(output_dir)
    except Exception as e:
        logger.warning("Manifest DB not available for import: %s", e)
        conn = None

    for conv in conversations:
        try:
            gemini_data = _parse_chatgpt_conversation(conv)
            if gemini_data is None:
                skipped += 1
                continue

            title = gemini_data.get("title", "Untitled")
            cid = gemini_data["cid"]
            turns = gemini_data.get("turns", [])

            # JSON fájlba írás
            filename = f"{sanitize_filename(title)}_{cid[:8]}.json"
            filepath = json_dir / filename
            filepath.write_text(
                json.dumps(gemini_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # Manifest DB frissítése
            if conn is not None:
                try:
                    _manifest_mark_exported(
                        conn, cid, title, len(turns), ["json"], 0,
                    )
                    _ensure_metadata_row(conn, cid)
                    # Source platform beállítása
                    conn.execute(
                        "UPDATE chat_metadata SET project = COALESCE(project, ?) WHERE chat_id = ?",
                        ("chatgpt", cid),
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO chat_metadata (chat_id, notes, updated_at) "
                        "VALUES (?, ?, ?)",
                        (cid, f"Imported from ChatGPT on {format_timestamp()}", format_timestamp()),
                    )
                    _index_chat_for_search(conn, cid, title, turns)
                    conn.commit()
                except Exception as e:
                    logger.warning("Manifest update failed for %s: %s", title[:50], e)

            imported += 1

        except Exception as e:
            logger.warning("ChatGPT import failed: %s", e)
            failed += 1

    if conn is not None:
        conn.close()

    return {"total": total, "imported": imported, "failed": failed, "skipped": skipped}


# ─── Claude import ───────────────────────────────────────────────────────────


def _parse_claude_conversation(conv: dict) -> dict | None:
    """Egy Claude konverzáció átalakítása Gemini formátumra.

    A Claude export formátuma (egyszerűsített):
        {
            "name": "Conversation Title",
            "created_at": "2024-01-01T...",
            "chat_messages": [
                {"sender": "human", "text": "Hello"},
                {"sender": "assistant", "text": "Hi there!"},
                ...
            ]
        }

    Args:
        conv: Egy Claude konverzáció objektum.

    Returns:
        Gemini formátumú dict, vagy None ha nincs értelmezhető üzenet.
    """
    title = conv.get("name") or conv.get("title", "Untitled")
    messages = conv.get("chat_messages", [])

    if not messages:
        return None

    turns = []
    for msg in messages:
        sender = msg.get("sender", "")
        text = msg.get("text", "").strip()
        if not text:
            continue

        role_map = {
            "human": "USER",
            "assistant": "MODEL",
            "system": "MODEL",
        }
        role = role_map.get(sender, "MODEL")
        turns.append({"role": role, "text": text, "images": []})

    if not turns:
        return None

    # Claude messages are typically in chronological order
    # Reverse for Gemini format (newest first)
    turns.reverse()

    cid = str(uuid.uuid4()).replace("-", "")

    return {
        "cid": cid,
        "title": title,
        "exported_at": format_timestamp(),
        "turns": turns,
        "source": "claude",
    }


def import_claude_json(
    input_path: Path,
    output_dir: Path | None = None,
) -> dict:
    """Claude conversations.json importálása Gemini formátumba.

    Args:
        input_path: A Claude export JSON fájl elérési útja.
        output_dir: Kimeneti könyvtár (alap: EXPORT_OUTPUT_DIR vagy ./exports).

    Returns:
        {"total": int, "imported": int, "failed": int, "skipped": int}
    """
    if output_dir is None:
        output_dir = Path(DEFAULT_OUTPUT)

    if not input_path.exists():
        raise FileNotFoundError(f"A fájl nem található: {input_path}")

    raw = json.loads(input_path.read_text(encoding="utf-8"))
    conversations = raw if isinstance(raw, list) else [raw]

    output_dir.mkdir(parents=True, exist_ok=True)
    json_dir = output_dir / "json"
    json_dir.mkdir(parents=True, exist_ok=True)

    total = len(conversations)
    imported = 0
    failed = 0
    skipped = 0

    try:
        conn = _init_manifest(output_dir)
    except Exception as e:
        logger.warning("Manifest DB not available for import: %s", e)
        conn = None

    for conv in conversations:
        try:
            gemini_data = _parse_claude_conversation(conv)
            if gemini_data is None:
                skipped += 1
                continue

            title = gemini_data.get("title", "Untitled")
            cid = gemini_data["cid"]
            turns = gemini_data.get("turns", [])

            filename = f"{sanitize_filename(title)}_{cid[:8]}.json"
            filepath = json_dir / filename
            filepath.write_text(
                json.dumps(gemini_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            if conn is not None:
                try:
                    _manifest_mark_exported(conn, cid, title, len(turns), ["json"], 0)
                    _ensure_metadata_row(conn, cid)
                    conn.execute(
                        "UPDATE chat_metadata SET project = COALESCE(project, ?) WHERE chat_id = ?",
                        ("claude", cid),
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO chat_metadata (chat_id, notes, updated_at) "
                        "VALUES (?, ?, ?)",
                        (cid, f"Imported from Claude on {format_timestamp()}", format_timestamp()),
                    )
                    _index_chat_for_search(conn, cid, title, turns)
                    conn.commit()
                except Exception as e:
                    logger.warning("Manifest update failed for %s: %s", title[:50], e)

            imported += 1

        except Exception as e:
            logger.warning("Claude import failed: %s", e)
            failed += 1

    if conn is not None:
        conn.close()

    return {"total": total, "imported": imported, "failed": failed, "skipped": skipped}


# ─── Auto-detect import ─────────────────────────────────────────────────────


def import_auto(
    input_path: Path,
    output_dir: Path | None = None,
) -> dict:
    """Automatikusan detektálja a forrás platformot és importál.

    A detektálás a JSON struktúra alapján történik:
        - Ha a konverzációkban van "mapping" és "current_node" → ChatGPT
        - Ha a konverzációkban van "chat_messages" → Claude
        - Egyébként hibát dob

    Args:
        input_path: Az importálandó JSON fájl.
        output_dir: Kimeneti könyvtár.

    Returns:
        Import statisztikák dict.
    """
    if not input_path.exists():
        raise FileNotFoundError(f"A fájl nem található: {input_path}")

    raw = json.loads(input_path.read_text(encoding="utf-8"))
    conversations = raw if isinstance(raw, list) else [raw]

    if not conversations:
        raise ValueError("Az import fájl üres — nincsenek konverzációk.")

    first = conversations[0]

    # ChatGPT detektálás: van "mapping" és "current_node"
    if "mapping" in first and "current_node" in first:
        logger.info("Auto-detektált forrás: ChatGPT")
        return import_chatgpt_json(input_path, output_dir)

    # Claude detektálás: van "chat_messages"
    if "chat_messages" in first:
        logger.info("Auto-detektált forrás: Claude")
        return import_claude_json(input_path, output_dir)

    raise ValueError(
        "Nem sikerült detektálni a forrás platformot. "
        "Támogatott: ChatGPT (mapping+current_node), Claude (chat_messages). "
        "Használd a --source chatgpt vagy --source claude opciót."
    )


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="ChatGPT/Claude export JSON importálása Gemini formátumba.",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="A bemeneti JSON fájl (conversations.json).",
    )
    parser.add_argument(
        "--source",
        choices=["chatgpt", "claude", "auto"],
        default="auto",
        help="Forrás platform (alap: auto-detektálás).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(DEFAULT_OUTPUT),
        help=f"Kimeneti könyvtár (alap: {DEFAULT_OUTPUT}).",
    )
    args = parser.parse_args()

    import_fn = {
        "chatgpt": import_chatgpt_json,
        "claude": import_claude_json,
        "auto": import_auto,
    }[args.source]

    try:
        result = import_fn(args.input, args.output)
        print(f"\n📥 Import kész!")
        print(f"   Összes: {result['total']}")
        print(f"   ✅ Importálva: {result['imported']}")
        print(f"   ❌ Sikertelen: {result['failed']}")
        print(f"   ⏭️ Kihagyva: {result['skipped']}")
    except Exception as e:
        print(f"❌ Import hiba: {e}")
        raise SystemExit(1)
