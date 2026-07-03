"""PDF export formázó: WeasyPrint alapú HTML→PDF konverzió.

Egyedi chat PDF + összesített archívum PDF generálás, beágyazott képekkel.
A WeasyPrint import lazy — csak akkor töltődik be, amikor ténylegesen PDF-et generálunk,
így hiányzó GTK függőségek esetén sem akadályozza a többi funkcionalitást.
"""

import html as html_mod
from pathlib import Path

from gemini_export.formatters import _build_html_chat_content
from gemini_export.logging_config import get_logger
from gemini_export.utils import format_timestamp, sanitize_filename

logger = get_logger(__name__)


def _get_weasyprint_html():
    """Lazy import: csak akkor tölti be a WeasyPrint-et, ha tényleg kell.

    Ezzel elkerüljük, hogy hiányzó GTK függőségek (libgobject-2.0-0)
    esetén a modul importja elszálljon — a többi funkció továbbra is működik.
    """
    try:
        from weasyprint import HTML  # noqa: F811

        return HTML
    except (ImportError, OSError) as e:
        logger.debug("WeasyPrint nem elérhető: %s", e)
        return None


# Print-optimized CSS: dark theme on screen, light on actual paper print
_PDF_CSS = """<style>
:root {
  --bg: #0f1117;
  --surface: #1a1d27;
  --border: #2e3345;
  --text: #e1e4ed;
  --text-dim: #8b8fa8;
  --accent: #6c8eff;
  --user-bg: #1a2740;
  --model-bg: #1a1d27;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Segoe UI', system-ui, sans-serif;
  background: var(--bg);
  color: var(--text);
  max-width: 800px;
  margin: 0 auto;
  padding: 1.5rem;
  line-height: 1.7;
}
h1 {
  font-size: 1.5rem;
  margin-bottom: .25rem;
  background: linear-gradient(135deg, var(--accent), #a78bfa);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  color: var(--accent);
}
h2 {
  font-size: 1.2rem;
  margin: 2rem 0 .5rem;
  color: var(--accent);
  border-bottom: 1px solid var(--border);
  padding-bottom: .25rem;
}
.meta {
  color: var(--text-dim);
  font-size: .8rem;
  margin-bottom: 2rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--border);
}
.turn {
  margin-bottom: 1.5rem;
  padding: 1rem;
  border-radius: 10px;
}
.turn-user {
  background: var(--user-bg);
  border-left: 3px solid var(--accent);
}
.turn-model {
  background: var(--model-bg);
  border-left: 3px solid #34d399;
}
.role {
  font-size: .75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .05em;
  margin-bottom: .5rem;
}
.role-user { color: var(--accent); }
.role-model { color: #34d399; }
.text {
  white-space: pre-wrap;
  word-break: break-word;
}
.text pre {
  background: #0d1117;
  border-radius: 10px;
  margin: .5rem 0 1rem;
  overflow-x: auto;
  padding: 1rem;
}
.text pre code {
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  font-size: .82rem;
  line-height: 1.5;
  background: none;
  padding: 0;
}
.images {
  display: flex;
  flex-wrap: wrap;
  gap: .5rem;
  margin-top: .75rem;
}
.images img {
  max-width: 100%;
  max-height: 400px;
  border-radius: 8px;
  border: 1px solid var(--border);
  object-fit: contain;
}
.page-break { page-break-after: always; }

/* Print: invert to light theme for actual paper printing */
@media print {
  :root {
    --bg: #ffffff;
    --surface: #f5f5f5;
    --border: #cccccc;
    --text: #1a1a1a;
    --text-dim: #666666;
    --accent: #2255cc;
    --user-bg: #e8f0fe;
    --model-bg: #f5f5f5;
  }
  body { max-width: 100%; padding: 0; }
  h1 {
    background: none;
    -webkit-text-fill-color: var(--accent);
    color: var(--accent);
  }
  .text pre { background: #f0f0f0; border: 1px solid #ddd; }
}
</style>"""


def _resolve_image_paths(chat_data: dict, output_dir: Path) -> dict:
    """Átalakítja a képek relatív elérési útjait abszolút `file://` URL-ekké.

    A WeasyPrint-nek abszolút elérési utak kellenek a helyi fájlokhoz.
    """
    resolved = dict(chat_data)
    turns = []
    for turn in chat_data.get("turns", []):
        t = dict(turn)
        images = []
        for img in turn.get("images", []):
            i = dict(img)
            path = i.get("downloaded_path", i.get("url", ""))
            if path and not path.startswith(
                ("http://", "https://", "file://", "data:")
            ):
                abs_path = (output_dir / path).resolve()
                if abs_path.exists():
                    i["downloaded_path"] = abs_path.as_uri()
                else:
                    i["downloaded_path"] = path
            images.append(i)
        t["images"] = images
        turns.append(t)
    resolved["turns"] = turns
    return resolved


def _build_pdf_chat_html(chat_data: dict) -> str:
    """Egy beszélgetés PDF-barát HTML tartalmának generálása.

    A formatters._build_html_chat_content()-ra épít, de kiegészíti
    print-optimalizált CSS-sel.
    """
    title = chat_data.get("title", "Untitled")
    body = _build_html_chat_content(
        chat_data, include_style=False, full_document=False
    )
    return f"""<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<title>{html_mod.escape(title)}</title>
{_PDF_CSS}
</head>
<body>
{body}
</body>
</html>"""


def _build_all_chats_pdf_html(all_chats_data: list[dict]) -> str:
    """Összes beszélgetés PDF-barát HTML tartalmának generálása.

    Minden chat külön oldalon, tartalomjegyzékkel.
    """
    total_chats = len(all_chats_data)
    total_turns = sum(c.get("turn_count", 0) for c in all_chats_data)

    # Tartalomjegyzék
    toc_items = []
    chat_sections = []
    for i, chat in enumerate(all_chats_data):
        title = chat.get("title", "Untitled")
        turn_count = len(chat.get("turns", []))

        safe_title = html_mod.escape(title)

        toc_items.append(
            f'<li><a href="#chat-{i}">{safe_title[:80]}</a> '
            f'<span class="toc-meta">({turn_count} üzenet)</span></li>'
        )

        # Chat body: reuse existing formatter (CSS nélkül)
        chat_body = _build_html_chat_content(
            chat, include_style=False, full_document=False
        )

        section = (
            f'<div class="chat-section" id="chat-{i}">'
            f'{chat_body}'
            f'</div>'
        )
        # Page break after each chat except the last
        if i < len(all_chats_data) - 1:
            section += '<div class="page-break"></div>'
        chat_sections.append(section)

    return f"""<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<title>Gemini Chat Archívum — Teljes Export</title>
{_PDF_CSS}
<style>
  .toc {{ background: var(--surface); border-radius: 10px; padding: 1.5rem;
          margin-bottom: 2rem; page-break-after: always; }}
  .toc h2 {{ margin-top: 0; }}
  .toc ol {{ padding-left: 1.5rem; }}
  .toc li {{ margin-bottom: .3rem; font-size: .9rem; }}
  .toc a {{ color: var(--accent); text-decoration: none; }}
  .toc-meta {{ color: var(--text-dim); font-size: .75rem; }}
  .chat-section {{ margin-bottom: 1rem; }}
  .cover {{ text-align: center; padding: 4rem 1rem; page-break-after: always; }}
  .cover h1 {{ font-size: 2rem; margin-bottom: 1rem; }}
  .cover .subtitle {{ color: var(--text-dim); font-size: 1rem; }}
  .stats-grid {{ display: grid; grid-template-columns: repeat(3, 1fr);
                 gap: 1rem; max-width: 400px; margin: 2rem auto; }}
  .stat-val {{ font-size: 1.5rem; font-weight: 700; }}
  .stat-lbl {{ font-size: .75rem; color: var(--text-dim);
               text-transform: uppercase; }}
</style>
</head>
<body>

<div class="cover">
  <h1>Gemini Chat Archívum</h1>
  <div class="subtitle">Teljes export — {format_timestamp()}</div>
  <div class="stats-grid">
    <div><div class="stat-val">{total_chats}</div>
         <div class="stat-lbl">Beszélgetés</div></div>
    <div><div class="stat-val">{total_turns}</div>
         <div class="stat-lbl">Üzenet</div></div>
    <div><div class="stat-val">{format_timestamp()[:10]}</div>
         <div class="stat-lbl">Dátum</div></div>
  </div>
</div>

<div class="toc">
  <h2>Tartalomjegyzék</h2>
  <ol>
    {"".join(toc_items)}
  </ol>
</div>

{"".join(chat_sections)}

</body>
</html>"""


# ─── Fő API ──────────────────────────────────────────────────────────────────


def export_chat_to_pdf(chat_data: dict, output_dir: Path) -> Path | None:
    """Egy beszélgetés PDF fájlba mentése.

    Args:
        chat_data: A chat adatai (cid, title, turns, exported_at, ...).
        output_dir: Az exports könyvtár.

    Returns:
        A létrehozott PDF fájl elérési útja, vagy None hiba esetén.
    """
    HTML = _get_weasyprint_html()
    if HTML is None:
        logger.warning(
            "A weasyprint csomag nincs telepítve vagy hiányoznak a "
            "GTK függőségei. Telepítés: pip install weasyprint. "
            "Windowson a GTK3 runtime is szükséges lehet."
        )
        return None

    title = chat_data.get("title", "Untitled")
    cid = chat_data.get("cid", "unknown")

    filename = f"{sanitize_filename(title)}_{cid[:8]}.pdf"
    pdf_dir = output_dir / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    filepath = pdf_dir / filename

    try:
        # Képek abszolút elérési utakra konvertálása
        resolved = _resolve_image_paths(chat_data, output_dir)

        # HTML generálás
        html_content = _build_pdf_chat_html(resolved)

        # PDF generálás WeasyPrint-tel
        HTML(string=html_content).write_pdf(str(filepath))

        logger.debug("PDF generálva: %s", filepath)
        return filepath
    except Exception as e:
        logger.error("PDF generálási hiba (%s): %s", title[:50], e)
        return None


def generate_all_chats_pdf(
    all_chats_data: list[dict], output_dir: Path
) -> Path | None:
    """Összes beszélgetés egyetlen PDF fájlba mentése.

    Args:
        all_chats_data: Az összes chat adatait tartalmazó lista.
        output_dir: Az exports könyvtár.

    Returns:
        A létrehozott PDF fájl elérési útja, vagy None hiba esetén.
    """
    HTML = _get_weasyprint_html()
    if HTML is None:
        logger.warning(
            "A weasyprint csomag nincs telepítve vagy hiányoznak a "
            "GTK függőségei. Telepítés: pip install weasyprint."
        )
        return None

    filepath = output_dir / "all_chats.pdf"

    try:
        # Képek abszolút elérési utakra konvertálása minden chat-ben
        resolved_chats = [
            _resolve_image_paths(chat, output_dir) for chat in all_chats_data
        ]

        # HTML generálás
        html_content = _build_all_chats_pdf_html(resolved_chats)

        # PDF generálás WeasyPrint-tel
        HTML(string=html_content).write_pdf(str(filepath))

        logger.info("Összesített PDF generálva: %s", filepath)
        return filepath
    except Exception as e:
        logger.error("Összesített PDF generálási hiba: %s", e)
        return None
