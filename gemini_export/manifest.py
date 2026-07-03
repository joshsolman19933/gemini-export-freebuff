"""Manifest adatbázis kezelés: SQLite export állapot követése."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from gemini_export.utils import format_timestamp


def _init_manifest(output_dir: Path) -> sqlite3.Connection:
    """Inicializálja az SQLite manifest adatbázist."""
    db_path = output_dir / "manifest.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS exports (
            chat_id TEXT PRIMARY KEY,
            title TEXT,
            last_exported_at TEXT,
            message_count INTEGER DEFAULT 0,
            exported_formats TEXT DEFAULT '[]',
            image_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ok',
            error_message TEXT,
            last_checked_at TEXT
        )
    """)
    # FTS5 teljes szoveges kereso
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chats_fts USING fts5(
            chat_id, title, content, tokenize='unicode61'
        )
    """)
    # Chat metaadatok (cimkek, projekt, kedvenc, jegyzet)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_metadata (
            chat_id TEXT PRIMARY KEY,
            tags TEXT DEFAULT '[]',
            project TEXT,
            category TEXT,
            is_favorite INTEGER DEFAULT 0,
            processing_status TEXT DEFAULT 'new',
            notes TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (chat_id) REFERENCES exports(chat_id)
        )
    """)
    # Schema migráció: AI oszlopok (Phase 4) — idempotens, létező oszlopnál hiba nélkül kilép
    for col, col_type in [
        ("summary", "TEXT"),
        ("auto_tags", "TEXT"),
        ("analyzed_at", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE chat_metadata ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass  # Az oszlop már létezik
    # chat_todos tábla (AI teendők)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            todo_text TEXT NOT NULL,
            category TEXT DEFAULT 'todo',
            done INTEGER DEFAULT 0,
            created_at TEXT,
            FOREIGN KEY (chat_id) REFERENCES exports(chat_id)
        )
    """)
    # Export history
    conn.execute("""
        CREATE TABLE IF NOT EXISTS export_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            output_dir TEXT,
            format TEXT,
            total_chats INTEGER DEFAULT 0,
            exported INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            skipped INTEGER DEFAULT 0,
            settings_json TEXT DEFAULT '{}'
        )
    """)
    # Export presets
    conn.execute("""
        CREATE TABLE IF NOT EXISTS export_presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            settings_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT,
            last_used_at TEXT
        )
    """)
    conn.commit()
    return conn


def _manifest_mark_exported(
    conn: sqlite3.Connection,
    cid: str,
    title: str,
    msg_count: int,
    formats: list[str],
    image_count: int = 0,
) -> None:
    """Bejegyzi a sikeres exportot a manifestbe."""
    now = format_timestamp()
    conn.execute(
        """INSERT OR REPLACE INTO exports
           (chat_id, title, last_exported_at, message_count, exported_formats, image_count, status, error_message, last_checked_at)
           VALUES (?, ?, ?, ?, ?, ?, 'ok', NULL, ?)""",
        (cid, title, now, msg_count, json.dumps(formats), image_count, now),
    )
    conn.commit()


def _manifest_mark_failed(
    conn: sqlite3.Connection,
    cid: str,
    title: str,
    error: str,
) -> None:
    """Bejegyzi a sikertelen exportot a manifestbe."""
    now = format_timestamp()
    conn.execute(
        """INSERT OR REPLACE INTO exports
           (chat_id, title, last_exported_at, error_message, status, last_checked_at)
           VALUES (?, ?, ?, ?, 'failed', ?)""",
        (cid, title, now, str(error)[:500], now),
    )
    conn.commit()


def _manifest_needs_export(
    conn: sqlite3.Connection,
    cid: str,
    formats: list[str],
    chat_timestamp: float | None = None,
) -> bool:
    """Eldönti, hogy egy chatet újra kell-e exportálni.

    Újraexportálás kell, ha:
    - Még sosem volt exportálva (nincs a manifestben)
    - Az előző export sikertelen volt
    - A chat timestamp-je újabb, mint az utolsó export (új üzenetek lehetnek)
    - Új formátumban kérjük, ami még nincs meg
    """
    row = conn.execute(
        "SELECT status, last_exported_at, exported_formats FROM exports WHERE chat_id = ?",
        (cid,),
    ).fetchone()

    if row is None:
        return True  # Soha nem volt exportálva

    status, last_exported_at, stored_formats_json = row

    if status == "failed":
        return True  # Előző export sikertelen volt

    # Timestamp alapú változásdetektálás: ha a chat frissebb, mint az utolsó export
    if chat_timestamp is not None and last_exported_at:
        try:
            last_ts = datetime.fromisoformat(last_exported_at).timestamp()
            if chat_timestamp > last_ts:
                return True  # A chat módosult az utolsó export óta
        except (ValueError, OSError):
            pass

    try:
        stored_formats = set(json.loads(stored_formats_json))
    except (json.JSONDecodeError, TypeError):
        stored_formats = set()

    if not set(formats).issubset(stored_formats):
        return True  # Új formátumot kérünk

    return False  # Minden rendben, nem kell újraexportálni


def _manifest_get_stats(conn: sqlite3.Connection) -> dict:
    """Visszaadja a manifest statisztikáit."""
    row = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) FROM exports"
    ).fetchone()
    return {
        "total": row[0] or 0,
        "ok": row[1] or 0,
        "failed": row[2] or 0,
    }
