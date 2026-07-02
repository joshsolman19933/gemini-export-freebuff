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

import argparse
import asyncio
import csv
import hashlib
import html as html_mod
import json
import mimetypes
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import orjson
from dotenv import load_dotenv
from gemini_webapi import GeminiClient
from gemini_webapi.constants import GRPC
from gemini_webapi.types import RPCData, ChatInfo
from gemini_webapi.utils import extract_json_from_response, get_nested_value

try:
    from ai_layer import (
        analyze_chat, _ensure_ai_schema, _store_ai_results,
        generate_summary, extract_todos, suggest_tags,
    )
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False


# ─── Konfiguráció ────────────────────────────────────────────────────────────

def load_config() -> dict:
    """Betölti a környezeti változókat .env fájlból és a rendszer környezetből."""
    load_dotenv()
    return {
        "secure_1psid": os.getenv("GEMINI_SECURE_1PSID", ""),
        "secure_1psidts": os.getenv("GEMINI_SECURE_1PSIDTS", ""),
        "auto_cookies": os.getenv("GEMINI_AUTO_COOKIES", "") == "1",
        "output_dir": os.getenv("EXPORT_OUTPUT_DIR", "./exports"),
        "delay": float(os.getenv("EXPORT_DELAY", "0.5")),
    }


# ─── Segédfüggvények ─────────────────────────────────────────────────────────

def sanitize_filename(name: str, max_length: int = 80) -> str:
    """Fájlnévként használható formátumra alakítja a beszélgetés címét."""
    sanitized = re.sub(r"[\\/:*?\"<>|]", "_", name)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rsplit(" ", 1)[0]
    return sanitized or "untitled"


def format_timestamp() -> str:
    """Aktuális időbélyeg ISO formátumban."""
    return datetime.now(timezone.utc).isoformat()


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


async def _download_turn_images(
    turns: list[dict],
    cid: str,
    output_dir: Path,
    session: aiohttp.ClientSession,
    print_lock: asyncio.Lock | None = None,
    index: int = 0,
    total: int = 0,
) -> int:
    """Letölti az üzenetekben található képeket és frissíti a turn adatokat.

    Minden turn "images" listáját átalakítja: az eredeti metaadatok megmaradnak,
    és hozzáadódik a "downloaded_path" helyi elérési út (relatív a fájlokhoz képest).

    Visszaadja a sikeresen letöltött képek számát.
    """
    media_dir = output_dir / "media" / cid[:12]
    media_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0

    for turn in turns:
        images_raw = turn.pop("images_raw", None)
        if not images_raw:
            continue

        image_entries = []
        for img in images_raw:
            meta = _extract_image_metadata(img)
            if not meta:
                continue

            url = meta["url"]
            alt = meta["alt"]

            # Generáljunk egyedi fájlnevet az URL hash-éből
            url_hash = hashlib.md5(url.encode()).hexdigest()[:10]
            ext = _guess_image_ext(url)
            filename = f"{url_hash}{ext}"
            filepath = media_dir / filename

            # Csak akkor töltsük le, ha még nincs meg
            if not filepath.exists():
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status == 200:
                            filepath.write_bytes(await resp.read())
                            downloaded += 1
                except Exception:
                    pass  # Csendben kihagyjuk a sikertelen letöltéseket

            # Relatív út: csak akkor, ha a fájl valóban létezik
            # A markdown/ és html/ alkönyvtárakból ../media/ a helyes út,
            # az all_chats.html esetén a hívó oldal kezeli a formázást
            if filepath.exists():
                rel_path = f"../media/{cid[:12]}/{filename}"
                image_entries.append({
                    "url": url,
                    "alt": alt,
                    "downloaded_path": rel_path,
                    "local_filename": filename,
                })
            else:
                # A letöltés sikertelen — csak az eredeti URL-t őrizzük meg
                image_entries.append({
                    "url": url,
                    "alt": alt,
                    "downloaded_path": None,
                    "local_filename": None,
                })

        turn["images"] = image_entries

    if downloaded > 0 and print_lock:
        async with print_lock:
            print(f"[{index}/{total}]   └─ {downloaded} kép letoltve", flush=True)

    return downloaded


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


def export_chat_to_markdown(chat_data: dict, output_dir: Path) -> Path:
    """Egy beszélgetés Markdown formátumba mentése."""
    title = chat_data.get("title", "Untitled")
    cid = chat_data.get("cid", "unknown")
    turns = chat_data.get("turns", [])
    exported_at = chat_data.get("exported_at", format_timestamp())

    filename = f"{sanitize_filename(title)}_{cid[:8]}.md"
    filepath = output_dir / "markdown" / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# {title}\n",
        f"**Chat ID:** `{cid}`",
        f"**Exportálva:** {exported_at}",
        f"**Üzenetek száma:** {len(turns)}\n",
        "---\n",
    ]

    # A turns lista újtól régi felé van rendezve (gemini_webapi alapértelmezés),
    # így megfordítjuk, hogy a beszélgetés időrendben jelenjen meg
    for turn in reversed(turns):
        role = turn.get("role", "unknown").upper()
        text = turn.get("text", "")

        if role == "USER":
            lines.append(f"### 👤 Te\n\n{text}\n")
        elif role == "MODEL":
            lines.append(f"### 🤖 Gemini\n\n{text}\n")
        else:
            lines.append(f"### {role}\n\n{text}\n")

        # Képek
        images = turn.get("images", [])
        for img in images:
            alt = img.get("alt", "Kép")
            path = img.get("downloaded_path", img.get("url", ""))
            lines.append(f"\n![{alt}]({path})\n")

        lines.append("---\n")

    filepath.write_text("\n".join(lines), encoding="utf-8")
    return filepath


def export_chat_to_json(chat_data: dict, output_dir: Path) -> Path:
    """Egy beszélgetés egyedi JSON fájlba mentése."""
    cid = chat_data.get("cid", "unknown")
    title = chat_data.get("title", "Untitled")

    filename = f"{sanitize_filename(title)}_{cid[:8]}.json"
    filepath = output_dir / "json" / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)

    filepath.write_text(
        json.dumps(chat_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return filepath


def _html_escape(text: str) -> str:
    """HTML escape + új sorok megőrzése <br>-rel."""
    escaped = html_mod.escape(text, quote=False)
    return escaped.replace("\n", "<br>")


def _build_html_chat_content(chat_data: dict, include_style: bool = True, full_document: bool = True) -> str:
    """Egy beszélgetés HTML tartalmának generálása.

    Args:
        chat_data: A chat adatai.
        include_style: Ha True, beágyazott CSS-t is tartalmaz.
        full_document: Ha True, teljes HTML dokumentum (<!DOCTYPE>...<html>).
                       Ha False, csak a belső tartalom (<h1>, meta, turns).
    """
    title = chat_data.get("title", "Untitled")
    cid = chat_data.get("cid", "unknown")
    turns = chat_data.get("turns", [])
    exported_at = chat_data.get("exported_at", "")

    css = ""
    if include_style:
        css = """<style>
:root{--bg:#0f1117;--surface:#1a1d27;--border:#2e3345;--text:#e1e4ed;--text-dim:#8b8fa8;--accent:#6c8eff;--user-bg:#1a2740;--model-bg:#1a1d27}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);max-width:800px;margin:0 auto;padding:1.5rem;line-height:1.7}
h1{font-size:1.5rem;margin-bottom:.25rem;background:linear-gradient(135deg,var(--accent),#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.meta{color:var(--text-dim);font-size:.8rem;margin-bottom:2rem;padding-bottom:1rem;border-bottom:1px solid var(--border)}
.turn{margin-bottom:1.5rem;padding:1rem;border-radius:10px;animation:fadeIn .3s}
.turn-user{background:var(--user-bg);border-left:3px solid var(--accent)}
.turn-model{background:var(--model-bg);border-left:3px solid #34d399}
.role{font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:.5rem}
.role-user{color:var(--accent)}.role-model{color:#34d399}
.text{white-space:pre-wrap;word-break:break-word}
.images{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.75rem}
.images img{max-width:100%;max-height:400px;border-radius:8px;border:1px solid var(--border);object-fit:contain}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
</style>"""

    # Fordított sorrendben vannak (újtól régi), megfordítjuk
    turns_html = []
    for turn in reversed(turns):
        role = turn.get("role", "unknown").upper()
        text = _html_escape(turn.get("text", ""))
        role_class = "turn-user" if role == "USER" else "turn-model"
        role_label_class = "role-user" if role == "USER" else "role-model"
        role_label = "Te" if role == "USER" else "Gemini"

        # Képek HTML generálása
        images_html = ""
        images = turn.get("images", [])
        if images:
            imgs = []
            for img in images:
                alt = html_mod.escape(img.get("alt", "Kép"))
                path = img.get("downloaded_path", img.get("url", ""))
                imgs.append(f'<img src="{html_mod.escape(path)}" alt="{alt}" loading="lazy">')
            images_html = f'<div class="images">{"".join(imgs)}</div>'

        turns_html.append(
            f'<div class="turn {role_class}">'
            f'<div class="role {role_label_class}">{role_label}</div>'
            f'<div class="text">{text}</div>'
            f'{images_html}'
            f'</div>'
        )

    # A body tartalom mindig kell (teljes dokumentumhoz és önálló tartalomként is)
    body = f"""<h1>{html_mod.escape(title)}</h1>
<div class="meta">
  Chat ID: <code>{html_mod.escape(cid)}</code><br>
  Exportálva: {html_mod.escape(exported_at)}<br>
  Üzenetek: {len(turns)}
</div>
{''.join(turns_html)}"""

    if full_document:
        return f"""<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{html_mod.escape(title)}</title>
{css}
</head>
<body>
{body}
</body>
</html>"""
    else:
        return body


def export_chat_to_html(chat_data: dict, output_dir: Path) -> Path:
    """Egy beszélgetés HTML fájlba mentése."""
    title = chat_data.get("title", "Untitled")
    cid = chat_data.get("cid", "unknown")

    filename = f"{sanitize_filename(title)}_{cid[:8]}.html"
    filepath = output_dir / "html" / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)

    content = _build_html_chat_content(chat_data, include_style=True)
    filepath.write_text(content, encoding="utf-8")
    return filepath


def export_chat_to_csv(csv_writer, chat_data: dict) -> None:
    """Egy beszélgetés üzeneteinek CSV-be írása (append)."""
    title = chat_data.get("title", "Untitled")
    cid = chat_data.get("cid", "unknown")
    turns = chat_data.get("turns", [])

    for turn in reversed(turns):
        role = turn.get("role", "unknown")
        text = turn.get("text", "")
        csv_writer.writerow([cid, title, role.upper(), text])


def generate_all_chats_html(all_chats_data: list[dict], output_dir: Path) -> Path:
    """Összes beszélgetés egyetlen, onallo HTML fajlban - navigációval, keresssel."""
    filepath = output_dir / "all_chats.html"

    # Navigáció
    nav_items = []
    chat_contents = []
    for i, chat in enumerate(all_chats_data):
        cid = chat.get("cid", "")
        title = chat.get("title", "Untitled")
        safe_title = html_mod.escape(title)
        nav_items.append(
            f'<a href="#chat-{i}" class="nav-item" data-index="{i}">'
            f'{safe_title[:80]}</a>'
        )
        chat_html = _build_html_chat_content(chat, include_style=False, full_document=False)
        # all_chats.html az exports/ gyokerben van, nem alkönyvtárban
        chat_html = chat_html.replace("../media/", "media/")
        chat_contents.append(f'<div class="chat-section" id="chat-{i}">{chat_html}</div>')

    html = f"""<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Gemini Chat Export</title>
<style>
:root{{--bg:#0f1117;--surface:#1a1d27;--border:#2e3345;--text:#e1e4ed;--text-dim:#8b8fa8;--accent:#6c8eff;--user-bg:#1a2740;--model-bg:#1a1d27;--nav-w:260px}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);display:flex;min-height:100vh}}
.sidebar{{width:var(--nav-w);min-width:var(--nav-w);background:var(--surface);border-right:1px solid var(--border);height:100vh;position:fixed;overflow-y:auto;padding:1rem;z-index:10}}
.sidebar h2{{font-size:.85rem;color:var(--accent);margin-bottom:.75rem;text-transform:uppercase;letter-spacing:.05em}}
.search-box{{width:100%;padding:.5rem .75rem;background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:6px;margin-bottom:1rem;font-size:.8rem;outline:none}}
.search-box:focus{{border-color:var(--accent)}}
.nav-item{{display:block;padding:.4rem .5rem;font-size:.8rem;color:var(--text-dim);text-decoration:none;border-radius:4px;margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;transition:background .15s,color .15s}}
.nav-item:hover,.nav-item.active{{background:var(--user-bg);color:var(--text)}}
.nav-item.hidden{{display:none}}
.main{{margin-left:var(--nav-w);flex:1;padding:2rem;max-width:calc(100% - var(--nav-w))}}
.chat-section{{margin-bottom:3rem;padding-bottom:2rem;border-bottom:1px solid var(--border)}}
h1{{font-size:1.5rem;background:linear-gradient(135deg,var(--accent),#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.meta{{color:var(--text-dim);font-size:.8rem;padding-bottom:1rem;border-bottom:1px solid var(--border);margin-bottom:1.5rem}}
.turn{{margin-bottom:1.5rem;padding:1rem;border-radius:10px}}
.turn-user{{background:var(--user-bg);border-left:3px solid var(--accent)}}
.turn-model{{background:var(--model-bg);border-left:3px solid #34d399}}
.role{{font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:.5rem}}
.role-user{{color:var(--accent)}}.role-model{{color:#34d399}}
.text{{white-space:pre-wrap;word-break:break-word}}
.images{{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.75rem}}
.images img{{max-width:100%;max-height:400px;border-radius:8px;border:1px solid var(--border);object-fit:contain}}
.stats{{background:var(--surface);border-radius:8px;padding:1rem;margin-bottom:1rem}}
.stats-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:.5rem}}
.stat-val{{font-size:1.2rem;font-weight:700}}
.stat-lbl{{font-size:.7rem;color:var(--text-dim);text-transform:uppercase}}
@media(max-width:768px){{.sidebar{{display:none}}.main{{margin-left:0;max-width:100%}}}}
</style>
</head>
<body>
<nav class="sidebar">
<h2>Beszélgetések ({len(all_chats_data)})</h2>
<input type="text" class="search-box" placeholder="Keresés..." oninput="filterChats(this.value)" autofocus>
<div id="navList">{''.join(nav_items)}</div>
</nav>
<main class="main">
<div class="stats">
<div class="stats-grid">
<div><div class="stat-val">{len(all_chats_data)}</div><div class="stat-lbl">Beszélgetés</div></div>
<div><div class="stat-val">{sum(c.get('turn_count',0) for c in all_chats_data)}</div><div class="stat-lbl">Üzenet</div></div>
<div><div class="stat-val">{format_timestamp()[:10]}</div><div class="stat-lbl">Export dátuma</div></div>
</div>
</div>
{''.join(chat_contents)}
</main>
<script>
function filterChats(q) {{
  q=q.toLowerCase().trim();
  document.querySelectorAll('.nav-item').forEach(el=>{{
    el.classList.toggle('hidden',q&&!el.textContent.toLowerCase().includes(q))
  }});
  document.querySelectorAll('.chat-section').forEach((el,i)=>{{
    if(!q){{el.style.display=''}}
    else{{
      const el2=document.querySelector('.nav-item[data-index="'+i+'"]');
      el.style.display=(el2&&!el2.classList.contains('hidden'))?'':'none'
    }}
  }})
}}
</script>
</body>
</html>"""

    filepath.write_text(html, encoding="utf-8")
    return filepath


# ─── Manifest adatbázis (export állapot követése) ────────────────────────────

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


# ─── FTS5 kereső és metaadat kezelés ─────────────────────────────────────

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
        print(f"  Több találat is van erre a prefixre ({len(rows)}). Adj meg pontosabb ID-t.")
        return None
    else:
        print(f"  Nincs találat erre az ID-re: {partial}")
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
    print(f"  Cimkek: {', '.join(merged)}")


def _set_project(conn: sqlite3.Connection, cid: str, project: str) -> None:
    """Projektet rendel egy chat-hez."""
    _ensure_metadata_row(conn, cid)
    conn.execute(
        "UPDATE chat_metadata SET project = ?, updated_at = ? WHERE chat_id = ?",
        (project, format_timestamp(), cid),
    )
    conn.commit()
    print(f"  Projekt: {project}")


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


# ─── Paginált chat lista lekérés ─────────────────────────────────────────────

async def _fetch_chats_paginated(client: GeminiClient, max_total: int) -> list[ChatInfo]:
    """Page token alapú paginációval lekéri az összes beszélgetést.

    A Gemini API ~100-as batch limitet használ. A válasz `part_body`
    struktúrája: [None, token_string, chat_list]. Az index 1-en lévő
    string a következő oldal tokenje, amit a kérés második
    paramétereként kell visszaküldeni: [100, token, [filter]].
    """
    all_chats: list[ChatInfo] = []

    # Két RPC típust használunk (mint az eredeti _fetch_recent_chats):
    # [1, None, 1] és [0, None, 1] - különböző szűrők/nézetek
    # Mindkettőt pagináljuk a teljesség érdekében.
    rpc_filters = [
        [1, None, 1],   # pinned/first view
        [0, None, 1],   # unpinned/second view
    ]

    for rpc_filter in rpc_filters:
        page_token = None
        while len(all_chats) < max_total:
            # Használjuk az orjson-t, mert a gemini_webapi is ezt használja
            # (bytes-t ad vissza, .decode("utf-8")-al stringgé alakítjuk)
            payload = orjson.dumps([100, page_token, rpc_filter]).decode("utf-8")

            try:
                response = await client._batch_execute([
                    RPCData(rpcid=GRPC.LIST_CHATS, payload=payload)
                ])
            except Exception:
                break  # Ha hibázik, lépjünk a következő filterre

            chats_json = extract_json_from_response(response.text)
            has_more = False

            for part in chats_json:
                part_body_str = get_nested_value(part, [2])
                if not part_body_str:
                    continue
                try:
                    part_body = json.loads(part_body_str)
                except json.JSONDecodeError:
                    continue

                # Chat lista kinyerése
                chat_list = get_nested_value(part_body, [2])
                if isinstance(chat_list, list):
                    for chat_data in chat_list:
                        if not isinstance(chat_data, list) or len(chat_data) < 2:
                            continue
                        cid = get_nested_value(chat_data, [0], "")
                        title = get_nested_value(chat_data, [1], "")
                        is_pinned = bool(get_nested_value(chat_data, [2]))
                        timestamp_data = get_nested_value(chat_data, [5])
                        timestamp = 0.0
                        if isinstance(timestamp_data, list) and len(timestamp_data) >= 2:
                            timestamp = float(timestamp_data[0]) + float(timestamp_data[1]) / 1e9

                        if cid and not any(c.cid == cid for c in all_chats):
                            all_chats.append(ChatInfo(
                                cid=cid, title=title,
                                is_pinned=is_pinned, timestamp=timestamp,
                            ))

                # Page token kinyerése: a part_body struktúra [None, str_token, list]
                # a token az index 1-en lévő base64-szerű string
                next_token = get_nested_value(part_body, [1])
                if isinstance(next_token, str) and next_token:
                    page_token = next_token
                    has_more = True

            if not has_more:
                break  # Nincs több oldal ennél a filternél

    return all_chats


# ─── Retry/backoff logika ──────────────────────────────────────────────────

async def _retry_read_chat(
    client: GeminiClient,
    cid: str,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> object:
    """API hívás újrapróbálása exponential backoff-fal.

    Args:
        client: GeminiClient példány
        cid: Chat ID
        max_retries: Maximális újrapróbálások száma (alap: 3)
        base_delay: Alap késleltetés másodpercben (exponenciálisan nő: 1s, 2s, 4s)

    Returns:
        A history objektum, vagy kivételt dob.
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return await client.read_chat(cid)
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                wait = base_delay * (2 ** attempt)
                await asyncio.sleep(wait)
    raise last_error  # type: ignore[misc]


# ─── Fő exportálási logika ───────────────────────────────────────────────────

async def _export_single_chat(
    client: GeminiClient,
    chat_info: ChatInfo,
    index: int,
    total: int,
    output_dir: Path,
    formats: list[str],
    resume: bool,
    delay: float,
    sem: asyncio.Semaphore,
    csv_writer,
    csv_lock: asyncio.Lock,
    print_lock: asyncio.Lock,
    http_session: aiohttp.ClientSession,
    manifest_conn: sqlite3.Connection | None = None,
    ai_analyze: bool = False,
) -> dict | None:
    """Egyetlen beszélgetés letöltése és exportálása. Párhuzamos futtatásra tervezve.

    Visszaad egy dict-et az eredményekkel.
    """
    cid = chat_info.cid
    title = getattr(chat_info, "title", "Untitled")
    title = title or "Untitled"

    # Manifest-alapú resume: timestamp összehasonlítással érzékeli a változásokat
    chat_ts = getattr(chat_info, "timestamp", 0) or 0
    if resume and manifest_conn and not _manifest_needs_export(manifest_conn, cid, formats, chat_ts):
        async with print_lock:
            print(f"[{index}/{total}] {title[:80]}... [~] (már exportálva)", flush=True)
        return {"status": "skipped", "cid": cid}

    # Fallback: fájl-alapú resume (ha nincs manifest vagy --no-resume nélkül fut)
    if resume and not manifest_conn and already_exported(cid, output_dir, formats):
        async with print_lock:
            print(f"[{index}/{total}] {title[:80]}... [~] (már exportálva)", flush=True)
        return {"status": "skipped", "cid": cid}

    async with sem:
        # Beszélgetés előzményeinek lekérése retry/backoff-fal
        try:
            history = await _retry_read_chat(client, cid)
        except Exception as e:
            async with print_lock:
                print(f"[{index}/{total}] {title[:80]}... [!] Hiba: {e}", flush=True)
            if manifest_conn:
                _manifest_mark_failed(manifest_conn, cid, title, str(e))
            return {"status": "failed", "cid": cid}

    # Polite pause a semaphore-n KÍVÜL
    if delay > 0:
        await asyncio.sleep(delay)

    if not history:
        async with print_lock:
            print(f"[{index}/{total}] {title[:80]}... [~] (üres előzmény)", flush=True)
        return {"status": "skipped", "cid": cid}

    # ChatTurn objektumok feldolgozása
    turns = [extract_turn_data(turn) for turn in history.turns]

    # Képek letöltése (ha vannak)
    await _download_turn_images(turns, cid, output_dir, http_session, print_lock, index, total)

    chat_data = {
        "cid": cid,
        "title": title,
        "exported_at": format_timestamp(),
        "turn_count": len(turns),
        "turns": turns,
    }

    # Exportálás a kiválasztott formátumokban
    try:
        if "json" in formats:
            export_chat_to_json(chat_data, output_dir)
        if "markdown" in formats:
            export_chat_to_markdown(chat_data, output_dir)
        if "html" in formats:
            export_chat_to_html(chat_data, output_dir)
        if "csv" in formats and csv_writer is not None:
            async with csv_lock:
                export_chat_to_csv(csv_writer, chat_data)
    except Exception as e:
        async with print_lock:
            print(f"[{index}/{total}] {title[:80]}... [!] FÁJL Hiba: {e}", flush=True)
        if manifest_conn:
            _manifest_mark_failed(manifest_conn, cid, title, str(e))
        return {"status": "failed", "cid": cid}

    # Sikeres export rögzítése a manifestben + FTS5 indexelés
    if manifest_conn:
        img_count = sum(len(t.get("images", [])) for t in turns)
        _manifest_mark_exported(manifest_conn, cid, title, len(turns), formats, img_count)
        _index_chat_for_search(manifest_conn, cid, title, turns)

        # AI elemzés (ha engedélyezve)
        if ai_analyze and AI_AVAILABLE:
            try:
                _ensure_ai_schema(manifest_conn)
                summary = generate_summary(turns, title)
                tags = suggest_tags(turns, title)
                todos = extract_todos(turns, title)
                _store_ai_results(manifest_conn, cid, summary, tags, todos)
            except Exception:
                pass  # AI hiba nem akadályozza az exportot

    async with print_lock:
        print(f"[{index}/{total}] {title[:80]}... [+] ({len(turns)} üzenet)", flush=True)

        ts = getattr(chat_info, "timestamp", 0)
        return {
            "status": "exported",
            "cid": cid,
            "chat_data": chat_data,
            "msg_count": len(turns),
            "timestamp": ts if ts > 0 else None,
        }


async def export_all_chats(
    client: GeminiClient,
    output_dir: Path,
    formats: list[str],
    delay: float,
    resume: bool,
    concurrency: int = 3,
    ai_analyze: bool = False,
) -> dict:
    """Párhuzamosan letolti es exportalja az összes beszélgetést asyncio.gather-rel."""

    print("\n[*] Beszélgetések listájának lekérése...")
    chats = list(client.list_chats()) if client.list_chats() else []

    if not chats:
        print("[!] Nincsenek beszélgetések a fiókodban, vagy nem sikerült lekérni őket.")
        return {"total": 0, "exported": 0, "skipped": 0, "failed": 0, "chats": []}

    total_count = len(chats)
    print(f"[+] {total_count} beszélgetés található.")
    print(f"[i] Párhuzamos letöltés: {concurrency} szalon (asyncio.gather)\n")

    # Szinkronizációs primitívek
    sem = asyncio.Semaphore(concurrency)
    csv_lock = asyncio.Lock()
    print_lock = asyncio.Lock()

    # CSV fájl megnyitása (ha kell)
    csv_file = None
    csv_writer = None
    if "csv" in formats:
        csv_dir = output_dir / "csv"
        csv_dir.mkdir(parents=True, exist_ok=True)
        csv_file = open(csv_dir / "chats.csv", "w", newline="", encoding="utf-8-sig")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(["chat_id", "chat_title", "role", "text"])

    # Manifest adatbázis inicializálása
    manifest_conn = _init_manifest(output_dir)

    # aiohttp session a kép letöltésekhez
    http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))

    try:
        # Indítsuk el az összes task-ot párhuzamosan
        tasks = [
            _export_single_chat(
                client, chat_info, i, total_count,
                output_dir, formats, resume, delay,
                sem, csv_writer, csv_lock, print_lock,
                http_session, manifest_conn, ai_analyze,
            )
            for i, chat_info in enumerate(chats, 1)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Eredmények feldolgozása
        all_chats_data = []
        timestamps = []
        total_messages = 0
        stats = {"total": total_count, "exported": 0, "skipped": 0, "failed": 0}

        for result in results:
            if isinstance(result, Exception):
                stats["failed"] += 1
                print(f"[!] Váratlan hiba egy task-ban: {result}")
            elif result["status"] == "exported":
                all_chats_data.append(result["chat_data"])
                stats["exported"] += 1
                total_messages += result["msg_count"]
                if result["timestamp"]:
                    timestamps.append(result["timestamp"])
            elif result["status"] == "skipped":
                stats["skipped"] += 1
            elif result["status"] == "failed":
                stats["failed"] += 1

        # Kollektív JSON export
        if "json" in formats and all_chats_data:
            all_export_path = output_dir / "all_chats.json"
            all_export_path.write_text(
                json.dumps(
                    {
                        "exported_at": format_timestamp(),
                        "total_chats": len(all_chats_data),
                        "chats": all_chats_data,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"\n[*] Összesitett JSON: {all_export_path}")

        # Single-file HTML (összes chat egy fájlban)
        if "html" in formats and all_chats_data:
            all_html_path = generate_all_chats_html(all_chats_data, output_dir)
            print(f"[*] Összesitett HTML: {all_html_path}")

        # Markdown index
        if "markdown" in formats and all_chats_data:
            index_path = output_dir / "markdown" / "INDEX.md"
            index_lines = [
                "# Gemini Beszélgetések — Index\n",
                f"**Exportálva:** {format_timestamp()}",
                f"**Beszélgetések száma:** {len(all_chats_data)}\n",
                "---\n",
            ]
            for chat in all_chats_data:
                cid = chat["cid"]
                title = chat["title"]
                filename = f"{sanitize_filename(title)}_{cid[:8]}.md"
                index_lines.append(f"- [{title}]({filename})  ")
            index_path.write_text("\n".join(index_lines), encoding="utf-8")

        stats["chats"] = all_chats_data
        stats["total_messages"] = total_messages

        # Legrégebbi és legújabb chat
        if timestamps:
            oldest_ts = min(timestamps)
            newest_ts = max(timestamps)
            stats["oldest_chat"] = datetime.fromtimestamp(oldest_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            stats["newest_chat"] = datetime.fromtimestamp(newest_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

        return stats

    finally:
        if csv_file:
            try:
                csv_file.close()
            except Exception:
                pass
        await http_session.close()
        manifest_conn.close()


# ─── Szűrés és listázás ─────────────────────────────────────────────────────

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


# ─── CLI ──────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Gemini Chat Exporter -- Az összes Gemini beszélgetés exportalasa",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Példák:
  python export.py                         # JSON + Markdown
  python export.py --format json           # Csak JSON
  python export.py --format markdown       # Csak Markdown
  python export.py --output ./my_backup    # Egyedi kimeneti mappa
  python export.py --delay 1.0             # Lassabb, biztonságosabb tempó
  python export.py --auto-cookies          # Cookie-k automatikus importálása böngészőből
  python export.py --no-resume             # Újrakezdés (felülírja a meglévő fájlokat)
        """,
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown", "html", "csv", "both", "all"],
        default="both",
        help="Export formátuma (alapértelmezett: both = json+markdown, all = json+markdown+html+csv)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Kimeneti könyvtár (alapértelmezett: ./exports vagy EXPORT_OUTPUT_DIR env)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Késleltetés másodpercben a kérések között (alapértelmezett: 0.5)",
    )
    parser.add_argument(
        "--auto-cookies",
        action="store_true",
        default=None,
        help="Cookie-k automatikus importálása a böngészőből (browser-cookie3 szükséges)",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        default=False,
        help="Ne hagyja ki a már exportált beszélgetéseket (teljes újrakezdés)",
    )
    parser.add_argument(
        "--max-chats",
        type=int,
        default=2000,
        help="Maximum lekérhető beszélgetések száma (alapértelmezett: 2000). "
             "A gemini_webapi alapesetben csak 13-at kér le -- ezzel felülírhatod.",
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        default=None,
        help="Dátum szűrés kezdete (ÉÉÉÉ-HH-NN). Csak az ez után indított chatek.",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        default=None,
        help="Dátum szűrés vége (ÉÉÉÉ-HH-NN). Csak az ez előtt indított chatek.",
    )
    parser.add_argument(
        "--filter",
        dest="keyword_filter",
        default=None,
        help="Kulcsszó szűrés a chat címére (case-insensitive). Csak a találó chatek.",
    )
    parser.add_argument(
        "--list-chats",
        action="store_true",
        default=False,
        help="Csak listázás: kilistázza a beszélgetéseket (szűrve), export nélkül.",
    )
    parser.add_argument(
        "--concurrency", "-c",
        type=int,
        default=3,
        help="Párhuzamos letöltések szama (alap: 3). Novelheted a sebessegert, de tul magas erteknel rate-limit lehet.",
    )
    parser.add_argument(
        "--search",
        default=None,
        help="FTS5 teljes szöveges keresés a korábban exportált chat-ek között.",
    )
    parser.add_argument(
        "--tag",
        nargs=2,
        metavar=("CHAT_ID", "TAGS"),
        default=None,
        help="Címkék hozzáadása egy chat-hez. Pl: --tag abc123 'AI, projekt'",
    )
    parser.add_argument(
        "--list-tags",
        action="store_true",
        default=False,
        help="Összes egyedi címke listázása.",
    )
    parser.add_argument(
        "--browse",
        action="store_true",
        default=False,
        help="Interaktív chat böngésző indítása (keresés, címkézés, kedvencek).",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        default=False,
        help="Újraindexeli az összes korábban exportált chat-et a JSON fájlokból az FTS5 keresőbe.",
    )
    parser.add_argument(
        "--ai-analyze",
        action="store_true",
        default=False,
        help="AI elemzés az exportálás után: összefoglaló, teendők, címkék (OpenAI API szükséges).",
    )
    return parser.parse_args()


async def main():
    args = parse_args()
    config = load_config()

    # Konfiguráció összeállítása (CLI > env > default)
    output_dir = Path(args.output or config["output_dir"])
    delay = args.delay if args.delay is not None else config["delay"]
    auto_cookies = args.auto_cookies if args.auto_cookies is not None else config["auto_cookies"]
    resume = not args.no_resume

    if args.format == "both":
        formats = ["json", "markdown"]
    elif args.format == "all":
        formats = ["json", "markdown", "html", "csv"]
    else:
        formats = [args.format]

    max_chats = args.max_chats

    # Dátum és kulcsszó szűrés
    from_ts = None
    to_ts = None
    if args.from_date:
        try:
            from_ts = parse_date(args.from_date)
        except ValueError as e:
            print(f"[!] {e}"); sys.exit(1)
    if args.to_date:
        try:
            to_ts = parse_date(args.to_date)
            # Ha a dátum tiszta dátum (nincs időkomponens), a nap vegeig tartson
            if "T" not in args.to_date and " " not in args.to_date:
                to_ts += 86399  # +23:59:59
        except ValueError as e:
            print(f"[!] {e}"); sys.exit(1)
    keyword = args.keyword_filter
    list_only = args.list_chats

    # ── Új tudástár parancsok (nem igényelnek API kapcsolatot) ─────────

    # Manifest DB megnyitása (olvasható legyen akkor is, ha még nincs export)
    if args.search or args.list_tags or args.browse or args.tag or args.reindex:
        output_dir.mkdir(parents=True, exist_ok=True)
        mconn = _init_manifest(output_dir)
        try:
            if args.search:
                results = _search_chats(mconn, args.search)
                print(f"\n  Találatok: '{args.search}' → {len(results)} chat")
                for r in results:
                    fav = "⭐ " if r["is_favorite"] else "  "
                    tags = ", ".join(r["tags"]) if r["tags"] else ""
                    print(f"  {fav}[{r['cid'][:8]}] {r['title'][:65]} ({r['message_count']} üzenet)")
                    if tags:
                        print(f"       Címkék: {tags}")
                return
            if args.list_tags:
                tags = _list_tags(mconn)
                print(f"\n  Címkék ({len(tags)}): {', '.join(tags) if tags else 'nincsenek'}")
                return
            if args.tag:
                cid, tag_str = args.tag
                _add_tags(mconn, cid, [t.strip() for t in tag_str.split(",")])
                return
            if args.browse:
                _browse_chats(mconn)
                return
            if args.reindex:
                count = _reindex_all_chats(mconn, output_dir)
                print(f"\n  Újraindexelve: {count} chat az FTS5 keresőbe.")
                return
        finally:
            mconn.close()

    # Validacio: --from nem lehet kesobbi mint --to
    if from_ts and to_ts and from_ts > to_ts:
        print("[!] A --from dátum kesobbi mint a --to dátum.")
        sys.exit(1)

    # Kimeneti könyvtár létrehozása (csak ha nem list-only mód)
    if not list_only:
        output_dir.mkdir(parents=True, exist_ok=True)

    # ── Hitelesítés ──────────────────────────────────────────────────────

    print("=" * 60)
    print("  Gemini Chat Exporter")
    print("=" * 60)
    print(f"  Kimeneti könyvtár: {output_dir.resolve()}")
    print(f"  Formátumok:        {', '.join(formats)}")
    print(f"  Késleltetés:       {delay}s")
    print(f"  Resume:            {'igen' if resume else 'nem'}")
    print(f"  Auto-cookies:      {'igen' if auto_cookies else 'nem'}")
    print(f"  Max chats:         {max_chats}")
    print(f"  Párhuzamos let.:   {args.concurrency} szalon")
    if from_ts:
        print(f"  Dátum -tol:        {args.from_date}")
    if to_ts:
        print(f"  Dátum -ig:         {args.to_date}")
    if keyword:
        print(f"  Kulcsszó szűrés:   '{keyword}'")
    if list_only:
        print(f"  Lista mod:         igen (export nelkul)")
    print("=" * 60)

    # Client inicializálása
    if auto_cookies:
        print("\n[*] Cookie-k automatikus importalasa a böngészőbol...")
        try:
            client = GeminiClient()
        except Exception as e:
            print(f"[!] Nem sikerult a cookie-k automatikus importalasa: {e}")
            print("   Állítsd be manuálisan a GEMINI_SECURE_1PSID és GEMINI_SECURE_1PSIDTS")
            print("   változókat a .env fájlban, vagy futtasd --auto-cookies nélkül.")
            sys.exit(1)
    else:
        secure_1psid = config["secure_1psid"]
        secure_1psidts = config["secure_1psidts"]

        if not secure_1psid:
            env_path = Path(".env")
            env_example = Path(".env.example")
            print("\n[!] Hianyzo GEMINI_SECURE_1PSID kornyezeti valtozo!")
            if not env_path.exists() and env_example.exists():
                print(f"   Úgy tűnik, nincs .env fájl. Hozd létre a példa alapján:")
                print(f"   > cp .env.example .env")
                print(f"   Majd töltsd ki a cookie értékeket.")
            print("   Cookie-k kinyerése: https://gemini.google.com -> F12")
            print("   -> Application -> Cookies -> __Secure-1PSID")
            print("   Alternatíva: használd a --auto-cookies kapcsolót.")
            sys.exit(1)

        client = GeminiClient(secure_1psid, secure_1psidts)

    # Inicializálás
    print("\n[*] Kapcsolódás a Geminihez...")
    try:
        await client.init(timeout=30, auto_close=False, auto_refresh=True)
        print("[+] Sikeresen csatlakozva.")
    except Exception as e:
        print(f"[!] Sikertelen inicializálás: {e}")
        print("   Ellenőrizd a cookie-kat -- lehet, hogy lejártak vagy érvénytelenek.")
        sys.exit(1)

    # A gemini_webapi alapból csak 13 beszélgetést kér le (recent=13),
    # és a szerver ~100-as batch limitet használ.
    # Page token alapú paginációval lekérjük az ÖSSZES beszélgetést.
    print(f"[*] Összes beszélgetés lekerese paginacioval (max. {max_chats})...")
    try:
        all_chats = await _fetch_chats_paginated(client, max_chats)
        # Felülírjuk a client belső listáját a teljes listával
        client._recent_chats = all_chats
        print(f"[+] {len(all_chats)} beszélgetés betoltve (paginacio: OK).\n")
    except Exception as e:
        print(f"[!] Figyelmeztetés: a paginalt lekerdezes hibazott: {e}")
        # Fallback: próbáljuk a sima _fetch_recent_chats-et
        try:
            await client._fetch_recent_chats(recent=max_chats)
            chat_count = len(client._recent_chats) if client._recent_chats else 0
            print(f"[+] {chat_count} beszélgetés betoltve (fallback mod).\n")
        except Exception:
            print("   Az alap 13 beszélgetéssel folytatodik.\n")

    # ── Szűrés ─────────────────────────────────────────────────────────

    # Alkalmazzuk a dátum és kulcsszó szűrőket
    if from_ts or to_ts or keyword:
        chats_before = len(client._recent_chats) if client._recent_chats else 0
        filtered_chats, filter_stats = filter_chats(
            client._recent_chats or [],
            from_ts=from_ts, to_ts=to_ts, keyword=keyword,
        )
        client._recent_chats = filtered_chats
        print(f"[i] Szűrés: {filter_stats['filtered']} talalat / {filter_stats['total']} összesbol "
              f"(dátum: -{filter_stats['reason_date']}, kulcsszo: -{filter_stats['reason_keyword']})\n")

    # ── Lista mód (--list-chats) ────────────────────────────────────────

    if list_only:
        all_chats_list = client._recent_chats if client._recent_chats else []
        list_chats_only(all_chats_list)
        return

    # ── Exportálás ───────────────────────────────────────────────────────

    start_time = time.time()
    stats = await export_all_chats(client, output_dir, formats, delay, resume, args.concurrency, args.ai_analyze)
    elapsed = time.time() - start_time

    # ── Összesítés ───────────────────────────────────────────────────────

    print("\n" + "=" * 60)
    print("  EXPORT Kész")
    print("=" * 60)
    print(f"  Összes beszélgetés:  {stats['total']}")
    print(f"  Sikeresen exportált: {stats['exported']}")
    print(f"  Kihagyva (resume):   {stats['skipped']}")
    print(f"  Sikertelen:          {stats['failed']}")
    print(f"  Eltelt idő:          {elapsed:.1f} mp")
    if "total_messages" in stats:
        print(f"  Összes Üzenet:       {stats['total_messages']}")
    if "oldest_chat" in stats:
        print(f"  Legrégebbi chat:     {stats['oldest_chat']}")
        print(f"  Legújabb chat:       {stats['newest_chat']}")
    print(f"  Kimeneti könyvtár:   {output_dir.resolve()}")
    # Manifest statisztika
    try:
        manifest_conn = _init_manifest(output_dir)
        mstats = _manifest_get_stats(manifest_conn)
        manifest_conn.close()
        if mstats["total"] > 0:
            print(f"  Manifest:            {mstats['ok']} OK, {mstats['failed']} sikertelen")
    except Exception:
        pass
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
