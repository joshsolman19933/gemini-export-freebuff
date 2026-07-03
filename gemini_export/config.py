"""Konfiguráció kezelés: környezeti változók betöltése .env fájlból."""

import os

from dotenv import load_dotenv


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
