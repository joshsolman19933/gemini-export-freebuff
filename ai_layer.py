#!/usr/bin/env python3
"""
AI Layer — OpenAI-kompatibilis API integráció
==============================================
Automatikus összefoglalók, teendők/projektötletek kinyerése,
és címkejavaslatok a Gemini beszélgetésekhez.

Bármilyen OpenAI-kompatibilis API-val működik:
- OpenAI, Groq, Ollama, LM Studio, stb.

Környezeti változók (.env):
    OPENAI_API_KEY=sk-...          # API kulcs (kötelező)
    OPENAI_BASE_URL=https://...    # API base URL (alap: https://api.openai.com/v1)
    OPENAI_MODEL=gpt-4o-mini       # Modell név (alap: gpt-4o-mini)
    OPENAI_MAX_TOKENS=500          # Max válasz tokenek (alap: 500)
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI


# ─── Konfiguráció ────────────────────────────────────────────────────────────

def _get_openai_client() -> OpenAI:
    """Létrehoz egy OpenAI (vagy kompatibilis) klienst a környezeti változókból."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY környezeti változó nincs beállítva. "
            "Állítsd be a .env fájlban: OPENAI_API_KEY=sk-..."
        )

    return OpenAI(api_key=api_key, base_url=base_url)


def _get_model() -> str:
    """Visszaadja a konfigurált modell nevet."""
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _get_max_tokens() -> int:
    """Visszaadja a maximális válasz tokenek számát."""
    return int(os.getenv("OPENAI_MAX_TOKENS", "500"))


# ─── Chat tartalom formázása ────────────────────────────────────────────────

def _format_chat_for_ai(turns: list[dict], title: str, max_chars: int = 12000) -> str:
    """A chat üzeneteket formázza az AI számára olvasható szöveggé.

    A régebbi üzeneteket levágja, ha túl hosszú lenne.
    Az üzenetek fordított sorrendben vannak (újtól régi felé, ahogy a gemini_webapi adja),
    ezért a feldolgozáshoz megfordítjuk őket.
    """
    parts = [f"Title: {title}\n"]

    # Megfordítjuk, hogy időrendben legyenek (legrégebbitől a legújabbig)
    chronological = list(reversed(turns))

    total_chars = len(parts[0])
    processed = 0
    for turn in chronological:
        role = turn.get("role", "unknown").upper()
        text = turn.get("text", "").strip()
        if not text:
            continue

        role_label = "User" if role == "USER" else "Gemini"
        line = f"\n### {role_label}:\n{text}\n"

        if total_chars + len(line) > max_chars:
            break  # Elértük a limitet, a régebbi üzeneteket kihagyjuk

        parts.append(line)
        total_chars += len(line)
        processed += 1

    # Ha levágtunk üzeneteket, jelezzük
    total = len(chronological)
    if processed < total:
        parts.insert(1, f"\n[{total - shown} korábbi üzenet kihagyva a hossz miatt]\n")

    return "".join(parts)


def _parse_json_response(content: str) -> list | dict | None:
    """Megpróbál JSON-t kinyerni az AI válaszból."""
    content = content.strip()

    # Próbáljuk meg közvetlenül JSON-ként értelmezni
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Próbáljuk meg kinyerni a JSON-t markdown kódblokkból
    import re
    match = re.search(r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Próbáljunk meg bármilyen JSON tömböt vagy objektumot találni
    for pattern in [r"\[.*?\]", r"\{.*?\}"]:
        match = re.search(pattern, content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                continue

    return None


# ─── AI funkciók ─────────────────────────────────────────────────────────────

def generate_summary(turns: list[dict], title: str) -> str | None:
    """Létrehoz egy rövid (2-3 mondatos) összefoglalót a beszélgetésről.

    Args:
        turns: A chat üzenetek listája (fordított sorrendben).
        title: A chat címe.

    Returns:
        Az összefoglaló szöveg, vagy None hiba esetén.
    """
    try:
        client = _get_openai_client()
        chat_text = _format_chat_for_ai(turns, title)

        # Nyelv felismerése: ha a cím magyar ékezetes karaktereket tartalmaz, magyarul kérjük
        has_hungarian = any(c in title for c in "áéíóöőúüűÁÉÍÓÖŐÚÜŰ")
        lang_instruction = "magyarul" if has_hungarian else "in the same language as the conversation"

        response = client.chat.completions.create(
            model=_get_model(),
            messages=[{
                "role": "system",
                "content": (
                    f"You are a helpful assistant that summarizes conversations. "
                    f"Write a concise 2-3 sentence summary {lang_instruction}. "
                    f"Focus on the main topic, key decisions, and outcomes. "
                    f"Reply ONLY with the summary text, no prefixes or labels."
                ),
            }, {
                "role": "user",
                "content": f"Summarize this conversation:\n\n{chat_text}",
            }],
            max_tokens=_get_max_tokens(),
            temperature=0.3,
        )

        summary = response.choices[0].message.content
        return summary.strip() if summary else None
    except Exception as e:
        print(f"  [AI] Összefoglaló hiba: {e}")
        return None


def extract_todos(turns: list[dict], title: str) -> list[dict] | None:
    """Kinyeri a teendőket, projektötleteket, döntéseket a beszélgetésből.

    Args:
        turns: A chat üzenetek listája (fordított sorrendben).
        title: A chat címe.

    Returns:
        Lista: [{"text": "...", "category": "todo|project_idea|decision|learning"}, ...]
        vagy None hiba esetén.
    """
    try:
        client = _get_openai_client()
        chat_text = _format_chat_for_ai(turns, title)

        has_hungarian = any(c in title for c in "áéíóöőúüűÁÉÍÓÖŐÚÜŰ")
        lang_instruction = "in Hungarian" if has_hungarian else "in the same language as the conversation"

        response = client.chat.completions.create(
            model=_get_model(),
            messages=[{
                "role": "system",
                "content": (
                    f"You extract action items, todos, project ideas, decisions, and learnings "
                    f"from conversations. Reply ONLY with a JSON array of objects. "
                    f"Each object has: \"text\" (the item description), "
                    f"\"category\" (one of: \"todo\", \"project_idea\", \"decision\", \"learning\"). "
                    f"Write the items {lang_instruction}. "
                    f"If there are no items, reply with an empty array: []"
                ),
            }, {
                "role": "user",
                "content": f"Extract todos, project ideas, decisions, and learnings:\n\n{chat_text}",
            }],
            max_tokens=_get_max_tokens(),
            temperature=0.3,
        )

        content = response.choices[0].message.content
        if not content:
            return None

        result = _parse_json_response(content)
        if isinstance(result, list):
            # Validáljuk az elemeket
            valid_categories = {"todo", "project_idea", "decision", "learning"}
            validated = []
            for item in result:
                if isinstance(item, dict) and "text" in item:
                    validated.append({
                        "text": str(item["text"]),
                        "category": item.get("category", "todo")
                        if item.get("category") in valid_categories else "todo",
                        "done": False,
                    })
            return validated if validated else []

        return []
    except Exception as e:
        print(f"  [AI] Teendő kinyerés hiba: {e}")
        return None


def suggest_tags(turns: list[dict], title: str) -> list[str] | None:
    """Javasol címkéket a beszélgetés tartalma alapján.

    Args:
        turns: A chat üzenetek listája (fordított sorrendben).
        title: A chat címe.

    Returns:
        Lista címke stringekkel, vagy None hiba esetén.
    """
    try:
        client = _get_openai_client()
        chat_text = _format_chat_for_ai(turns, title)

        response = client.chat.completions.create(
            model=_get_model(),
            messages=[{
                "role": "system",
                "content": (
                    "You suggest relevant tags for conversations. "
                    "Reply ONLY with a JSON array of 3-5 lowercase tags (strings). "
                    "Tags should be short (1-3 words), descriptive, and useful for categorizing. "
                    "Use lowercase English for tag names. "
                    "Example: [\"python\", \"machine learning\", \"api design\"]"
                ),
            }, {
                "role": "user",
                "content": f"Suggest tags for this conversation:\n\n{chat_text}",
            }],
            max_tokens=200,
            temperature=0.4,
        )

        content = response.choices[0].message.content
        if not content:
            return None

        result = _parse_json_response(content)
        if isinstance(result, list):
            # Tisztítás: csak stringek, kisbetűsítés, whitespace trim
            return [str(t).strip().lower() for t in result if t and str(t).strip()]
        return []
    except Exception as e:
        print(f"  [AI] Címke javaslat hiba: {e}")
        return None


# ─── Manifest adatbázis bővítés ──────────────────────────────────────────────

def _ensure_ai_schema(conn: sqlite3.Connection) -> None:
    """Biztosítja, hogy az AI-hoz szükséges táblák és oszlopok léteznek."""
    # chat_metadata bővítése (ha már létezik)
    for col, col_type in [
        ("summary", "TEXT"),
        ("auto_tags", "TEXT"),
        ("analyzed_at", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE chat_metadata ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass  # Az oszlop már létezik

    # chat_todos tábla
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
    conn.commit()


def _store_ai_results(
    conn: sqlite3.Connection,
    cid: str,
    summary: str | None,
    tags: list[str] | None,
    todos: list[dict] | None,
) -> None:
    """Eltárolja az AI elemzés eredményeit a manifestben."""
    from export import _ensure_metadata_row, format_timestamp

    _ensure_metadata_row(conn, cid)
    now = format_timestamp()

    updates = []
    values = []

    if summary:
        updates.append("summary = ?")
        values.append(summary)

    if tags:
        updates.append("auto_tags = ?")
        values.append(json.dumps(tags))

    if summary or tags:
        updates.append("analyzed_at = ?")
        values.append(now)

    if updates:
        values.append(cid)
        conn.execute(
            f"UPDATE chat_metadata SET {', '.join(updates)} WHERE chat_id = ?",
            values,
        )

    # Teendők törlése és újra beszúrása
    if todos is not None:
        conn.execute("DELETE FROM chat_todos WHERE chat_id = ?", (cid,))
        for todo in todos:
            conn.execute(
                "INSERT INTO chat_todos (chat_id, todo_text, category, done, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (cid, todo["text"], todo.get("category", "todo"), 0, now),
            )

    conn.commit()


def _get_ai_results(conn: sqlite3.Connection, cid: str) -> dict:
    """Lekéri egy chat AI elemzési eredményeit."""
    row = conn.execute(
        "SELECT summary, auto_tags, analyzed_at FROM chat_metadata WHERE chat_id = ?",
        (cid,),
    ).fetchone()

    result = {"summary": None, "auto_tags": [], "analyzed_at": None, "todos": []}

    if row and row[0] is not None:
        result["summary"] = row[0]
        try:
            result["auto_tags"] = json.loads(row[1]) if row[1] else []
        except json.JSONDecodeError:
            result["auto_tags"] = []
        result["analyzed_at"] = row[2]

    # Teendők lekérése
    todo_rows = conn.execute(
        "SELECT id, todo_text, category, done FROM chat_todos WHERE chat_id = ? ORDER BY id",
        (cid,),
    ).fetchall()
    result["todos"] = [
        {"id": r[0], "text": r[1], "category": r[2], "done": bool(r[3])}
        for r in todo_rows
    ]

    return result


# ─── Batch elemzés ───────────────────────────────────────────────────────────

def analyze_chat(
    conn: sqlite3.Connection,
    cid: str,
    turns: list[dict],
    title: str,
    options: dict | None = None,
) -> dict:
    """Teljes AI elemzés egy chat-en: összefoglaló, teendők, címkék.

    Args:
        conn: Manifest kapcsolat.
        cid: Chat ID.
        turns: A chat üzenetei (fordított sorrendben).
        title: A chat címe.
        options: Dict a funkciók engedélyezéséhez:
            {"summarize": True, "todos": True, "tags": True}

    Returns:
        Eredmény dict: {"summary": ..., "todos": [...], "tags": [...], "error": ...}
    """
    if options is None:
        options = {"summarize": True, "todos": True, "tags": True}

    result = {"summary": None, "todos": None, "tags": None, "error": None}

    try:
        _ensure_ai_schema(conn)

        if options.get("summarize"):
            result["summary"] = generate_summary(turns, title)

        if options.get("todos"):
            result["todos"] = extract_todos(turns, title)

        if options.get("tags"):
            result["tags"] = suggest_tags(turns, title)

        # Eltárolás
        _store_ai_results(conn, cid, result["summary"], result["tags"], result["todos"])

    except Exception as e:
        result["error"] = str(e)

    return result


def analyze_chat_from_json(
    conn: sqlite3.Connection,
    output_dir: Path,
    cid: str,
    options: dict | None = None,
) -> dict:
    """AI elemzés egy korábban exportált chat-en (JSON fájlból olvasva).

    Args:
        conn: Manifest kapcsolat.
        output_dir: Az exports könyvtár.
        cid: Chat ID (vagy első 8 karakter).
        options: Dict a funkciókhoz.

    Returns:
        Eredmény dict.
    """
    # JSON fájl keresése
    pattern = f"*_{cid[:8]}.json"
    json_files = list(output_dir.glob(f"json/{pattern}"))
    if not json_files:
        return {"error": f"Chat not found: {cid}"}

    try:
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        title = data.get("title", "Untitled")
        turns = data.get("turns", [])
        return analyze_chat(conn, data.get("cid", cid), turns, title, options)
    except Exception as e:
        return {"error": str(e)}
