"""Fő exportálási logika: egyedi és tömeges chat export."""

import asyncio
import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from gemini_webapi import GeminiClient
from gemini_webapi.types import ChatInfo

from gemini_export.formatters import (
    export_chat_to_csv,
    export_chat_to_html,
    export_chat_to_json,
    export_chat_to_markdown,
    generate_all_chats_html,
)
from gemini_export.image_utils import _download_turn_images
from gemini_export.logging_config import get_logger
from gemini_export.manifest import (
    _init_manifest,
    _manifest_mark_exported,
    _manifest_mark_failed,
    _manifest_needs_export,
)
from gemini_export.pagination import _retry_read_chat
from gemini_export.pdf_formatter import export_chat_to_pdf, generate_all_chats_pdf
from gemini_export.search import _index_chat_for_search
from gemini_export.utils import (
    already_exported,
    extract_turn_data,
    format_timestamp,
    sanitize_filename,
)

logger = get_logger(__name__)

try:
    from ai_layer import (
        _ensure_ai_schema,
        _store_ai_results,
        extract_todos,
        generate_summary,
        suggest_tags,
    )
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False


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
    template_theme: str = "dark",
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
            logger.info("[%d/%d] %s... [~] (már exportálva)", index, total, title[:80])
        return {"status": "skipped", "cid": cid}

    # Fallback: fájl-alapú resume (ha nincs manifest vagy --no-resume nélkül fut)
    if resume and not manifest_conn and already_exported(cid, output_dir, formats):
        async with print_lock:
            logger.info("[%d/%d] %s... [~] (már exportálva)", index, total, title[:80])
        return {"status": "skipped", "cid": cid}

    async with sem:
        # Beszélgetés előzményeinek lekérése retry/backoff-fal
        try:
            history = await _retry_read_chat(client, cid)
        except Exception as e:
            async with print_lock:
                logger.error("[%d/%d] %s... [!] Hiba: %s", index, total, title[:80], e)
            if manifest_conn:
                _manifest_mark_failed(manifest_conn, cid, title, str(e))
            return {"status": "failed", "cid": cid}

    # Polite pause a semaphore-n KÍVÜL
    if delay > 0:
        await asyncio.sleep(delay)

    if not history:
        async with print_lock:
            logger.info("[%d/%d] %s... [~] (üres előzmény)", index, total, title[:80])
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
            export_chat_to_html(chat_data, output_dir, theme=template_theme)
        if "pdf" in formats:
            export_chat_to_pdf(chat_data, output_dir)
        if "csv" in formats and csv_writer is not None:
            async with csv_lock:
                export_chat_to_csv(csv_writer, chat_data)
    except Exception as e:
        async with print_lock:
            logger.error("[%d/%d] %s... [!] FÁJL Hiba: %s", index, total, title[:80], e)
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
        logger.info("[%d/%d] %s... [+] (%d üzenet)", index, total, title[:80], len(turns))

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
    template_theme: str = "dark",
) -> dict:
    """Párhuzamosan letolti es exportalja az összes beszélgetést asyncio.gather-rel."""

    logger.info("Beszélgetések listájának lekérése...")
    chats = list(client.list_chats()) if client.list_chats() else []

    if not chats:
        logger.warning("Nincsenek beszélgetések a fiókodban, vagy nem sikerült lekérni őket.")
        return {"total": 0, "exported": 0, "skipped": 0, "failed": 0, "chats": []}

    total_count = len(chats)
    logger.info("[+] %d beszélgetés található.", total_count)
    logger.info("Párhuzamos letöltés: %d szalon (asyncio.gather)", concurrency)

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
                template_theme,
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
                logger.error("Váratlan hiba egy task-ban: %s", result)
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
            logger.info("Összesitett JSON: %s", all_export_path)

        # Single-file HTML (összes chat egy fájlban)
        if "html" in formats and all_chats_data:
            all_html_path = generate_all_chats_html(all_chats_data, output_dir, theme=template_theme)
            logger.info("Összesitett HTML: %s", all_html_path)

        # Single-file PDF (összes chat egy PDF-ben)
        if "pdf" in formats and all_chats_data:
            all_pdf_path = generate_all_chats_pdf(all_chats_data, output_dir)
            if all_pdf_path:
                logger.info("Összesitett PDF: %s", all_pdf_path)

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
