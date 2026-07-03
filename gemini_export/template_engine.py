"""Jinja2 template engine for HTML/Markdown chat export rendering.

Provides a TemplateEngine class that wraps Jinja2 with:
- Four built-in themes: dark (default), light, minimal, academic
- Custom CSS injection
- Fallback to string-based formatting if Jinja2/templates are unavailable
- Template discovery from templates/exports/ directory
"""

import html as html_mod
import logging
import re
from pathlib import Path
from typing import Any

try:
    from jinja2 import Environment, FileSystemLoader, TemplateNotFound
    JINJA2_AVAILABLE = True
except ImportError:
    JINJA2_AVAILABLE = False

logger = logging.getLogger(__name__)

BUILTIN_THEMES = ("dark", "light", "minimal", "academic")
DEFAULT_THEME = "dark"

_HIGHLIGHT_JS_CDN = (
    '<link rel="stylesheet"'
    ' href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">'
    '<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>'
)


class TemplateEngine:
    """Jinja2-based template engine for chat export rendering.

    Usage:
        engine = TemplateEngine(template_dir="templates")
        html = engine.render_chat_html(chat_data, theme="dark")
        html = engine.render_all_chats_html(all_chats, theme="light", custom_css="...")
    """

    def __init__(self, template_dir: Path | str | None = None):
        """Initialize the template engine.

        Args:
            template_dir: Root directory containing the 'exports/' subdirectory
                          with Jinja2 templates. Defaults to 'templates/' relative
                          to the project root.
        """
        self._available = JINJA2_AVAILABLE
        self._env: Environment | None = None

        if template_dir is None:
            # Default: look for 'templates/' relative to this file's package
            template_dir = Path(__file__).parent.parent / "templates"

        self._template_dir = Path(template_dir)

        if self._available:
            try:
                loader = FileSystemLoader(str(self._template_dir))
                self._env = Environment(
                    loader=loader,
                    autoescape=False,  # We handle escaping in macros/templates
                    trim_blocks=True,
                    lstrip_blocks=True,
                )
                self._env.globals["_HIGHLIGHT_JS_CDN"] = _HIGHLIGHT_JS_CDN
                # Custom filter: render text with code block wrapping
                self._env.filters["render_text"] = self._render_text_filter
                # Verify template existence
                try:
                    self._env.get_template("exports/chat.j2")
                except TemplateNotFound:
                    self._available = False
                    logger.debug(
                        "Jinja2 templates not found in %s/exports/ — "
                        "using string-based fallback.",
                        self._template_dir,
                    )
            except Exception as e:
                self._available = False
                logger.debug("Jinja2 init failed: %s — using string-based fallback.", e)

    @property
    def available(self) -> bool:
        """Whether the Jinja2 template engine is available."""
        return self._available and self._env is not None

    @property
    def template_dir(self) -> Path:
        return self._template_dir

    def _normalize_theme(self, theme: str | None) -> str:
        """Validate and normalize theme name."""
        if theme and theme in BUILTIN_THEMES:
            return theme
        return DEFAULT_THEME

    def _normalize_chat_data(self, chat_data: dict) -> dict:
        """Ensure chat_data has all required fields for templates."""
        data: dict[str, Any] = dict(chat_data)
        data.setdefault("title", "Untitled")
        data.setdefault("cid", "unknown")
        data.setdefault("exported_at", "")
        data.setdefault("turns", [])
        data.setdefault("turn_count", len(data["turns"]))
        return data

    def render_chat_html(
        self,
        chat_data: dict,
        theme: str = DEFAULT_THEME,
        custom_css: str | None = None,
    ) -> str:
        """Render a single chat as HTML using Jinja2 templates.

        Args:
            chat_data: Chat data dict with title, cid, turns, exported_at.
            theme: One of 'dark', 'light', 'minimal', 'academic'.
            custom_css: Optional custom CSS string to inject.

        Returns:
            HTML string.

        Raises:
            RuntimeError: If Jinja2 templates are not available.
        """
        if not self.available:
            raise RuntimeError("Jinja2 template engine is not available.")

        theme = self._normalize_theme(theme)
        data = self._normalize_chat_data(chat_data)

        template = self._env.get_template("exports/chat.j2")
        return template.render(
            chat_data=data,
            theme=theme,
            custom_css=custom_css,
        )

    def render_all_chats_html(
        self,
        all_chats: list[dict],
        theme: str = DEFAULT_THEME,
        custom_css: str | None = None,
        export_date: str = "",
    ) -> str:
        """Render all chats as a combined HTML with sidebar navigation.

        Args:
            all_chats: List of chat data dicts.
            theme: One of 'dark', 'light', 'minimal', 'academic'.
            custom_css: Optional custom CSS string to inject.
            export_date: Date string for the stats panel.

        Returns:
            HTML string.

        Raises:
            RuntimeError: If Jinja2 templates are not available.
        """
        if not self.available:
            raise RuntimeError("Jinja2 template engine is not available.")

        theme = self._normalize_theme(theme)
        chats = [self._normalize_chat_data(c) for c in all_chats]

        template = self._env.get_template("exports/all_chats.j2")
        return template.render(
            chats=chats,
            theme=theme,
            custom_css=custom_css,
            export_date=export_date,
        )

    def list_themes(self) -> tuple[str, ...]:
        """Return available theme names."""
        return BUILTIN_THEMES


    @staticmethod
    def _render_text_filter(text: str) -> str:
        """Jinja2 filter: HTML escape + code block wrapping for highlight.js.

        Detects ```...``` code blocks and wraps them in <pre><code>,
        the rest of the text is HTML-escaped with <br> line breaks.
        """
        if not text:
            return ""

        code_block_pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
        parts = []
        last_end = 0

        for match in code_block_pattern.finditer(text):
            before = text[last_end:match.start()]
            if before:
                escaped = html_mod.escape(before, quote=False)
                parts.append(escaped.replace("\n", "<br>"))

            lang = match.group(1).strip() if match.group(1) else ""
            code = match.group(2)
            escaped_code = html_mod.escape(code, quote=False)
            lang_class = f' class="language-{lang}"' if lang else ""
            parts.append(
                f"<pre><code{lang_class}>{escaped_code}</code></pre>"
            )
            last_end = match.end()

        remaining = text[last_end:]
        if remaining:
            escaped = html_mod.escape(remaining, quote=False)
            parts.append(escaped.replace("\n", "<br>"))

        return "".join(parts)


# ── Module-level singleton ────────────────────────────────────────────────

_engine: TemplateEngine | None = None


def get_template_engine(template_dir: Path | str | None = None) -> TemplateEngine:
    """Get or create the module-level template engine singleton."""
    global _engine
    if _engine is None:
        _engine = TemplateEngine(template_dir)
    return _engine


def reset_engine() -> None:
    """Reset the module-level singleton (useful for testing)."""
    global _engine
    _engine = None
