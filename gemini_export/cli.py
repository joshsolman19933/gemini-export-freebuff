"""CLI parancssori feldolgozás és fő belépési pont."""

import argparse
import json
import sys
import time
from pathlib import Path

from gemini_webapi import GeminiClient

from gemini_export.config import load_config
from gemini_export.cookie_store import (
    KEYRING_AVAILABLE,
    _get_keyring_backend_name,
    delete_cookies,
    get_cookies,
    set_cookies,
)
from gemini_export.exporter import export_all_chats
from gemini_export.logging_config import get_logger
from gemini_export.manifest import _init_manifest, _manifest_get_stats
from gemini_export.pagination import _fetch_chats_paginated
from gemini_export.search import (
    _add_tags,
    _browse_chats,
    _list_tags,
    _reindex_all_chats,
    _search_chats,
)
from gemini_export.utils import filter_chats, list_chats_only, parse_date

logger = get_logger(__name__)

try:
    from ai_layer import batch_analyze_all
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False


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
        choices=["json", "markdown", "html", "csv", "pdf", "both", "all"],
        default="both",
        help="Export formátuma (alapértelmezett: both = json+markdown,"
             " all = json+markdown+html+csv+pdf)",
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
    parser.add_argument(
        "--ai-analyze-all",
        action="store_true",
        default=False,
        help="Batch AI elemzés: az összes már exportált, még nem elemzett"
             " chat elemzése (API kapcsolat nélkül is működik,"
             " csak a manifest DB-t használja).",
    )
    parser.add_argument(
        "--template",
        choices=["dark", "light", "minimal", "academic"],
        default="dark",
        help="HTML export témája (csak --format html|all esetén). "
             "dark=alap sötét, light=világos, minimal=letisztult, "
             "academic=akadémiai serif (alap: dark)",
    )
    parser.add_argument(
        "--store-cookies",
        nargs=2,
        metavar=("SECURE_1PSID", "SECURE_1PSIDTS"),
        default=None,
        help="Cookie-k biztonságos tárolása a rendszer kulcstartóban (keyring).",
    )
    parser.add_argument(
        "--clear-cookies",
        action="store_true",
        default=False,
        help="Cookie-k törlése a rendszer kulcstartóból.",
    )
    parser.add_argument(
        "--list-cookies",
        action="store_true",
        default=False,
        help="Cookie-k tárolási helyének megjelenítése.",
    )
    return parser.parse_args()


async def main():
    args = parse_args()
    config = load_config()

    # ── Keyring cookie management (nem igényel API kapcsolatot) ───────

    if args.store_cookies:
        psid, psidts = args.store_cookies
        if set_cookies(psid, psidts):
            logger.info("Cookie-k sikeresen eltárolva a keyringben.")
        else:
            logger.warning("A cookie-k mentése a keyringbe sikertelen. "
                           "A .env fájlban továbbra is elérhetőek.")
        return

    if args.clear_cookies:
        if delete_cookies():
            logger.info("Cookie-k törölve a keyringből.")
        else:
            logger.warning("A keyring nem elérhető — nincs mit törölni.")
        return

    if args.list_cookies:
        _psid, _psidts, source = get_cookies()
        if source == "keyring":
            logger.info("Cookie-k forrása: 🔐 Rendszer kulcstartó (keyring)")
            logger.info("Backend: %s", _get_keyring_backend_name())
            logger.info("GEMINI_SECURE_1PSID:  %s", _psid[:20] + "..." if _psid else "(nincs beállítva)")
            logger.info("GEMINI_SECURE_1PSIDTS: %s", _psidts[:20] + "..." if _psidts else "(nincs beállítva)")
        elif source == "env":
            logger.info("Cookie-k forrása: ⚠️  Környezeti változó (.env fájl) — plaintext!")
            logger.info("Javasolt a --store-cookies használata a biztonságos tároláshoz.")
            logger.info("GEMINI_SECURE_1PSID:  %s", _psid[:20] + "..." if _psid else "(nincs beállítva)")
        else:
            logger.info("Cookie-k forrása: ❌ Nincsenek beállítva (se keyring, se .env)")
        return

    # Konfiguráció összeállítása (CLI > env > default)
    output_dir = Path(args.output or config["output_dir"])
    delay = args.delay if args.delay is not None else config["delay"]
    auto_cookies = args.auto_cookies if args.auto_cookies is not None else config["auto_cookies"]
    resume = not args.no_resume

    if args.format == "both":
        formats = ["json", "markdown"]
    elif args.format == "all":
        formats = ["json", "markdown", "html", "csv", "pdf"]
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
            logger.error("%s", e)
            sys.exit(1)
    if args.to_date:
        try:
            to_ts = parse_date(args.to_date)
            if "T" not in args.to_date and " " not in args.to_date:
                to_ts += 86399  # +23:59:59
        except ValueError as e:
            logger.error("%s", e)
            sys.exit(1)
    keyword = args.keyword_filter
    list_only = args.list_chats

    # ── Új tudástár parancsok (nem igényelnek API kapcsolatot) ─────────

    if args.search or args.list_tags or args.browse or args.tag or args.reindex or args.ai_analyze_all:
        output_dir.mkdir(parents=True, exist_ok=True)
        mconn = _init_manifest(output_dir)
        try:
            if args.search:
                results = _search_chats(mconn, args.search)
                logger.info("Találatok: '%s' → %d chat", args.search, len(results))
                for r in results:
                    fav = "⭐ " if r["is_favorite"] else "  "
                    tags = ", ".join(r["tags"]) if r["tags"] else ""
                    logger.info("%-3s [%s] %-65s (%d üzenet)", fav, r['cid'][:8], r['title'][:65], r['message_count'])
                    if tags:
                        logger.info("       Címkék: %s", tags)
                return
            if args.list_tags:
                tags = _list_tags(mconn)
                logger.info("Címkék (%d): %s", len(tags), ', '.join(tags) if tags else 'nincsenek')
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
                logger.info("Újraindexelve: %d chat az FTS5 keresőbe.", count)
                return
            if args.ai_analyze_all:
                if not AI_AVAILABLE:
                    logger.error(
                        "Az AI réteg nem elérhető. "
                        "Telepítsd az openai csomagot és állítsd be az OPENAI_API_KEY-t."
                    )
                    return
                logger.info("🧠 Batch AI elemzés indítása...")
                logger.info("Kimeneti könyvtár: %s", output_dir.resolve())
                analyzed = 0
                failed = 0
                skipped = 0
                total = 0
                for event in batch_analyze_all(mconn, output_dir):
                    event_type = ""
                    event_data = ""
                    for line in event.split("\n"):
                        if line.startswith("event: "):
                            event_type = line[7:].strip()
                        elif line.startswith("data: "):
                            event_data += line[6:]
                    if not event_data:
                        continue
                    try:
                        data = json.loads(event_data)
                    except json.JSONDecodeError:
                        continue

                    if event_type == "start":
                        total = data.get("total", 0)
                        logger.info("%d elemzetlen chat / %d összesen", data.get('unanalyzed', 0), total)
                    elif event_type == "progress":
                        current = data.get("current", 0)
                        pct = round((current / max(total, 1)) * 100)
                        title = (data.get("title", "") or "")[:70]
                        status = data.get("status", "")
                        icon = {"analyzing": "⏳", "done": "✅", "skipped": "⏭️", "failed": "❌"}.get(status, "•")
                        bar_len = 30
                        filled = int(bar_len * current / max(total, 1))
                        bar = "█" * filled + "░" * (bar_len - filled)
                        print(f"\r  [{bar}] {pct:3d}% {icon} {title}", end="", flush=True)
                        if status == "done":
                            analyzed = data.get("analyzed", analyzed)
                        if status == "failed":
                            failed = data.get("failed", failed)
                            err = data.get("error", "")
                            if err:
                                # Új sor + logger figyelmeztetés (progress bar után)
                                print()  # clear the \r progress bar line
                                logger.warning("⚠️  %s", err)
                    elif event_type == "result":
                        tags = data.get("tags")
                        if tags:
                            logger.info("🏷️  %s", ', '.join(tags[:5]))
                    elif event_type == "done":
                        analyzed = data.get("analyzed", 0)
                        failed = data.get("failed", 0)
                        skipped = data.get("skipped", 0)
                    elif event_type == "error":
                        logger.error("❌ %s", event_data)

                logger.info("%s", "=" * 50)
                logger.info("🎉 Batch elemzés kész!")
                logger.info("✅ Elemzett: %d", analyzed)
                logger.info("⏭️ Kihagyva: %d", skipped)
                logger.info("❌ Sikertelen: %d", failed)
                logger.info("%s", "=" * 50)
                return
        finally:
            mconn.close()

    if from_ts and to_ts and from_ts > to_ts:
        logger.error("A --from dátum kesobbi mint a --to dátum.")
        sys.exit(1)

    if not list_only:
        output_dir.mkdir(parents=True, exist_ok=True)

    # ── Hitelesítés ──────────────────────────────────────────────────────

    logger.info("%s", "=" * 50)
    logger.info("Gemini Chat Exporter")
    logger.info("%s", "=" * 50)
    logger.info("Kimeneti könyvtár: %s", output_dir.resolve())
    logger.info("Formátumok:        %s", ', '.join(formats))
    logger.info("Késleltetés:       %ss", delay)
    logger.info("Resume:            %s", 'igen' if resume else 'nem')
    logger.info("Auto-cookies:      %s", 'igen' if auto_cookies else 'nem')
    logger.info("Max chats:         %d", max_chats)
    logger.info("Párhuzamos let.:   %d szalon", args.concurrency)
    if "html" in formats:
        logger.info("HTML téma:         %s", args.template)
    if from_ts:
        logger.info("Dátum -tol:        %s", args.from_date)
    if to_ts:
        logger.info("Dátum -ig:         %s", args.to_date)
    if keyword:
        logger.info("Kulcsszó szűrés:   '%s'", keyword)
    if list_only:
        logger.info("Lista mod:         igen (export nelkul)")
    logger.info("%s", "=" * 50)

    # Client inicializálása
    if auto_cookies:
        logger.info("Cookie-k automatikus importalasa a böngészőbol...")
        try:
            client = GeminiClient()
        except Exception as e:
            logger.error("Nem sikerult a cookie-k automatikus importalasa: %s", e)
            logger.error("Állítsd be manuálisan a GEMINI_SECURE_1PSID és GEMINI_SECURE_1PSIDTS")
            logger.error("változókat a .env fájlban, vagy futtasd --auto-cookies nélkül.")
            sys.exit(1)
    else:
        # Cookie-k lekérése: keyring → .env fallback
        secure_1psid, secure_1psidts, cookie_source = get_cookies()

        if not secure_1psid:
            env_path = Path(".env")
            env_example = Path(".env.example")
            logger.error("Hianyzo GEMINI_SECURE_1PSID kornyezeti valtozo!")
            if KEYRING_AVAILABLE:
                logger.error("A keyring elérhető, de nem tartalmaz cookie-kat.")
                logger.error(
                    "Tárold el a cookie-kat: "
                    "python export.py --store-cookies <PSID> <PSIDTS>"
                )
            if not env_path.exists() and env_example.exists():
                logger.error("Úgy tűnik, nincs .env fájl. Hozd létre a példa alapján:")
                logger.error("> cp .env.example .env")
                logger.error("Majd töltsd ki a cookie értékeket vagy használd a --store-cookies-t.")
            else:
                logger.error("Cookie-k kinyerése: https://gemini.google.com -> F12")
                logger.error("-> Application -> Cookies -> __Secure-1PSID")
                logger.error("Alternatíva: használd a --auto-cookies kapcsolót.")
            sys.exit(1)

        # Figyelmeztetés plaintext .env használat esetén
        if cookie_source == "env":
            logger.warning(
                "A cookie-k jelenleg a .env fájlban vannak tárolva (plaintext)."
            )
            if KEYRING_AVAILABLE:
                logger.warning(
                    "Javasolt a biztonságos keyring használata: "
                    "python export.py --store-cookies <PSID> <PSIDTS>"
                )
            else:
                logger.warning(
                    "Telepítsd a keyring csomagot a biztonságos tároláshoz: pip install keyring"
                )

        client = GeminiClient(secure_1psid, secure_1psidts)

    # Inicializálás
    logger.info("Kapcsolódás a Geminihez...")
    try:
        await client.init(timeout=30, auto_close=False, auto_refresh=True)
        logger.info("[+] Sikeresen csatlakozva.")
    except Exception as e:
        logger.error("Sikertelen inicializálás: %s", e)
        logger.error("Ellenőrizd a cookie-kat -- lehet, hogy lejártak vagy érvénytelenek.")
        sys.exit(1)

    logger.info("Összes beszélgetés lekerese paginacioval (max. %d)...", max_chats)
    try:
        all_chats = await _fetch_chats_paginated(client, max_chats)
        client._recent_chats = all_chats
        logger.info("[+] %d beszélgetés betoltve (paginacio: OK).", len(all_chats))
    except Exception as e:
        logger.warning("A paginalt lekerdezes hibazott: %s", e)
        try:
            await client._fetch_recent_chats(recent=max_chats)
            chat_count = len(client._recent_chats) if client._recent_chats else 0
            logger.info("[+] %d beszélgetés betoltve (fallback mod).", chat_count)
        except Exception:
            logger.warning("Az alap 13 beszélgetéssel folytatodik.")

    # ── Szűrés ─────────────────────────────────────────────────────────

    if from_ts or to_ts or keyword:
        filtered_chats, filter_stats = filter_chats(
            client._recent_chats or [],
            from_ts=from_ts, to_ts=to_ts, keyword=keyword,
        )
        client._recent_chats = filtered_chats
        logger.info(
            "Szűrés: %d talalat / %d összesbol (dátum: -%d, kulcsszo: -%d)",
            filter_stats['filtered'], filter_stats['total'],
            filter_stats['reason_date'], filter_stats['reason_keyword'],
        )

    # ── Lista mód ────────────────────────────────────────────────────────

    if list_only:
        all_chats_list = client._recent_chats if client._recent_chats else []
        list_chats_only(all_chats_list)
        return

    # ── Exportálás ───────────────────────────────────────────────────────

    start_time = time.time()
    stats = await export_all_chats(client, output_dir, formats, delay, resume, args.concurrency, args.ai_analyze, args.template)
    elapsed = time.time() - start_time

    # ── Összesítés ───────────────────────────────────────────────────────

    logger.info("%s", "=" * 50)
    logger.info("EXPORT Kész")
    logger.info("%s", "=" * 50)
    logger.info("Összes beszélgetés:  %d", stats['total'])
    logger.info("Sikeresen exportált: %d", stats['exported'])
    logger.info("Kihagyva (resume):   %d", stats['skipped'])
    logger.info("Sikertelen:          %d", stats['failed'])
    logger.info("Eltelt idő:          %.1f mp", elapsed)
    if "total_messages" in stats:
        logger.info("Összes Üzenet:       %d", stats['total_messages'])
    if "oldest_chat" in stats:
        logger.info("Legrégebbi chat:     %s", stats['oldest_chat'])
        logger.info("Legújabb chat:       %s", stats['newest_chat'])
    logger.info("Kimeneti könyvtár:   %s", output_dir.resolve())
    try:
        manifest_conn = _init_manifest(output_dir)
        mstats = _manifest_get_stats(manifest_conn)
        manifest_conn.close()
        if mstats["total"] > 0:
            logger.info("Manifest:            %d OK, %d sikertelen", mstats['ok'], mstats['failed'])
    except Exception:
        pass
    logger.info("%s", "=" * 50)
