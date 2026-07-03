#!/usr/bin/env python3
"""
Prompt Template Loader — Customizable AI prompt templates
==========================================================
Loads prompt templates from the `prompts/` directory, supports
variable substitution ({{title}}, {{lang}}, {{context}}, etc.),
and falls back to built-in defaults if no custom template exists.

Template files:
    prompts/summarize.txt  — AI összefoglaló prompt
    prompts/todos.txt      — Teendő/projektötlet kinyerő prompt
    prompts/tags.txt       — Címke javaslat prompt
    prompts/rag_query.txt  — RAG Q&A prompt (tartalmazza a {{context}} változót)

Változók:
    {{title}}   — A chat címe
    {{lang}}    — Nyelvi instrukció ("magyarul" vagy "in English")
    {{context}} — A RAG kontextus (csak rag_query esetén)
    {{turns}}   — A chat tartalma (csak advanced használatra)
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Az alapértelmezett prompt könyvtár (a projekt gyökérhez relatív)
_PROMPT_DIR = Path(os.getenv("PROMPTS_DIR", "prompts"))

# Cache a betöltött template-ekhez
_cache: dict[str, str | None] = {}


def _resolve_prompt_dir() -> Path:
    """Visszaadja a prompt könyvtár abszolút elérési útját."""
    # Ha a PROMPTS_DIR abszolút, használjuk
    p = Path(_PROMPT_DIR)
    if p.is_absolute():
        return p
    # Relatív a projekt gyökérhez (ahol az ai_layer.py van)
    # Keressük a gemini_export csomag szülőjét
    try:
        import gemini_export

        root = Path(gemini_export.__file__).parent.parent
        return root / _PROMPT_DIR
    except Exception:
        # Fallback: aktuális munkakönyvtár
        return Path.cwd() / _PROMPT_DIR


def _ensure_prompt_dir() -> Path:
    """Biztosítja, hogy a prompt könyvtár létezik."""
    d = _resolve_prompt_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_prompt(name: str) -> str | None:
    """Betölt egy prompt template-et fájlból, vagy visszaadja a beépített default-ot.

    Args:
        name: A template neve kiterjesztés nélkül (pl. "summarize", "todos", "tags", "rag_query").

    Returns:
        A template szövege változókkal, vagy None ha nincs elérhető template.
    """
    cache_key = name
    if cache_key in _cache:
        return _cache[cache_key]

    prompt_dir = _resolve_prompt_dir()
    file_path = prompt_dir / f"{name}.txt"

    if file_path.exists():
        try:
            content = file_path.read_text(encoding="utf-8").strip()
            _cache[cache_key] = content
            return content
        except Exception:
            pass

    # Fallback: beépített default
    default = _get_default_prompt(name)
    _cache[cache_key] = default
    return default


def _get_default_prompt(name: str) -> str | None:
    """Visszaadja a beépített default prompt-ot egy névhez."""
    defaults = {
        "summarize": (
            "You are a helpful assistant that summarizes conversations. "
            "Write a concise 2-3 sentence summary {{lang}}. "
            "Focus on the main topic, key decisions, and outcomes. "
            "Reply ONLY with the summary text, no prefixes or labels."
        ),
        "todos": (
            "You extract action items, todos, project ideas, decisions, and learnings "
            "from conversations. Reply ONLY with a JSON array of objects. "
            "Each object has: \"text\" (the item description), "
            "\"category\" (one of: \"todo\", \"project_idea\", \"decision\", \"learning\"). "
            "Write the items {{lang}}. "
            "If there are no items, reply with an empty array: []"
        ),
        "tags": (
            "You suggest relevant tags for conversations. "
            "Reply ONLY with a JSON array of 3-5 lowercase tags (strings). "
            "Tags should be short (1-3 words), descriptive, and useful for categorizing. "
            "Use lowercase English for tag names. "
            "Example: [\"python\", \"machine learning\", \"api design\"]"
        ),
        "rag_query": (
            "You are a helpful assistant answering questions based on the user's Gemini chat archives. "
            "Answer the question using ONLY the provided conversation excerpts as context. "
            "If the context doesn't contain enough information, say so honestly. "
            "Always cite which chat(s) your answer comes from by mentioning the chat title. "
            "Answer {{lang}}. Be concise and helpful.\n\n"
            "## Context (from user's Gemini chat archives):\n"
            "{{context}}"
        ),
        "compare": (
            "You are an expert at comparing and contrasting conversations. "
            "You will receive two separate Gemini conversations to compare.\n\n"
            "## Conversation A: {{title_a}}\n{{content_a}}\n\n"
            "## Conversation B: {{title_b}}\n{{content_b}}\n\n"
            "Analyze the two conversations and provide a thorough comparison. "
            "Focus on the requested perspective: {{perspective}}. "
            "Answer {{lang}}.\n\n"
            "Structure your response as follows:\n"
            "1. **Summary**: Brief 1-2 sentence summary of each conversation.\n"
            "2. **Comparison**: The detailed comparison from the requested perspective.\n"
            "3. **Key Takeaways**: 2-3 actionable insights from comparing these conversations.\n\n"
            "Be concise, specific, and cite specific examples from both conversations when relevant."
        ),
    }
    return defaults.get(name)


def render_prompt(name: str, variables: dict[str, str] | None = None) -> str | None:
    """Betölt és renderel egy prompt template-et a megadott változókkal.

    Args:
        name: A template neve (pl. "summarize").
        variables: Dict a változókhoz, pl. {"lang": "magyarul", "title": "Projekt ötletek"}.

    Returns:
        A renderelt prompt szöveg, vagy None ha a template nem található.
    """
    template = get_prompt(name)
    if template is None:
        return None

    if variables:
        return _substitute_variables(template, variables)

    return template


def _substitute_variables(template: str, variables: dict[str, str]) -> str:
    """Behelyettesíti a {{változó}} placeholdereket a template-ben."""
    result = template
    for key, value in variables.items():
        placeholder = f"{{{{{key}}}}}"
        if placeholder in result:
            result = result.replace(placeholder, value)
        # Also handle {{ key }} (with spaces)
        pattern = re.escape(f"{{{{ {key} }}}}")
        result = re.sub(pattern, value, result)
    return result


def list_prompts() -> list[dict]:
    """Visszaadja az összes elérhető prompt template metaadatát.

    Returns:
        Lista: [{"name": "...", "custom": bool, "content": "...", "variables": [...]}, ...]
    """
    prompt_dir = _resolve_prompt_dir()
    builtin_names = {"summarize", "todos", "tags", "rag_query", "compare"}
    results = []

    for name in sorted(builtin_names):
        file_path = prompt_dir / f"{name}.txt"
        custom = file_path.exists()
        content = get_prompt(name)
        variables = _extract_variables(content or "")

        results.append({
            "name": name,
            "custom": custom,
            "path": str(file_path) if custom else None,
            "content": content,
            "variables": variables,
        })

    return results


def _extract_variables(template: str) -> list[str]:
    """Kinyeri a {{változó}} neveket a template-ből."""
    matches = re.findall(r"\{\{\s*(\w+)\s*\}\}", template)
    return sorted(set(matches))


def save_prompt(name: str, content: str) -> bool:
    """Eltárol egy egyedi prompt template-et fájlba.

    Args:
        name: A template neve (a .txt automatikusan hozzáadva).
        content: A template tartalma.

    Returns:
        True ha sikeres, False hiba esetén.

    Raises:
        ValueError: Ha a név nem engedélyezett.
    """
    allowed = {"summarize", "todos", "tags", "rag_query", "compare"}
    if name not in allowed:
        raise ValueError(
            f"Ismeretlen prompt név: '{name}'. "
            f"Engedélyezett: {', '.join(sorted(allowed))}"
        )

    prompt_dir = _ensure_prompt_dir()
    file_path = prompt_dir / f"{name}.txt"

    try:
        file_path.write_text(content.strip(), encoding="utf-8")
        # Invalidate cache
        _cache.pop(name, None)
        return True
    except Exception:
        return False


def reset_prompt(name: str) -> bool:
    """Visszaállít egy prompt template-et az alapértelmezettre (törli az egyedi fájlt).

    Args:
        name: A template neve.

    Returns:
        True ha a reset sikeres (fájl törölve vagy nem is létezett).
    """
    allowed = {"summarize", "todos", "tags", "rag_query", "compare"}
    if name not in allowed:
        raise ValueError(f"Ismeretlen prompt név: '{name}'.")

    prompt_dir = _resolve_prompt_dir()
    file_path = prompt_dir / f"{name}.txt"

    if file_path.exists():
        try:
            file_path.unlink()
        except Exception:
            return False

    # Invalidate cache
    _cache.pop(name, None)
    return True


def clear_cache() -> None:
    """Törli a template cache-t (hasznos teszteléshez)."""
    _cache.clear()
