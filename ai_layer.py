#!/usr/bin/env python3
"""
AI Layer — Multi-provider AI integráció
=========================================
Automatikus összefoglalók, teendők/projektötletek kinyerése,
és címkejavaslatok a Gemini beszélgetésekhez.

Támogatott API-k (factory pattern az ai_providers modulon keresztül):
- OpenAI, Anthropic Claude, Google Gemini
- Groq, Together.ai, DeepSeek
- Ollama, LM Studio (lokális)

Környezeti változók (.env):
    AI_PROVIDER=openai|anthropic|gemini|groq|together|deepseek|ollama
    OPENAI_API_KEY=sk-...          # API kulcs (OpenAI-kompatibilisekhez)
    OPENAI_BASE_URL=https://...    # API base URL (alap: https://api.openai.com/v1)
    OPENAI_MODEL=gpt-4o-mini       # Modell név (alap: provider default)
    OPENAI_MAX_TOKENS=500          # Max válasz tokenek (alap: 500)
    OPENAI_EMBEDDING_MODEL=...     # Embedding modell (alap: provider default)
    ANTHROPIC_API_KEY=sk-ant-...   # Anthropic API kulcs
    GEMINI_API_KEY=...             # Google Gemini API kulcs
"""

import json
import math
import os
import re
import sqlite3
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

from gemini_export.ai_providers import (
    AIProvider,
    detect_provider_info,
    get_provider,
)
from gemini_export.logging_config import get_logger

logger = get_logger(__name__)


def _get_provider() -> AIProvider:
    """Létrehoz vagy visszaad egy AI provider példányt a környezeti változók alapján."""
    return get_provider()


def _get_model() -> str:
    """Visszaadja a konfigurált modell nevet."""
    try:
        return _get_provider().get_model()
    except Exception as e:
        logger.debug("Provider not available for model lookup, using env fallback: %s", e)
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
        parts.insert(1, f"\n[{total - processed} korábbi üzenet kihagyva a hossz miatt]\n")

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
        provider = _get_provider()
        chat_text = _format_chat_for_ai(turns, title)

        # Nyelv felismerése: ha a cím magyar ékezetes karaktereket tartalmaz, magyarul kérjük
        has_hungarian = any(c in title for c in "áéíóöőúüűÁÉÍÓÖŐÚÜŰ")
        lang_instruction = "magyarul" if has_hungarian else "in the same language as the conversation"

        # Egyedi vagy default prompt betöltése (import hiba esetén fallback)
        try:
            from gemini_export.prompt_templates import render_prompt as _render  # noqa: F811
        except ImportError:
            _render = lambda *a, **kw: None  # noqa: E731
        system_prompt = _render("summarize", {"lang": lang_instruction})

        response = provider.chat_completion(
            model=_get_model(),
            messages=[{
                "role": "system",
                "content": system_prompt or (
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
        logger.warning(f"Összefoglaló hiba: {e}")
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
        provider = _get_provider()
        chat_text = _format_chat_for_ai(turns, title)

        has_hungarian = any(c in title for c in "áéíóöőúüűÁÉÍÓÖŐÚÜŰ")
        lang_instruction = "in Hungarian" if has_hungarian else "in the same language as the conversation"

        # Egyedi vagy default prompt betöltése (import hiba esetén fallback)
        try:
            from gemini_export.prompt_templates import render_prompt as _render  # noqa: F811
        except ImportError:
            _render = lambda *a, **kw: None  # noqa: E731
        system_prompt = _render("todos", {"lang": lang_instruction})

        response = provider.chat_completion(
            model=_get_model(),
            messages=[{
                "role": "system",
                "content": system_prompt or (
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
        logger.warning(f"Teendő kinyerés hiba: {e}")
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
        provider = _get_provider()
        chat_text = _format_chat_for_ai(turns, title)

        # Egyedi vagy default prompt betöltése (import hiba esetén fallback)
        try:
            from gemini_export.prompt_templates import render_prompt as _render  # noqa: F811
        except ImportError:
            _render = lambda *a, **kw: None  # noqa: E731
        system_prompt = _render("tags", {})

        response = provider.chat_completion(
            model=_get_model(),
            messages=[{
                "role": "system",
                "content": system_prompt or (
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
        logger.warning(f"Címke javaslat hiba: {e}")
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
    from gemini_export.search import _ensure_metadata_row
    from gemini_export.utils import format_timestamp

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


# ─── RAG (Retrieval-Augmented Generation) ──────────────────────────────────


def _ensure_rag_schema(conn: sqlite3.Connection) -> None:
    """Biztosítja, hogy a RAG-hoz szükséges embedding tábla létezik."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_embeddings (
            chat_id TEXT PRIMARY KEY,
            embedding TEXT NOT NULL,
            created_at TEXT,
            FOREIGN KEY (chat_id) REFERENCES exports(chat_id)
        )
    """)
    conn.commit()


def _get_embedding_model() -> str:
    """Visszaadja a konfigurált embedding modell nevet."""
    try:
        emb = _get_provider().get_embedding_model()
        return emb or os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    except Exception as e:
        logger.debug("Provider not available for embedding model lookup: %s", e)
        return os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")


def generate_embedding(text: str, model: str | None = None) -> list[float] | None:
    """Embedding generálása egy szöveghez (multi-provider + lokális).

    Az EMBEDDING_PROVIDER env var szabályozza a forrást:
        - "local": mindig lokális sentence-transformers modellt használ
        - "openai" / "ollama" / "gemini" stb.: mindig API-alapú embedding
        - "auto" (alap): lokálisat próbál először, API-ra fallback

    Args:
        text: A bemeneti szöveg.
        model: Embedding modell név (override, provider-specifikus).

    Returns:
        Float lista, vagy None hiba esetén.
    """
    if not text or not text.strip():
        return None

    embedding_provider = os.getenv("EMBEDDING_PROVIDER", "auto").lower().strip()

    # Route 1: Force local embeddings
    if embedding_provider == "local":
        try:
            from gemini_export.local_embedding import local_embed

            return local_embed(text, model)
        except Exception as e:
            logger.warning(f"Local embedding hiba: {e}")
            return None

    # Route 2: Force API-based embeddings
    if embedding_provider not in ("auto", ""):
        try:
            provider = _get_provider()
            emb_model = model or _get_embedding_model()
            if not emb_model:
                logger.warning(f"A(z) {embedding_provider} provider nem támogat embeddinget.")
                return None
            return provider.embedding(model=emb_model, text=text.strip()[:8000])
        except Exception as e:
            logger.warning(f"API embedding hiba ({embedding_provider}): {e}")
            return None

    # Route 3: Auto — try local first, fall back to API
    try:
        from gemini_export.local_embedding import is_local_embedding_available, local_embed

        if is_local_embedding_available():
            result = local_embed(text, model)
            if result is not None:
                return result
            logger.debug("Local embedding failed, falling back to API")
    except Exception:
        pass  # sentence-transformers not installed, fall back to API

    # Fall back to API provider
    try:
        provider = _get_provider()
        emb_model = model or _get_embedding_model()
        if not emb_model:
            logger.warning("Az API provider nem támogat embeddinget, lokális embedding sem elérhető.")
            return None
        return provider.embedding(model=emb_model, text=text.strip()[:8000])
    except Exception as e:
        logger.warning(f"Embedding hiba (API fallback): {e}")
        return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Két vektor koszinusz hasonlósága."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _format_chat_for_embedding(turns: list[dict], title: str) -> str:
    """Chat tartalom formázása embedding generáláshoz (cím + első üzenetek)."""
    parts = [title]
    chronological = list(reversed(turns))
    total = 0
    for turn in chronological:
        text = turn.get("text", "").strip()
        if not text:
            continue
        parts.append(text[:300])
        total += len(text)
        if total > 2500:
            break
    return "\n".join(parts)[:8000]


def index_chat_embedding(conn: sqlite3.Connection, cid: str, turns: list[dict], title: str) -> bool:
    """Embedding generálása és tárolása egy chat-hez.

    Returns:
        True ha sikeres, False ha hiba történt.
    """
    _ensure_rag_schema(conn)
    text = _format_chat_for_embedding(turns, title)
    embedding = generate_embedding(text)
    if embedding is None:
        return False
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO chat_embeddings (chat_id, embedding, created_at) VALUES (?, ?, ?)",
        (cid, json.dumps(embedding), now),
    )
    conn.commit()
    return True


def index_all_embeddings(conn: sqlite3.Connection, output_dir: Path, progress_callback=None) -> dict:
    """Embedding generálása az összes exportált chat-hez.

    Args:
        conn: Manifest kapcsolat.
        output_dir: Az exports könyvtár.
        progress_callback: Opcionális callback(processed, total) a haladás jelzéséhez.

    Returns:
        {"total": int, "indexed": int, "skipped": int, "failed": int}
    """
    _ensure_rag_schema(conn)
    rows = conn.execute(
        "SELECT chat_id, title FROM exports WHERE status = 'ok'"
    ).fetchall()

    total = len(rows)
    indexed = 0
    skipped = 0
    failed = 0

    for i, (cid, title) in enumerate(rows):
        # Ellenőrizzük, hogy már indexelve van-e
        existing = conn.execute(
            "SELECT 1 FROM chat_embeddings WHERE chat_id = ?", (cid,)
        ).fetchone()
        if existing:
            skipped += 1
            if progress_callback:
                progress_callback(i + 1, total)
            continue

        # JSON fájl keresése
        pattern = f"*_{cid[:8]}.json"
        json_files = list(output_dir.glob(f"json/{pattern}"))
        if not json_files:
            failed += 1
            if progress_callback:
                progress_callback(i + 1, total)
            continue

        try:
            data = json.loads(json_files[0].read_text(encoding="utf-8"))
            turns = data.get("turns", [])
            if index_chat_embedding(conn, cid, turns, title or data.get("title", "")):
                indexed += 1
            else:
                failed += 1
        except Exception:
            failed += 1

        if progress_callback:
            progress_callback(i + 1, total)

    return {"total": total, "indexed": indexed, "skipped": skipped, "failed": failed}


def find_related_chats(
    conn: sqlite3.Connection,
    target_cid: str,
    top_k: int = 8,
    min_similarity: float = 0.35,
) -> list[dict]:
    """Kapcsolódó chat-ek keresése embedding hasonlóság alapján.

    A cél chat embeddingjét használja kiindulópontként és koszinusz
    hasonlóság alapján rangsorolja a többi indexelt chat-et.

    Args:
        conn: Manifest kapcsolat.
        target_cid: A kiinduló chat ID-je.
        top_k: Visszaadott kapcsolódó chat-ek száma.
        min_similarity: Minimális hasonlósági küszöb (0-1).

    Returns:
        Lista: [{"cid": ..., "title": ..., "similarity": ..., "message_count": ...}, ...]
    """
    _ensure_rag_schema(conn)

    # Cél chat embeddingjének lekérése
    target_row = conn.execute(
        "SELECT embedding FROM chat_embeddings WHERE chat_id = ?", (target_cid,)
    ).fetchone()
    if not target_row:
        return []

    try:
        target_emb = json.loads(target_row[0])
    except (json.JSONDecodeError, TypeError):
        return []

    # Összes többi chat embedding + metaadat lekérése
    rows = conn.execute(
        """SELECT ce.chat_id, ce.embedding, e.title, e.message_count,
                  e.image_count, e.last_exported_at,
                  m.tags, m.project, m.is_favorite
           FROM chat_embeddings ce
           JOIN exports e ON ce.chat_id = e.chat_id
           LEFT JOIN chat_metadata m ON ce.chat_id = m.chat_id
           WHERE ce.chat_id != ?""",
        (target_cid,),
    ).fetchall()

    scored = []
    for cid, emb_json, title, msg_count, img_count, exported_at, tags_json, project, fav in rows:
        try:
            emb = json.loads(emb_json)
            sim = _cosine_similarity(target_emb, emb)
            if sim >= min_similarity:
                tags = []
                if tags_json:
                    try:
                        tags = json.loads(tags_json)
                    except json.JSONDecodeError:
                        pass
                scored.append({
                    "cid": cid,
                    "title": title or "Untitled",
                    "similarity": round(sim, 4),
                    "message_count": msg_count or 0,
                    "image_count": img_count or 0,
                    "exported_at": exported_at,
                    "tags": tags,
                    "project": project,
                    "is_favorite": bool(fav),
                })
        except (json.JSONDecodeError, TypeError):
            continue

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:top_k]


def build_knowledge_graph(
    conn: sqlite3.Connection,
    target_cid: str,
    max_nodes: int = 15,
    min_similarity: float = 0.3,
) -> dict:
    """Tudásgráf adatok építése D3.js force-directed graph-hoz.

    A gráf tartalmazza:
    - A cél chat-et (külön jelöléssel)
    - A kapcsolódó chat-eket (elsődleges kapcsolatok)
    - Másodlagos kapcsolatokat (related-ek egymás közti hasonlósága, ha elég nagy)

    Args:
        conn: Manifest kapcsolat.
        target_cid: A központi chat ID-je.
        max_nodes: Maximális node-ok száma.
        min_similarity: Minimális hasonlóság az élekhez.

    Returns:
        {"nodes": [...], "links": [...]} D3.js-kompatibilis formátumban.
    """
    # Elsődleges kapcsolatok
    related = find_related_chats(conn, target_cid, top_k=max_nodes - 1, min_similarity=min_similarity)
    if not related:
        return {"nodes": [], "links": [], "error": "Nincsenek indexelt kapcsolatok. Először indexeld az archívumot."}

    # Cél chat metaadatainak lekérése
    target_row = conn.execute(
        "SELECT title, message_count, image_count FROM exports WHERE chat_id = ?",
        (target_cid,),
    ).fetchone()
    target_title = target_row[0] if target_row else "Untitled"

    # Node-ok építése (cél + kapcsolódók)
    node_ids = {target_cid}
    nodes = [{
        "id": target_cid,
        "title": target_title,
        "group": "target",
        "message_count": target_row[1] if target_row else 0,
        "radius": 8,
    }]

    for r in related:
        node_ids.add(r["cid"])
        nodes.append({
            "id": r["cid"],
            "title": r["title"],
            "group": "related",
            "message_count": r["message_count"],
            "similarity": r["similarity"],
            "radius": 4 + r["similarity"] * 5,
        })

    # Élek: cél → kapcsolódók
    links = []
    for r in related:
        links.append({
            "source": target_cid,
            "target": r["cid"],
            "value": r["similarity"],
            "type": "primary",
        })

    # Másodlagos kapcsolatok: related-ek egymás közt (ha van elég node)
    if len(related) >= 3:
        _ensure_rag_schema(conn)
        related_cids = [r["cid"] for r in related]
        placeholders = ",".join("?" for _ in related_cids)

        emb_rows = conn.execute(
            f"SELECT chat_id, embedding FROM chat_embeddings WHERE chat_id IN ({placeholders})",
            related_cids,
        ).fetchall()

        emb_map: dict[str, list[float]] = {}
        for cid, emb_json in emb_rows:
            try:
                emb_map[cid] = json.loads(emb_json)
            except (json.JSONDecodeError, TypeError):
                pass

        secondary_threshold = 0.45  # Magasabb küszöb a másodlagos élekhez
        for i in range(len(related_cids)):
            for j in range(i + 1, len(related_cids)):
                a_cid, b_cid = related_cids[i], related_cids[j]
                if a_cid in emb_map and b_cid in emb_map:
                    sim = _cosine_similarity(emb_map[a_cid], emb_map[b_cid])
                    if sim >= secondary_threshold:
                        links.append({
                            "source": a_cid,
                            "target": b_cid,
                            "value": round(sim, 4),
                            "type": "secondary",
                        })

    return {"nodes": nodes, "links": links}


def search_similar_chats(conn: sqlite3.Connection, question: str, top_k: int = 5) -> list[dict]:
    """Szemantikus keresés: embedding alapú hasonló chat-ek keresése.

    Args:
        conn: Manifest kapcsolat.
        question: A felhasználó kérdése.
        top_k: Visszaadott találatok száma.

    Returns:
        Lista: [{"cid": ..., "title": ..., "similarity": ...}, ...]
    """
    question_embedding = generate_embedding(question)
    if question_embedding is None:
        return []

    rows = conn.execute(
        """SELECT ce.chat_id, ce.embedding, e.title
           FROM chat_embeddings ce
           JOIN exports e ON ce.chat_id = e.chat_id"""
    ).fetchall()

    if not rows:
        return []

    scored = []
    for cid, emb_json, title in rows:
        try:
            emb = json.loads(emb_json)
            sim = _cosine_similarity(question_embedding, emb)
            scored.append((sim, cid, title or "Untitled"))
        except (json.JSONDecodeError, TypeError):
            continue

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    return [{"cid": cid, "title": title, "similarity": round(sim, 4)} for sim, cid, title in top]


def rag_query_stream(
    conn: sqlite3.Connection,
    output_dir: Path,
    question: str,
    top_k: int = 5,
) -> Generator[str, None, None]:
    """RAG query streaming válasszal (SSE formátum).

    A folyamat:
    1. Embedding generálás a kérdéshez
    2. Hasonló chat-ek keresése
    3. Chat tartalom betöltése JSON-ból
    4. Kontextus összeállítása
    5. LLM válasz streamelése SSE event-ekként

    Yields:
        SSE event string-ek: "event: ...\\ndata: ...\\n\\n"
    """
    # 1. Embedding keresés
    similar = search_similar_chats(conn, question, top_k)

    if not similar:
        yield (
            "event: error\ndata: Nincsenek indexelt beszélgetések. "
            "Először indexeld az archívumot a 🧠 Index gombbal.\n\n"
        )
        return

    # 2. Források küldése
    sources_json = json.dumps(similar, ensure_ascii=False)
    yield f"event: sources\ndata: {sources_json}\n\n"

    # 3. Chat tartalom betöltése
    context_parts = []
    for chat in similar:
        cid = chat["cid"]
        pattern = f"*_{cid[:8]}.json"
        json_files = list(output_dir.glob(f"json/{pattern}"))
        if not json_files:
            continue
        try:
            data = json.loads(json_files[0].read_text(encoding="utf-8"))
            chat_text = _format_chat_for_ai(data.get("turns", []), data.get("title", ""), max_chars=4000)
            context_parts.append(f"--- Chat: {chat['title']} ---\n{chat_text}")
        except Exception:
            continue

    if not context_parts:
        yield "event: error\ndata: A hasonló beszélgetések nem tölthetők be.\n\n"
        return

    context = "\n\n".join(context_parts)[:12000]

    # 4. Nyelv felismerése + egyedi prompt betöltése
    has_hungarian = any(c in question for c in "áéíóöőúüűÁÉÍÓÖŐÚÜŰ")
    lang_instruction = "magyarul" if has_hungarian else "in the same language as the question"

    try:
        from gemini_export.prompt_templates import render_prompt  # noqa: F811
    except ImportError:
        render_prompt = lambda *a, **kw: None  # noqa: E731
    system_prompt = render_prompt("rag_query", {"lang": lang_instruction, "context": context})

    if not system_prompt:
        system_prompt = (
            f"You are a helpful assistant answering questions based on the user's Gemini chat archives. "
            f"Answer the question using ONLY the provided conversation excerpts as context. "
            f"If the context doesn't contain enough information, say so honestly. "
            f"Always cite which chat(s) your answer comes from by mentioning the chat title. "
            f"Answer {lang_instruction}. Be concise and helpful.\n\n"
            f"## Context (from user's Gemini chat archives):\n{context}"
        )

    try:
        provider = _get_provider()
        for token in provider.chat_completion_stream(
            model=_get_model(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            max_tokens=_get_max_tokens(),
            temperature=0.5,
        ):
            yield f"data: {token}\n\n"

        yield "event: done\ndata: {}\n\n"

    except Exception as e:
        yield f"event: error\ndata: Hiba a válasz generálásakor: {str(e)}\n\n"


def detect_ai_provider() -> dict:
    """Detektálja az AI szolgáltatót és visszaadja az elérhető funkciókat.

    Returns:
        {"provider": "openai"|"anthropic"|"gemini"|"groq"|"together"|"deepseek"|"ollama"|"unknown",
         "base_url": ...,
         "model": ...,
         "embedding_model": ...,
         "healthy": bool,
         "error": ...}
    """
    try:
        provider = _get_provider()
        return provider.to_dict()
    except Exception as e:
        info = detect_provider_info()
        return {
            "provider": info.get("provider", "unknown"),
            "base_url": info.get("base_url", ""),
            "model": info.get("model", ""),
            "embedding_model": info.get("embedding_model"),
            "healthy": False,
            "error": str(e),
        }


def get_rag_index_status(conn: sqlite3.Connection) -> dict:
    """Visszaadja a RAG index állapotát."""
    _ensure_rag_schema(conn)
    total = conn.execute("SELECT COUNT(*) FROM exports WHERE status = 'ok'").fetchone()[0]
    indexed = conn.execute("SELECT COUNT(*) FROM chat_embeddings").fetchone()[0]
    return {"total_chats": total, "indexed_chats": indexed, "ready": indexed > 0}


def hybrid_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 50,
) -> list[dict]:
    """Hibrid keresés: FTS5 + embedding kombinált rangsorolás.

    Ha van embedding index, a szemantikus hasonlóságot is figyelembe veszi.
    Az eredmények _score (0-1) és _source (fts5/embedding/hybrid) mezőket kapnak.

    Args:
        conn: Manifest kapcsolat.
        query: Keresési kifejezés.
        limit: Maximum találatok száma.

    Returns:
        Rangsorolt találati lista.
    """
    # 1. FTS5 keresés
    from gemini_export.search import _search_chats as fts5_search
    fts5_results = fts5_search(conn, query, limit=limit * 2)

    fts5_map: dict[str, dict] = {}
    total_fts = max(len(fts5_results), 1)
    for i, r in enumerate(fts5_results):
        score = 1.0 - i / max(total_fts - 1, 1)  # Első: 1.0, utolsó: ~0.0
        r["_score"] = round(score, 4)
        r["_source"] = "fts5"
        fts5_map[r["cid"]] = r

    # 2. Embedding keresés (ha van index)
    emb_results: list[dict] = []
    try:
        _ensure_rag_schema(conn)
        has_embeddings = conn.execute(
            "SELECT COUNT(*) FROM chat_embeddings"
        ).fetchone()[0] > 0

        if has_embeddings:
            question_embedding = generate_embedding(query)
            if question_embedding:
                rows = conn.execute(
                    "SELECT ce.chat_id, ce.embedding, e.title, "
                    "e.last_exported_at, e.message_count, e.status, "
                    "m.tags, m.project, m.is_favorite "
                    "FROM chat_embeddings ce "
                    "JOIN exports e ON ce.chat_id = e.chat_id "
                    "LEFT JOIN chat_metadata m ON ce.chat_id = m.chat_id"
                ).fetchall()

                scored = []
                for row in rows:
                    (cid, emb_json, title, exported_at, msg_count,
                     status, tags_json, project, fav) = row
                    try:
                        emb = json.loads(emb_json)
                        sim = _cosine_similarity(question_embedding, emb)
                        scored.append((
                            sim, cid, title, exported_at,
                            msg_count, status, tags_json, project, fav,
                        ))
                    except (json.JSONDecodeError, TypeError):
                        continue

                scored.sort(key=lambda x: x[0], reverse=True)
                for sim, cid, title, exported_at, _msg_count, status, tags_json, project, fav in scored[:limit * 2]:
                    if sim < 0.25:
                        continue
                    tags = []
                    if tags_json:
                        try:
                            tags = json.loads(tags_json)
                        except json.JSONDecodeError:
                            pass
                    emb_results.append({
                        "cid": cid, "title": title or "Untitled",
                        "exported_at": exported_at,
                        "message_count": _msg_count or 0,
                        "status": status, "tags": tags, "project": project,
                        "is_favorite": bool(fav), "processing_status": "new",
                        "_score": round(sim, 4), "_source": "embedding",
                        "_raw_emb_score": round(sim, 4),
                    })
    except Exception:
        pass  # Embedding hiba esetén csak FTS5

    # 3. Merge: FTS5 + embedding kombinálása
    merged: dict[str, dict] = {}

    for r in emb_results:
        cid = r["cid"]
        r["_score"] = round(r["_score"] * 0.6, 4)
        merged[cid] = r

    for r in fts5_results:
        cid = r["cid"]
        if cid in merged:
            # Blend: 40% FTS5 + 60% embedding (raw cosine similarity)
            raw_emb = merged[cid].get("_raw_emb_score", merged[cid]["_score"])
            merged[cid]["_score"] = round(0.4 * r["_score"] + 0.6 * raw_emb, 4)
            merged[cid]["_source"] = "hybrid"
            merged[cid].pop("_raw_emb_score", None)
        else:
            r["_score"] = round(r["_score"] * 0.4, 4)
            merged[cid] = r

    results = sorted(merged.values(), key=lambda x: x["_score"], reverse=True)[:limit]
    return results


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


# ─── Chat összehasonlítás ───────────────────────────────────────────────────

def compare_chats(
    conn: sqlite3.Connection,
    output_dir: Path,
    cid_a: str,
    cid_b: str,
    perspective: str = "general",
) -> Generator[str, None, None]:
    """Két chat AI összehasonlító elemzése, SSE streaming válasszal.

    Args:
        conn: Manifest kapcsolat.
        output_dir: Az exports könyvtár.
        cid_a: Az első chat ID-je.
        cid_b: A második chat ID-je.
        perspective: Az összehasonlítás nézőpontja:
            "general" — általános összehasonlítás
            "differences" — mi a különbség?
            "similarities" — miben hasonlítanak?
            "detail" — melyik a részletesebb?

    Yields:
        SSE event string-ek.
    """
    # Chat adatok betöltése
    def _load_chat(cid: str) -> dict | None:
        pattern = f"*_{cid[:8]}.json"
        json_files = list(output_dir.glob(f"json/{pattern}"))
        if not json_files:
            return None
        try:
            return json.loads(json_files[0].read_text(encoding="utf-8"))
        except Exception:
            return None

    data_a = _load_chat(cid_a)
    data_b = _load_chat(cid_b)

    if not data_a:
        yield f"event: error\ndata: Az első chat ({cid_a[:12]}) nem található.\n\n"
        return
    if not data_b:
        yield f"event: error\ndata: A második chat ({cid_b[:12]}) nem található.\n\n"
        return

    title_a = data_a.get("title", "Untitled")
    title_b = data_b.get("title", "Untitled")
    turns_a = data_a.get("turns", [])
    turns_b = data_b.get("turns", [])

    # Chat info küldése
    info = {
        "cid_a": cid_a, "title_a": title_a, "msg_count_a": len(turns_a),
        "cid_b": cid_b, "title_b": title_b, "msg_count_b": len(turns_b),
    }
    yield f"event: info\ndata: {json.dumps(info, ensure_ascii=False)}\n\n"

    # Tartalom formázása (rövidebb limit összehasonlításhoz, mivel két chat van)
    content_a = _format_chat_for_ai(turns_a, title_a, max_chars=6000)
    content_b = _format_chat_for_ai(turns_b, title_b, max_chars=6000)

    # Nyelv felismerése
    has_hungarian = any(c in title_a + title_b for c in "áéíóöőúüűÁÉÍÓÖŐÚÜŰ")
    lang_instruction = "magyarul" if has_hungarian else "in English"

    # Perspektíva szöveg
    perspective_map = {
        "general": "Provide a general comparison: compare topics, depth, tone, and usefulness.",
        "differences": "Focus on the DIFFERENCES: what did one conversation cover that the other missed? Where do they diverge in approach or conclusions?",
        "similarities": "Focus on the SIMILARITIES: what common themes, ideas, or patterns appear in both conversations?",
        "detail": "Compare the LEVEL OF DETAIL: which conversation goes deeper? Which has more specific examples, data, or actionable advice?",
    }
    perspective_text = perspective_map.get(perspective, perspective_map["general"])

    # Egyedi vagy default prompt betöltése
    try:
        from gemini_export.prompt_templates import render_prompt  # noqa: F811
    except ImportError:
        render_prompt = lambda *a, **kw: None  # noqa: E731

    system_prompt = render_prompt("compare", {
        "title_a": title_a,
        "content_a": content_a,
        "title_b": title_b,
        "content_b": content_b,
        "perspective": perspective_text,
        "lang": lang_instruction,
    })

    if not system_prompt:
        system_prompt = (
            f"You are an expert at comparing and contrasting conversations. "
            f"Analyze the two conversations and provide a thorough comparison. "
            f"Focus on the requested perspective: {perspective_text}. "
            f"Answer {lang_instruction}.\n\n"
            f"## Conversation A: {title_a}\n{content_a}\n\n"
            f"## Conversation B: {title_b}\n{content_b}\n\n"
            f"Structure your response with Summary, Comparison, and Key Takeaways sections. "
            f"Be concise, specific, and cite examples from both conversations."
        )

    try:
        provider = _get_provider()
        for token in provider.chat_completion_stream(
            model=_get_model(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Compare: '{title_a}' vs '{title_b}'"},
            ],
            max_tokens=max(_get_max_tokens(), 800),
            temperature=0.4,
        ):
            yield f"data: {token}\n\n"

        yield "event: done\ndata: {}\n\n"

    except Exception as e:
        yield f"event: error\ndata: Hiba az összehasonlítás során: {str(e)}\n\n"


# ─── Batch elemzés ──────────────────────────────────────────────────────────

def batch_analyze_all(
    conn: sqlite3.Connection,
    output_dir: Path,
    options: dict | None = None,
) -> Generator[str, None, None]:
    """Az összes még nem elemzett chat batch AI elemzése, SSE streaming progress-szel.

    SSE event-eket yield-el a haladásról:
        event: progress  — {"current": N, "total": M, "cid": ..., "title": ...}
        event: result    — az adott chat elemzési eredménye
        event: done      — {"analyzed": N, "failed": N, "skipped": N, "total": N}
        event: error     — hibaüzenet

    Args:
        conn: Manifest kapcsolat.
        output_dir: Az exports könyvtár.
        options: Dict a funkciókhoz (alap: {"summarize": True, "todos": True, "tags": True}).

    Yields:
        SSE event string-ek.
    """
    if options is None:
        options = {"summarize": True, "todos": True, "tags": True}

    _ensure_ai_schema(conn)

    # Összes exportált chat lekérése
    rows = conn.execute("""
        SELECT e.chat_id, e.title, e.message_count,
               m.analyzed_at
        FROM exports e
        LEFT JOIN chat_metadata m ON e.chat_id = m.chat_id
        WHERE e.status = 'ok'
        ORDER BY e.last_exported_at DESC
    """).fetchall()

    total = len(rows)
    if total == 0:
        yield "event: error\ndata: Nincsenek exportált beszélgetések. Először futtasd az exportot!\n\n"
        return

    # Számoljuk meg a már elemzetteket
    already_analyzed = sum(1 for r in rows if r[3] is not None)
    unanalyzed = total - already_analyzed

    if unanalyzed == 0:
        done_msg = json.dumps({
            "analyzed": 0, "failed": 0, "skipped": total,
            "total": total, "message": "Minden chat már elemezve van!",
        }, ensure_ascii=False)
        yield f"event: done\ndata: {done_msg}\n\n"
        return

    start_msg = json.dumps({
        "total": total, "unanalyzed": unanalyzed,
        "already_analyzed": already_analyzed,
    }, ensure_ascii=False)
    yield f"event: start\ndata: {start_msg}\n\n"

    analyzed = 0
    failed = 0
    skipped = 0

    for i, (cid, title, _msg_count, analyzed_at) in enumerate(rows):
        # Ha már elemezve van, kihagyjuk
        if analyzed_at is not None:
            skipped += 1
            progress = {
                "current": i + 1,
                "total": total,
                "cid": cid,
                "title": (title or "Untitled")[:80],
                "status": "skipped",
                "analyzed": analyzed,
                "failed": failed,
                "skipped": skipped,
            }
            yield f"event: progress\ndata: {json.dumps(progress, ensure_ascii=False)}\n\n"
            continue

        # Progress: elemzés előtt
        progress = {
            "current": i + 1,
            "total": total,
            "cid": cid,
            "title": (title or "Untitled")[:80],
            "status": "analyzing",
            "analyzed": analyzed,
            "failed": failed,
            "skipped": skipped,
        }
        yield f"event: progress\ndata: {json.dumps(progress, ensure_ascii=False)}\n\n"

        # JSON fájl betöltése
        pattern = f"*_{cid[:8]}.json"
        json_files = list(output_dir.glob(f"json/{pattern}"))
        if not json_files:
            failed += 1
            progress["status"] = "failed"
            progress["error"] = "JSON file not found"
            progress["failed"] = failed
            yield f"event: progress\ndata: {json.dumps(progress, ensure_ascii=False)}\n\n"
            continue

        try:
            data = json.loads(json_files[0].read_text(encoding="utf-8"))
            chat_title = data.get("title", title or "Untitled")
            turns = data.get("turns", [])

            result = analyze_chat(conn, cid, turns, chat_title, options)

            if result.get("error"):
                failed += 1
                progress["status"] = "failed"
                progress["error"] = result["error"]
                progress["failed"] = failed
            else:
                analyzed += 1
                progress["status"] = "done"
                progress["analyzed"] = analyzed
                # Eredmény küldése
                result_event = {
                    "cid": cid,
                    "title": chat_title[:80],
                    "summary": result.get("summary"),
                    "tags": result.get("tags"),
                    "todo_count": len(result.get("todos") or []),
                }
                yield f"event: result\ndata: {json.dumps(result_event, ensure_ascii=False)}\n\n"

            yield f"event: progress\ndata: {json.dumps(progress, ensure_ascii=False)}\n\n"

        except Exception as e:
            failed += 1
            progress["status"] = "failed"
            progress["error"] = str(e)
            progress["failed"] = failed
            yield f"event: progress\ndata: {json.dumps(progress, ensure_ascii=False)}\n\n"

    # Végleges összesítő
    done_data = {
        "analyzed": analyzed,
        "failed": failed,
        "skipped": skipped,
        "total": total,
    }
    yield f"event: done\ndata: {json.dumps(done_data, ensure_ascii=False)}\n\n"
