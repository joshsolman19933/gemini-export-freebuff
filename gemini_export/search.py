"""FTS5 teljes szöveges kereső, chat metaadat CRUD és interaktív böngésző."""

import json
import sqlite3
from pathlib import Path

from gemini_export.logging_config import get_logger
from gemini_export.utils import format_timestamp

logger = get_logger(__name__)


# ─── FTS5 kereső ─────────────────────────────────────────────────────

def _index_chat_for_search(conn: sqlite3.Connection, cid: str, title: str, turns: list[dict]) -> None:
    """Indexel egy chat-et az FTS5 keresőbe."""
    content_parts = [title]
    for turn in turns:
        text = turn.get("text", "")
        if text:
            content_parts.append(text)
    content = "\n".join(content_parts)
    conn.execute(
        "INSERT OR REPLACE INTO chats_fts(chat_id, title, content) VALUES (?, ?, ?)",
        (cid, title, content),
    )
    conn.commit()


def _search_chats(conn: sqlite3.Connection, query: str, limit: int = 50) -> list[dict]:
    """FTS5 teljes szöveges keresés a chat-ek között.

    Keres a címben és az üzenetek szövegében.
    """
    results = []
    try:
        rows = conn.execute(
            """SELECT c.chat_id, c.title, e.last_exported_at, e.message_count, e.status,
                      m.tags, m.project, m.is_favorite, m.processing_status
               FROM chats_fts c
               LEFT JOIN exports e ON c.chat_id = e.chat_id
               LEFT JOIN chat_metadata m ON c.chat_id = m.chat_id
               WHERE chats_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, limit),
        ).fetchall()
        for row in rows:
            cid, title, exported_at, msg_count, status, tags_json, project, fav, proc_status = row
            try:
                tags = json.loads(tags_json) if tags_json else []
            except json.JSONDecodeError:
                tags = []
            results.append({
                "cid": cid,
                "title": title or "Untitled",
                "exported_at": exported_at,
                "message_count": msg_count or 0,
                "status": status,
                "tags": tags,
                "project": project,
                "is_favorite": bool(fav),
                "processing_status": proc_status or "new",
            })
    except sqlite3.OperationalError:
        pass  # FTS5 szintaxis hiba vagy üres keresési kifejezés
    return results


def _reindex_all_chats(conn: sqlite3.Connection, output_dir: Path) -> int:
    """Újraindexeli az összes korábban exportált chat-et a JSON fájlokból."""
    rows = conn.execute(
        "SELECT chat_id, title FROM exports WHERE status = 'ok'"
    ).fetchall()
    count = 0
    for cid, title in rows:
        if not cid:
            continue
        # Keresd meg a JSON fájlt
        pattern = f"*_{cid[:8]}.json"
        json_files = list(output_dir.glob(f"json/{pattern}"))
        if not json_files:
            continue
        try:
            data = json.loads(json_files[0].read_text(encoding="utf-8"))
            turns = data.get("turns", [])
            _index_chat_for_search(conn, cid, title or data.get("title", ""), turns)
            count += 1
        except Exception:
            pass
    return count


def _resolve_chat_id(conn: sqlite3.Connection, partial: str) -> str | None:
    """Felold egy részleges chat ID-t (első 8 karakter) teljes ID-vá."""
    partial = partial.strip()
    if len(partial) >= 30:
        return partial  # Valószínűleg már teljes ID
    rows = conn.execute(
        "SELECT chat_id FROM exports WHERE chat_id LIKE ? LIMIT 2",
        (partial + "%",),
    ).fetchall()
    if len(rows) == 1:
        return rows[0][0]
    elif len(rows) > 1:
        logger.warning("Több találat is van erre a prefixre (%d). Adj meg pontosabb ID-t.", len(rows))
        return None
    else:
        logger.warning("Nincs találat erre az ID-re: %s", partial)
        return None


# ─── Chat metaadat CRUD ──────────────────────────────────────────────────

def _ensure_metadata_row(conn: sqlite3.Connection, cid: str) -> None:
    """Biztosítja, hogy létezik metaadat sor a chat-hez."""
    now = format_timestamp()
    conn.execute(
        "INSERT OR IGNORE INTO chat_metadata (chat_id, created_at, updated_at) VALUES (?, ?, ?)",
        (cid, now, now),
    )
    conn.commit()


def _add_tags(conn: sqlite3.Connection, cid: str, tags: list[str]) -> None:
    """Címkéket ad egy chat-hez (megőrzi a meglévőket)."""
    _ensure_metadata_row(conn, cid)
    row = conn.execute("SELECT tags FROM chat_metadata WHERE chat_id = ?", (cid,)).fetchone()
    existing = []
    if row and row[0]:
        try:
            existing = json.loads(row[0])
        except json.JSONDecodeError:
            existing = []
    merged = list(set(existing + [t.strip().lower() for t in tags if t.strip()]))
    conn.execute(
        "UPDATE chat_metadata SET tags = ?, updated_at = ? WHERE chat_id = ?",
        (json.dumps(merged), format_timestamp(), cid),
    )
    conn.commit()
    logger.info("Cimkek: %s", ", ".join(merged))


def _set_project(conn: sqlite3.Connection, cid: str, project: str) -> None:
    """Projektet rendel egy chat-hez."""
    _ensure_metadata_row(conn, cid)
    conn.execute(
        "UPDATE chat_metadata SET project = ?, updated_at = ? WHERE chat_id = ?",
        (project, format_timestamp(), cid),
    )
    conn.commit()
    logger.info("Projekt: %s", project)


def _toggle_favorite(conn: sqlite3.Connection, cid: str) -> bool:
    """Kedvenc jelölés ki/be."""
    _ensure_metadata_row(conn, cid)
    row = conn.execute("SELECT is_favorite FROM chat_metadata WHERE chat_id = ?", (cid,)).fetchone()
    current = bool(row[0]) if row else False
    new_val = 0 if current else 1
    conn.execute(
        "UPDATE chat_metadata SET is_favorite = ?, updated_at = ? WHERE chat_id = ?",
        (new_val, format_timestamp(), cid),
    )
    conn.commit()
    return bool(new_val)


def _list_tags(conn: sqlite3.Connection) -> list[str]:
    """Visszaadja az összes egyedi címkét."""
    rows = conn.execute("SELECT tags FROM chat_metadata WHERE tags IS NOT NULL AND tags != '[]'").fetchall()
    all_tags = set()
    for (tags_json,) in rows:
        try:
            for t in json.loads(tags_json):
                all_tags.add(t)
        except json.JSONDecodeError:
            pass
    return sorted(all_tags)


# ─── Interaktív chat böngésző ────────────────────────────────────────────

def _browse_chats(conn: sqlite3.Connection) -> None:
    """Interaktív chat böngésző a manifest adatbázis alapján."""
    while True:
        print("\n" + "=" * 60)
        print("  Chat Böngésző")
        print("=" * 60)
        print("  [l] Listázás (összes)")
        print("  [k] Keresés (FTS5)")
        print("  [c] Címkék listázása")
        print("  [t] Címkézés chat_id alapján")
        print("  [p] Projekt beállítása")
        print("  [f] Kedvenc jelölés")
        print("  [q] Kilépés")
        print("=" * 60)

        try:
            choice = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if choice == "q":
            break
        elif choice == "l":
            rows = conn.execute(
                """SELECT e.chat_id, e.title, e.last_exported_at, e.message_count, e.status,
                          m.is_favorite, m.tags, m.project
                   FROM exports e
                   LEFT JOIN chat_metadata m ON e.chat_id = m.chat_id
                   ORDER BY e.last_exported_at DESC
                   LIMIT 30"""
            ).fetchall()
            print(f"\n  {'Cím':<60} {'Üzenet':<8} {'Státusz':<10} {'Címkék'}")
            print(f"  {'-'*60} {'-'*8} {'-'*10} {'-'*20}")
            for row in rows:
                cid, title, _, msg_count, status, fav, tags_json, project = row
                fav_mark = "⭐ " if fav else "  "
                display_title = f"{fav_mark}{(title or 'Untitled')[:56]}"
                try:
                    tags = ", ".join(json.loads(tags_json)) if tags_json else ""
                except json.JSONDecodeError:
                    tags = ""
                print(f"  {display_title:<60} {msg_count or 0:<8} {status:<10} {tags[:20]}")
        elif choice == "k":
            query = input("  Keresés: ").strip()
            if query:
                results = _search_chats(conn, query)
                print(f"\n  Találatok: {len(results)}")
                for r in results[:20]:
                    print(f"  [{r['cid'][:8]}] {r['title'][:70]} ({r['message_count']} üzenet)")
        elif choice == "c":
            tags = _list_tags(conn)
            print(f"\n  Címkék ({len(tags)}): {', '.join(tags)}")
        elif choice == "t":
            cid_input = input("  Chat ID (vagy első 8 karakter): ").strip()
            cid = _resolve_chat_id(conn, cid_input) if cid_input else None
            if cid:
                tag_str = input("  Címkék (vesszővel elválasztva): ").strip()
                if tag_str:
                    tags = [t.strip() for t in tag_str.split(",")]
                    _add_tags(conn, cid, tags)
        elif choice == "p":
            cid_input = input("  Chat ID (vagy első 8 karakter): ").strip()
            cid = _resolve_chat_id(conn, cid_input) if cid_input else None
            if cid:
                project = input("  Projekt neve: ").strip()
                if project:
                    _set_project(conn, cid, project)
        elif choice == "f":
            cid_input = input("  Chat ID (vagy első 8 karakter): ").strip()
            cid = _resolve_chat_id(conn, cid_input) if cid_input else None
            if cid:
                is_fav = _toggle_favorite(conn, cid)
                print(f"  Kedvenc: {'⭐ be' if is_fav else 'ki'}")
