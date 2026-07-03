#!/usr/bin/env python3
"""
AI Provider Factory — Multi-provider AI abstraction layer
==========================================================
Factory pattern for AI providers: OpenAI, Anthropic, Google Gemini,
Groq, Together.ai, DeepSeek, Ollama, and any OpenAI-compatible API.

Supports chat completions (streaming + non-streaming), embeddings,
and health checks through a unified interface.

Környezeti változók:
    AI_PROVIDER=openai|anthropic|gemini|groq|together|deepseek|ollama
    OPENAI_API_KEY=sk-...        # API kulcs (OpenAI-kompatibilisekhez)
    OPENAI_BASE_URL=https://...   # API base URL (override)
    OPENAI_MODEL=...              # Modell név (override)
    OPENAI_EMBEDDING_MODEL=...    # Embedding modell (override)
    ANTHROPIC_API_KEY=sk-ant-...  # Anthropic API kulcs
    GEMINI_API_KEY=...            # Google Gemini API kulcs
"""

from __future__ import annotations

import os
import threading
from abc import ABC, abstractmethod
from collections.abc import Generator
from typing import Any

from gemini_export.logging_config import get_logger

logger = get_logger(__name__)

# ─── Provider configurations ────────────────────────────────────────────────

PROVIDER_CONFIGS: dict[str, dict[str, str | None]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "embedding_model": "text-embedding-3-small",
        "api_key_env": "OPENAI_API_KEY",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "embedding_model": None,  # Groq doesn't support embeddings
        "api_key_env": "OPENAI_API_KEY",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "embedding_model": "togethercomputer/m2-bert-80M-8k-retrieval",
        "api_key_env": "OPENAI_API_KEY",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "embedding_model": None,  # DeepSeek doesn't support embeddings
        "api_key_env": "OPENAI_API_KEY",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "llama3.2",
        "embedding_model": "nomic-embed-text",
        "api_key_env": None,  # Ollama doesn't require an API key
    },
    "anthropic": {
        "base_url": None,
        "model": "claude-3-5-sonnet-20241022",
        "embedding_model": None,
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "gemini": {
        "base_url": None,
        "model": "gemini-2.0-flash",
        "embedding_model": "models/text-embedding-004",
        "api_key_env": "GEMINI_API_KEY",
    },
}


def _is_local_endpoint(base_url: str) -> bool:
    """Ellenőrzi, hogy a konfigurált endpoint helyi-e (Ollama, LM Studio)."""
    return any(local in base_url for local in ("localhost", "127.0.0.1", "host.docker.internal"))


def _detect_provider_from_url(base_url: str) -> str | None:
    """Automatikus provider detektálás a base_url alapján."""
    lower = base_url.lower()
    if "ollama" in lower or ":11434" in lower:
        return "ollama"
    if "groq" in lower:
        return "groq"
    if "together" in lower:
        return "together"
    if "deepseek" in lower:
        return "deepseek"
    if "openai" in lower:
        return "openai"
    if "localhost" in lower or "127.0.0.1" in lower:
        if "1234" in lower:
            return "ollama"  # LM Studio uses 1234, treat as ollama-compatible
        return "ollama"
    if "anthropic" in lower:
        return "anthropic"
    if "googleapis" in lower or "generativelanguage" in lower:
        return "gemini"
    return None


# ─── Abstract base class ────────────────────────────────────────────────────


class AIProvider(ABC):
    """Abstract base for all AI providers."""

    name: str = "unknown"
    base_url: str | None = None
    model: str = ""
    embedding_model: str | None = None
    healthy: bool = False
    error: str | None = None

    @abstractmethod
    def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int = 500,
        temperature: float = 0.3,
        stream: bool = False,
    ) -> Any:
        """Non-streaming chat completion. Returns an object with .choices[0].message.content."""
        ...

    @abstractmethod
    def chat_completion_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int = 500,
        temperature: float = 0.5,
    ) -> Generator[str, None, None]:
        """Streaming chat completion. Yields content strings as they arrive."""
        ...

    @abstractmethod
    def embedding(self, model: str, text: str) -> list[float] | None:
        """Generate embedding for text. Returns list of floats or None on failure."""
        ...

    @abstractmethod
    def health_check(self) -> bool:
        """Check if the provider is reachable and healthy."""
        ...

    def get_model(self) -> str:
        """Return the current model name."""
        return self.model

    def get_embedding_model(self) -> str | None:
        """Return the embedding model name, or None if not supported."""
        return self.embedding_model

    def to_dict(self) -> dict:
        """Return provider metadata as a dict."""
        return {
            "provider": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "embedding_model": self.embedding_model,
            "healthy": self.healthy,
            "error": self.error,
        }


# ─── OpenAI-compatible provider ─────────────────────────────────────────────


class OpenAICompatibleProvider(AIProvider):
    """Provider for any OpenAI-compatible API (OpenAI, Groq, Together, DeepSeek, Ollama)."""

    def __init__(self, name: str, config: dict) -> None:
        self.name = name
        self.base_url = config.get("base_url", "")
        self.model = config.get("model", "gpt-4o-mini")
        self.embedding_model = config.get("embedding_model")
        self._api_key_env = config.get("api_key_env")

        # API key
        api_key = ""
        if self._api_key_env:
            api_key = os.getenv(self._api_key_env, "")
        if not api_key and _is_local_endpoint(self.base_url or ""):
            api_key = "ollama"  # Placeholder for local endpoints

        # Override base_url from env
        env_base = os.getenv("OPENAI_BASE_URL", "")
        if env_base:
            self.base_url = env_base

        # Override model from env
        env_model = os.getenv("OPENAI_MODEL", "")
        if env_model:
            self.model = env_model

        # Override embedding model from env
        env_emb = os.getenv("OPENAI_EMBEDDING_MODEL", "")
        if env_emb:
            self.embedding_model = env_emb

        # Create client
        try:
            from openai import OpenAI

            if not api_key and not _is_local_endpoint(self.base_url or ""):
                raise ValueError(
                    f"{self._api_key_env or 'API key'} környezeti változó nincs beállítva."
                )
            self._client = OpenAI(api_key=api_key or "ollama", base_url=self.base_url)
        except ImportError as err:
            raise ImportError(
                "Az 'openai' csomag szükséges az OpenAI-kompatibilis API-hoz. "
                "Telepítsd: pip install openai"
            ) from err

    def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int = 500,
        temperature: float = 0.3,
        stream: bool = False,
    ) -> Any:
        if stream:
            return self._client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )
        return self._client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=False,
        )

    def chat_completion_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int = 500,
        temperature: float = 0.5,
    ) -> Generator[str, None, None]:
        stream = self._client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def embedding(self, model: str, text: str) -> list[float] | None:
        if not self.embedding_model:
            return None
        try:
            response = self._client.embeddings.create(model=model, input=text)
            return response.data[0].embedding
        except Exception as e:
            logger.warning(f"Embedding hiba ({self.name}): {e}")
            return None

    def health_check(self) -> bool:
        try:
            if _is_local_endpoint(self.base_url or ""):
                self.healthy = True
            else:
                self._client.models.list(limit=1)
                self.healthy = True
        except Exception as e:
            self.healthy = False
            self.error = str(e)
        return self.healthy


# ─── Anthropic provider ─────────────────────────────────────────────────────


class AnthropicProvider(AIProvider):
    """Provider for Anthropic Claude API."""

    def __init__(self, config: dict) -> None:
        self.name = "anthropic"
        self.base_url = None
        self.model = config.get("model", "claude-3-5-sonnet-20241022")
        self.embedding_model = config.get("embedding_model")  # None, Anthropic has no embeddings

        env_model = os.getenv("OPENAI_MODEL", "")
        if env_model:
            self.model = env_model

        env_emb = os.getenv("OPENAI_EMBEDDING_MODEL", "")
        if env_emb:
            self.embedding_model = env_emb

        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY környezeti változó nincs beállítva. "
                "Állítsd be a .env fájlban: ANTHROPIC_API_KEY=sk-ant-..."
            )

        try:
            from anthropic import Anthropic

            self._client = Anthropic(api_key=api_key)
        except ImportError as err:
            raise ImportError(
                "Az 'anthropic' csomag szükséges a Claude API-hoz. "
                "Telepítsd: pip install anthropic"
            ) from err

    def _convert_messages(self, messages: list[dict[str, str]]) -> tuple[str | None, list[dict]]:
        """Convert OpenAI-style messages to Anthropic format.
        Returns (system_content, conversation_messages)."""
        system_content = None
        converted = []
        for msg in messages:
            if msg["role"] == "system":
                system_content = msg["content"]
            elif msg["role"] == "user":
                converted.append({"role": "user", "content": msg["content"]})
            elif msg["role"] == "assistant":
                converted.append({"role": "assistant", "content": msg["content"]})
            elif msg["role"] == "model":
                converted.append({"role": "assistant", "content": msg["content"]})
        return system_content, converted

    def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int = 500,
        temperature: float = 0.3,
        stream: bool = False,
    ) -> Any:
        system_content, converted = self._convert_messages(messages)
        kwargs = {
            "model": model,
            "messages": converted,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_content:
            kwargs["system"] = system_content

        if stream:
            return self._client.messages.stream(**kwargs)

        response = self._client.messages.create(**kwargs)
        # Wrap in OpenAI-compatible format
        return _AnthropicResponse(response)

    def chat_completion_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int = 500,
        temperature: float = 0.5,
    ) -> Generator[str, None, None]:
        system_content, converted = self._convert_messages(messages)
        kwargs = {
            "model": model,
            "messages": converted,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_content:
            kwargs["system"] = system_content

        with self._client.messages.stream(**kwargs) as stream:
            for event in stream:
                if event.type == "content_block_delta":
                    text = event.delta.text if hasattr(event.delta, "text") else ""
                    if text:
                        yield text
                elif event.type == "message_delta":
                    pass  # Final metadata

    def embedding(self, model: str, text: str) -> list[float] | None:
        logger.warning("Anthropic does not support embeddings. Use a different provider for RAG.")
        return None

    def health_check(self) -> bool:
        try:
            self._client.messages.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            self.healthy = True
        except Exception as e:
            self.healthy = False
            self.error = str(e)
        return self.healthy


class _AnthropicResponse:
    """Wraps an Anthropic response to be OpenAI-compatible."""

    def __init__(self, response: Any) -> None:
        self._response = response
        self.choices = [_AnthropicChoice(response)]


class _AnthropicChoice:
    def __init__(self, response: Any) -> None:
        content = response.content[0] if response.content else None
        self.message = _AnthropicMessage(content.text if content and hasattr(content, "text") else "")


class _AnthropicMessage:
    def __init__(self, text: str) -> None:
        self.content = text


# ─── Google Gemini provider ─────────────────────────────────────────────────


class GeminiProvider(AIProvider):
    """Provider for Google Gemini API."""

    def __init__(self, config: dict) -> None:
        self.name = "gemini"
        self.base_url = None
        self.model = config.get("model", "gemini-2.0-flash")
        self.embedding_model = config.get("embedding_model", "models/text-embedding-004")

        env_model = os.getenv("OPENAI_MODEL", "")
        if env_model:
            self.model = env_model

        env_emb = os.getenv("OPENAI_EMBEDDING_MODEL", "")
        if env_emb:
            self.embedding_model = env_emb

        api_key = os.getenv("GEMINI_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY környezeti változó nincs beállítva. "
                "Állítsd be a .env fájlban: GEMINI_API_KEY=..."
            )

        try:
            import google.generativeai as genai

            genai.configure(api_key=api_key)
            self._genai = genai
        except ImportError as err:
            raise ImportError(
                "A 'google-generativeai' csomag szükséges a Gemini API-hoz. "
                "Telepítsd: pip install google-generativeai"
            ) from err

    def _convert_messages(self, messages: list[dict[str, str]]) -> tuple[str | None, list[dict]]:
        """Convert to Gemini format. Returns (system_instruction, history)."""
        system_instruction = None
        history = []
        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            elif msg["role"] == "user":
                history.append({"role": "user", "parts": [msg["content"]]})
            elif msg["role"] in ("assistant", "model"):
                history.append({"role": "model", "parts": [msg["content"]]})
        return system_instruction, history

    def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int = 500,
        temperature: float = 0.3,
        stream: bool = False,
    ) -> Any:
        system_instruction, history = self._convert_messages(messages)

        # Remove system message from history (should be last user message with combined context)
        user_content = ""
        for msg in messages:
            if msg["role"] == "user":
                user_content = msg["content"]

        gen_config = self._genai.GenerationConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
        )

        model_instance = self._genai.GenerativeModel(
            model_name=model,
            system_instruction=system_instruction,
            generation_config=gen_config,
        )

        if not history:
            response = model_instance.generate_content(user_content)
        else:
            chat = model_instance.start_chat(history=history[:-1] if len(history) > 1 else [])
            response = chat.send_message(
                history[-1]["parts"][0] if history else user_content
            )

        if stream:
            return response  # Gemini returns iterable for streaming

        return _GeminiResponse(response)

    def chat_completion_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int = 500,
        temperature: float = 0.5,
    ) -> Generator[str, None, None]:
        system_instruction, history = self._convert_messages(messages)

        user_content = ""
        for msg in reversed(messages):
            if msg["role"] == "user":
                user_content = msg["content"]
                break

        gen_config = self._genai.GenerationConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
        )

        model_instance = self._genai.GenerativeModel(
            model_name=model,
            system_instruction=system_instruction,
            generation_config=gen_config,
        )

        if not history:
            response = model_instance.generate_content(user_content, stream=True)
        else:
            chat = model_instance.start_chat(history=history[:-1] if len(history) > 1 else [])
            response = chat.send_message(
                history[-1]["parts"][0] if history else user_content, stream=True
            )

        for chunk in response:
            if chunk.text:
                yield chunk.text

    def embedding(self, model: str, text: str) -> list[float] | None:
        if not self.embedding_model:
            return None
        try:
            result = self._genai.embed_content(
                model=model,
                content=text[:8000],
                task_type="retrieval_document",
            )
            return result.get("embedding", []) if result else None
        except Exception as e:
            logger.warning(f"Gemini embedding hiba: {e}")
            return None

    def health_check(self) -> bool:
        try:
            model_instance = self._genai.GenerativeModel(model_name=self.model)
            response = model_instance.generate_content("ping", generation_config={"max_output_tokens": 5})
            self.healthy = bool(response and response.text)
        except Exception as e:
            self.healthy = False
            self.error = str(e)
        return self.healthy


class _GeminiResponse:
    """Wraps a Gemini response to be OpenAI-compatible."""

    def __init__(self, response: Any) -> None:
        self._response = response
        text = response.text if hasattr(response, "text") else ""
        self.choices = [_GeminiChoice(text)]


class _GeminiChoice:
    def __init__(self, text: str) -> None:
        self.message = _GeminiMessage(text)


class _GeminiMessage:
    def __init__(self, text: str) -> None:
        self.content = text


# ─── Factory ─────────────────────────────────────────────────────────────────

_provider: AIProvider | None = None
_provider_lock = threading.Lock()


def get_provider(provider_name: str | None = None) -> AIProvider:
    """Get or create the AI provider instance (singleton, thread-safe).

    Provider selection priority:
    1. Explicit provider_name argument
    2. AI_PROVIDER env var
    3. Auto-detect from OPENAI_BASE_URL
    4. Default to "openai"

    Args:
        provider_name: Optional explicit provider name.

    Returns:
        AIProvider instance.

    Raises:
        ValueError: If the provider cannot be initialized.
    """
    global _provider

    # Fast path: already initialized and no explicit override
    if _provider is not None and provider_name is None:
        return _provider

    with _provider_lock:
        # Double-check inside lock
        if _provider is not None and provider_name is None:
            return _provider

    # Determine provider name
    if provider_name is None:
        provider_name = os.getenv("AI_PROVIDER", "")

    if not provider_name:
        # Auto-detect from base URL
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        provider_name = _detect_provider_from_url(base_url) or "openai"

    provider_name = provider_name.lower().strip()

    config = PROVIDER_CONFIGS.get(provider_name)
    if not config:
        raise ValueError(
            f"Ismeretlen AI provider: '{provider_name}'. "
            f"Támogatott: {', '.join(sorted(PROVIDER_CONFIGS))}"
        )

    # Create provider instance
    if provider_name == "anthropic":
        _provider = AnthropicProvider(config)
    elif provider_name == "gemini":
        _provider = GeminiProvider(config)
    else:
        # All OpenAI-compatible providers (openai, groq, together, deepseek, ollama)
        _provider = OpenAICompatibleProvider(provider_name, config)

    # Run health check
    try:
        _provider.health_check()
    except Exception as e:
        logger.debug(f"Provider health check failed: {e}")

    return _provider


def reset_provider() -> None:
    """Reset the cached provider instance (useful for testing)."""
    global _provider
    _provider = None


def detect_provider_info() -> dict:
    """Detect current AI provider configuration without creating a client.

    Returns metadata about what provider would be used.
    """
    provider_name = os.getenv("AI_PROVIDER", "")
    if not provider_name:
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        provider_name = _detect_provider_from_url(base_url) or "openai"

    config = PROVIDER_CONFIGS.get(provider_name, {})
    return {
        "provider": provider_name,
        "base_url": config.get("base_url", os.getenv("OPENAI_BASE_URL", "")),
        "model": os.getenv("OPENAI_MODEL", "") or config.get("model", ""),
        "embedding_model": os.getenv("OPENAI_EMBEDDING_MODEL", "") or config.get("embedding_model", ""),
        "api_key_env": config.get("api_key_env"),
    }
