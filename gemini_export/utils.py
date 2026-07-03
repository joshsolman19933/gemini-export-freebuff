"""
Utility functions for the Gemini Chat Exporter.
================================================
File name sanitization, timestamp formatting, date parsing,
chat filtering, image metadata extraction, and more.
"""

import json
import mimetypes
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from gemini_export.logging_config import get_logger

logger = get_logger(__name__)

# ─── File name utilities ────────────────────────────────────────────────────

def sanitize_filename(name: str, max_length: int = 80) -> str:
    """Fájlnévként használható formátumra alakítja a beszélgetés címét."""
    sanitized = re.sub(r"[\\/:*?\"<>|]", "_", name)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rsplit(" ", 1)[0]
    return sanitized or "untitled"


# ─── Timestamp utilities ────────────────────────────────────────────────────

def format_timestamp() -> str:
    """Aktuális időbélyeg ISO formátumban."""
    return datetime.now(timezone.utc).isoformat()


# ─── Image utilities ────────────────────────────────────────────────────────

def _guess_image_ext(url: str) -> str:
    """Kitalálja a kép kiterjesztését az URL vagy a content-type alapján."""
    # Próbáljuk kitalálni az URL-ből
    path = url.split("?")[0]
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"):
        return ext
    # Ha nincs kiterjesztés, próbáljuk mimetypes-szel
    mime, _ = mimetypes.guess_type(path)
    if mime:
        return mimetypes.guess_extension(mime) or ".png"
    return ".png"


def _extract_image_metadata(img) -> dict | None:
    """Kivonja egy Image objektum metaadatait (url, alt, title) egy szótárba."""
    if isinstance(img, dict):
        url = img.get("url", "")
        alt = img.get("alt", "") or img.get("title", "")
        return {"url": url, "alt": str(alt)} if url else None

    url = getattr(img, "url", None)
    if not url:
        return None
    alt = getattr(img, "alt", None) or getattr(img, "title", None) or ""
    return {"url": str(url), "alt": str(alt)}


# ─── Date and filtering utilities ───────────────────────────────────────────

def parse_date(date_str: str) -> float:
    """Dátum string (ÉÉÉÉ-HH-NN) átalakítása Unix timestamp-pé."""
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    raise ValueError(f"Nem ertelmezheto dátum: {date_str}. Hasznalj ÉÉÉÉ-HH-NN formátumot.")


def filter_chats(
    chats: list,
    from_ts: float | None = None,
    to_ts: float | None = None,
    keyword: str | None = None,
) -> tuple[list, dict]:
    """Szűri a chat listát dátum és kulcsszó alapján.

    Visszaadja a szűrt listát és a szűrési statisztikát.
    """
    filtered = []
    stats = {"total": len(chats), "filtered": 0, "reason_date": 0, "reason_keyword": 0}

    for chat in chats:
        ts = getattr(chat, "timestamp", 0)

        # Dátum szűrés
        if from_ts is not None and ts < from_ts:
            stats["reason_date"] += 1
            continue
        if to_ts is not None and ts > to_ts:
            stats["reason_date"] += 1
            continue

        # Kulcsszó szűrés
        if keyword:
            title = getattr(chat, "title", "") or ""
            if keyword.lower() not in title.lower():
                stats["reason_keyword"] += 1
                continue

        filtered.append(chat)

    stats["filtered"] = len(filtered)
    return filtered, stats


def list_chats_only(chats: list) -> None:
    """Kilistázza a beszélgetéseket egy szép táblázatban, export nélkül."""
    if not chats:
        print("\n  Nincsenek megjelenitheto beszélgetések.")
        return

    print(f"\n  {'#':<5} {'Cim':<60} {'Dátum':<20} {'Chat ID'}")
    print(f"  {'-'*4}  {'-'*60} {'-'*20} {'-'*20}")

    for i, chat in enumerate(chats, 1):
        title = getattr(chat, "title", "Untitled") or "Untitled"
        cid = getattr(chat, "cid", "??") or "??"
        ts = getattr(chat, "timestamp", 0)
        pinned = getattr(chat, "is_pinned", False)

        if ts > 0:
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        else:
            date_str = "?"

        pin_mark = "📌 " if pinned else "  "
        display_title = f"{pin_mark}{title[:57]}"

        print(f"  {i:<5} {display_title:<60} {date_str:<20} {cid}")

    print(f"\n  Összesen: {len(chats)} beszélgetés.")


# ─── Export state utilities ─────────────────────────────────────────────────

def already_exported(cid: str, output_dir: Path, formats: list[str]) -> bool:
    """Ellenőrzi, hogy egy adott chat már exportálva van-e (manifest + fájl alapú)."""
    # Fájl alapú ellenőrzés (gyors, nem igényel DB-t)
    ext_map = {"json": "json", "markdown": "md", "html": "html"}
    for fmt in formats:
        ext = ext_map.get(fmt)
        if ext:
            pattern = f"*_{cid[:8]}.{ext}"
            if list(output_dir.glob(f"{fmt}/{pattern}")):
                return True
    return False


# ─── Chat turn data extraction ──────────────────────────────────────────────

def extract_turn_data(turn) -> dict:
    """Kivonja egy ChatTurn objektum adatait egy szótárba.

    A képeket (Image objektumok) metaadattá alakítjuk (url, alt),
    a tényleges letöltés később, a _download_turn_images hívással történik.
    """
    data = {
        "role": getattr(turn, "role", "unknown"),
        "text": getattr(turn, "text", ""),
    }

    # Képek: nyers Image objektumok elmentése a későbbi letöltéshez
    if hasattr(turn, "images") and turn.images:
        data["images_raw"] = list(turn.images)

    # Egyéb attribútumok biztonságos kinyerése
    for attr in ("timestamp", "thoughts", "videos", "audio", "citations"):
        try:
            if hasattr(turn, attr):
                val = getattr(turn, attr)
                if val is not None:
                    try:
                        json.dumps(val, ensure_ascii=False)
                        data[attr] = val
                    except (TypeError, ValueError):
                        data[attr] = str(val)
        except Exception:
            pass
    return data
