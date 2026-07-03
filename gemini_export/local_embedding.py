#!/usr/bin/env python3
"""
Local Embedding Engine — Offline embedding models via sentence-transformers
============================================================================
Teljesen offline embedding generálás lokális modellekkel (CPU/GPU).
Nincs szükség API kulcsra vagy internetkapcsolatra az első letöltés után.

Támogatott modellek:
    - all-MiniLM-L6-v2 (alapértelmezett, 384 dimenzió, ~80 MB, CPU-n is gyors)
    - all-mpnet-base-v2 (768 dimenzió, ~420 MB, jobb minőség)
    - multi-qa-MiniLM-L6-cos-v1 (RAG-optimalizált)
    - Bármilyen SentenceTransformer-kompatibilis modell

Környezeti változók:
    EMBEDDING_PROVIDER=local|openai|ollama|auto  (alap: auto)
    LOCAL_EMBEDDING_MODEL=all-MiniLM-L6-v2       (alap: all-MiniLM-L6-v2)
"""

from __future__ import annotations

import os
import threading
from typing import Any

from gemini_export.logging_config import get_logger

logger = get_logger(__name__)

# Cache for the singleton model instance
_model: Any = None
_model_lock = threading.Lock()
_model_name: str | None = None
_initialized: bool = False
_init_error: str | None = None


def _get_local_embedding_model() -> Any:
    """Get or create the local embedding model (singleton, thread-safe).

    On first call, downloads the model from Hugging Face Hub.
    Subsequent calls return the cached model.

    Returns:
        SentenceTransformer model instance, or None if unavailable.
    """
    global _model, _model_name, _initialized, _init_error

    if _initialized:
        return _model

    with _model_lock:
        if _initialized:
            return _model

        model_name = os.getenv("LOCAL_EMBEDDING_MODEL", "all-MiniLM-L6-v2")

        try:
            from sentence_transformers import SentenceTransformer

            logger.info(f"Local embedding model letöltése/betöltése: {model_name}")
            _model = SentenceTransformer(model_name)
            _model_name = model_name
            _initialized = True  # Only mark as initialized after successful load
            logger.info(
                f"Local embedding model kész: {model_name} "
                f"({_model.get_sentence_embedding_dimension()} dimenzió)"
            )
            return _model
        except ImportError:
            _init_error = (
                "A 'sentence-transformers' csomag szükséges a lokális embeddinghez. "
                "Telepítsd: pip install sentence-transformers"
            )
            logger.warning(_init_error)
            _initialized = True  # Mark as tried-and-failed
            return None
        except Exception as e:
            _init_error = f"Hiba a lokális embedding modell betöltésekor: {e}"
            logger.warning(_init_error)
            _initialized = True  # Mark as tried-and-failed
            return None


def local_embed(text: str, model: str | None = None) -> list[float] | None:
    """Generate embedding using a local sentence-transformers model.

    Args:
        text: The input text (max 8000 chars).
        model: Optional model name override (uses LOCAL_EMBEDDING_MODEL env var or default).

    Returns:
        List of floats (384 dims for all-MiniLM-L6-v2), or None on failure.
    """
    if not text or not text.strip():
        return None

    text = text.strip()[:8000]

    try:
        m = _get_local_embedding_model()
        if m is None:
            return None

        embedding = m.encode(text, normalize_embeddings=True)
        return embedding.tolist()
    except Exception as e:
        logger.warning(f"Local embedding hiba: {e}")
        return None


def is_local_embedding_available() -> bool:
    """Check if local embeddings are available without triggering a download.

    Returns:
        True if the local model is loaded and ready.
    """
    if _initialized:
        return _model is not None

    # Check if sentence-transformers is importable without downloading
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


def get_local_embedding_info() -> dict:
    """Return metadata about the local embedding engine.

    Returns:
        {"available": bool, "model": str|None, "dimension": int|None, "error": str|None}
    """
    if _initialized and _model is not None:
        try:
            dim = _model.get_sentence_embedding_dimension()
        except Exception:
            dim = None
        return {
            "available": True,
            "model": _model_name,
            "dimension": dim,
            "error": None,
        }

    if _init_error:
        return {
            "available": False,
            "model": None,
            "dimension": None,
            "error": _init_error,
        }

    # Not yet initialized
    try:
        import sentence_transformers  # noqa: F401
        return {
            "available": True,
            "model": os.getenv("LOCAL_EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
            "dimension": None,  # Will be known after first use
            "error": None,
        }
    except ImportError:
        return {
            "available": False,
            "model": None,
            "dimension": None,
            "error": (
                "A 'sentence-transformers' csomag nincs telepítve. "
                "Telepítsd: pip install sentence-transformers"
            ),
        }


def reset_local_embedding() -> None:
    """Reset the local embedding engine (useful for testing)."""
    global _model, _model_name, _initialized, _init_error
    with _model_lock:
        _model = None
        _model_name = None
        _initialized = False
        _init_error = None
