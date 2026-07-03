"""Export formázók: Markdown, JSON, HTML, CSV generálás.

Támogatja a Jinja2 template engine-t (dark/light/minimal/academic témák),
automatikus fallback-kel a hagyományos string-formázásra."""

import html as html_mod
import json
import re
from pathlib import Path

from gemini_export.logging_config import get_logger
from gemini_export.utils import format_timestamp, sanitize_filename

logger = get_logger(__name__)

_HIGHLIGHT_JS_CDN = (
    '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">'
    '<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>'
)

# Template engine (lazy init)
_template_engine = None
_engine_attempted = False


def _get_engine():
    """Lazy-initialize the Jinja2 template engine, returns None if unavailable."""
    global _template_engine, _engine_attempted
    if not _engine_attempted:
        _engine_attempted = True
        try:
            from gemini_export.template_engine import get_template_engine
            _template_engine = get_template_engine()
            if _template_engine.available:
                logger.debug("Jinja2 template engine loaded.")
        except Exception:
            pass
    return _template_engine if _template_engine and _template_engine.available else None


def _html_escape(text: str) -> str:
    """HTML escape + új sorok megőrzése <br>-rel."""
    escaped = html_mod.escape(text, quote=False)
    return escaped.replace("\n", "<br>")


def _render_text_with_code_blocks(text: str) -> str:
    """HTML escape + kódblokkok highlight.js-kompatibilis wrap-elése.

    A ```...``` jelölésű kódblokkokat <pre><code class=\"language-xxx\">-be csomagolja,
    a többi szöveget html escape-eli és <br>-rel tördeli.
    """
    if not text:
        return ""

    # Kódblokkok felismerése: ```nyelv\n...kód...\n```
    code_block_pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)

    parts = []
    last_end = 0

    for match in code_block_pattern.finditer(text):
        # A kódblokk előtti szöveg
        before = text[last_end:match.start()]
        if before:
            parts.append(_html_escape(before))

        # Kódblokk feldolgozása
        lang = match.group(1).strip() if match.group(1) else ""
        code = match.group(2)
        escaped_code = html_mod.escape(code, quote=False)
        lang_class = f' class="language-{lang}"' if lang else ""
        parts.append(f'<pre><code{lang_class}>{escaped_code}</code></pre>')

        last_end = match.end()

    # Maradék szöveg
    remaining = text[last_end:]
    if remaining:
        parts.append(_html_escape(remaining))

    return "".join(parts)


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
.text pre{border-radius:10px;margin:.5rem 0 1rem;overflow-x:auto}
.text pre code{font-family:'SF Mono','Fira Code','Cascadia Code',monospace;font-size:.82rem;line-height:1.5;background:0 0;padding:0}
.images{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.75rem}
.images img{max-width:100%;max-height:400px;border-radius:8px;border:1px solid var(--border);object-fit:contain}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
</style>"""

    # Fordított sorrendben vannak (újtól régi), megfordítjuk
    turns_html = []
    for turn in reversed(turns):
        role = turn.get("role", "unknown").upper()
        text = _render_text_with_code_blocks(turn.get("text", ""))
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

    highlight_cdn = f"{_HIGHLIGHT_JS_CDN}"
    if full_document:
        return f"""<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{html_mod.escape(title)}</title>
{highlight_cdn}
{css}
</head>
<body>
{body}
<script>hljs.highlightAll();<\\/script>
</body>
</html>"""
    else:
        return body


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


def export_chat_to_html(
    chat_data: dict,
    output_dir: Path,
    theme: str = "dark",
    custom_css: str | None = None,
) -> Path:
    """Egy beszélgetés HTML fájlba mentése.

    Args:
        chat_data: A chat adatai.
        output_dir: Kimeneti könyvtár.
        theme: Téma ('dark', 'light', 'minimal', 'academic').
        custom_css: Egyedi CSS string (opcionális).
    """
    title = chat_data.get("title", "Untitled")
    cid = chat_data.get("cid", "unknown")

    filename = f"{sanitize_filename(title)}_{cid[:8]}.html"
    filepath = output_dir / "html" / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # Próbáljuk a Jinja2 template engine-t először
    engine = _get_engine()
    if engine:
        try:
            content = engine.render_chat_html(
                chat_data, theme=theme, custom_css=custom_css
            )
            filepath.write_text(content, encoding="utf-8")
            return filepath
        except Exception as e:
            logger.debug("Template rendering failed, falling back to string format: %s", e)

    # Fallback: hagyományos string-formázás
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


def generate_all_chats_html(
    all_chats_data: list[dict],
    output_dir: Path,
    theme: str = "dark",
    custom_css: str | None = None,
) -> Path:
    """Összes beszélgetés egyetlen, onallo HTML fajlban - navigációval, keresssel.

    Args:
        all_chats_data: Chat adatok listája.
        output_dir: Kimeneti könyvtár.
        theme: Téma ('dark', 'light', 'minimal', 'academic').
        custom_css: Egyedi CSS string (opcionális).
    """
    filepath = output_dir / "all_chats.html"
    export_date = format_timestamp()[:10]

    # Próbáljuk a Jinja2 template engine-t először
    engine = _get_engine()
    if engine:
        try:
            html = engine.render_all_chats_html(
                all_chats_data, theme=theme, custom_css=custom_css,
                export_date=export_date,
            )
            filepath.write_text(html, encoding="utf-8")
            return filepath
        except Exception as e:
            logger.debug("Template rendering failed, falling back to string format: %s", e)

    # Fallback: hagyományos string-formázás (meglévő kód)
    nav_items = []
    chat_contents = []
    for i, chat in enumerate(all_chats_data):
        _cid = chat.get("cid", "")
        title = chat.get("title", "Untitled")
        safe_title = html_mod.escape(title)
        nav_items.append(
            f'<a href="#chat-{i}" class="nav-item" data-index="{i}">'
            f'{safe_title[:80]}</a>'
        )
        chat_html = _build_html_chat_content(chat, include_style=False, full_document=False)
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
.text pre{{border-radius:10px;margin:.5rem 0 1rem;overflow-x:auto}}
.text pre code{{font-family:'SF Mono','Fira Code','Cascadia Code',monospace;font-size:.82rem;line-height:1.5;background:0 0;padding:0}}
.images{{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.75rem}}
.images img{{max-width:100%;max-height:400px;border-radius:8px;border:1px solid var(--border);object-fit:contain}}
.stats{{background:var(--surface);border-radius:8px;padding:1rem;margin-bottom:1rem}}
.stats-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:.5rem}}
.stat-val{{font-size:1.2rem;font-weight:700}}
.stat-lbl{{font-size:.7rem;color:var(--text-dim);text-transform:uppercase}}
@media(max-width:768px){{.sidebar{{display:none}}.main{{margin-left:0;max-width:100%}}}}
</style>
{_HIGHLIGHT_JS_CDN}
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
hljs.highlightAll();
</script>
</body>
</html>"""

    filepath.write_text(html, encoding="utf-8")
    return filepath
