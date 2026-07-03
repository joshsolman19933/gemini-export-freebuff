"""Kép letöltő segédfüggvény a Gemini chat exportáláshoz."""

import asyncio
import hashlib
from pathlib import Path

import aiohttp

from gemini_export.logging_config import get_logger
from gemini_export.utils import _extract_image_metadata, _guess_image_ext

logger = get_logger(__name__)


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
                logger.info("[%d/%d]   └─ %d kép letoltve", index, total, downloaded)

    return downloaded
