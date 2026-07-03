"""Cookie-k biztonságos tárolása keyring-gel.

Platform támogatás:
- Windows: Credential Manager
- macOS: Keychain
- Linux: Secret Service (D-Bus) vagy fallback plaintext

Használat:
    from gemini_export.cookie_store import get_cookies, set_cookies, delete_cookies

    # Olvasás (keyring → .env fallback)
    psid, psidts, source = get_cookies()

    # Tárolás keyringben
    set_cookies("your_psid", "your_psidts")

    # Törlés keyringből
    delete_cookies()
"""

import os

from gemini_export.logging_config import get_logger

logger = get_logger(__name__)

KEYRING_SERVICE = "gemini-export"
COOKIE_NAMES = {
    "secure_1psid": "GEMINI_SECURE_1PSID",
    "secure_1psidts": "GEMINI_SECURE_1PSIDTS",
}

# Próbáljuk a keyring importálását
try:
    import keyring  # type: ignore[import-untyped]
    KEYRING_AVAILABLE = True
except ImportError:
    keyring = None  # type: ignore[assignment]
    KEYRING_AVAILABLE = False


# ─── Keyring műveletek ──────────────────────────────────────────────────────


def _get_cookies_from_keyring() -> tuple[str, str]:
    """Cookie-k lekérése a keyringből. Ha nincs keyring, ("", "")-t ad vissza."""
    if not KEYRING_AVAILABLE:
        return "", ""
    try:
        psid = keyring.get_password(KEYRING_SERVICE, COOKIE_NAMES["secure_1psid"]) or ""
        psidts = keyring.get_password(KEYRING_SERVICE, COOKIE_NAMES["secure_1psidts"]) or ""
        return psid, psidts
    except Exception as e:
        logger.debug("Keyring hiba olvasáskor: %s", e)
        return "", ""


def set_cookies(secure_1psid: str, secure_1psidts: str) -> bool:
    """Cookie-k tárolása a keyringben.

    Args:
        secure_1psid: A GEMINI_SECURE_1PSID érték.
        secure_1psidts: A GEMINI_SECURE_1PSIDTS érték.

    Returns:
        True ha sikeres, False ha keyring nem elérhető vagy hiba történt.
    """
    if not KEYRING_AVAILABLE:
        logger.warning(
            "A keyring csomag nincs telepítve. "
            "A cookie-k csak a .env fájlban lesznek tárolva. "
            "Telepítés: pip install keyring"
        )
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, COOKIE_NAMES["secure_1psid"], secure_1psid)
        keyring.set_password(KEYRING_SERVICE, COOKIE_NAMES["secure_1psidts"], secure_1psidts)
        logger.info("Cookie-k eltárolva a keyringben (%s).", _get_keyring_backend_name())
        return True
    except Exception as e:
        logger.warning("Keyring hiba íráskor: %s. A cookie-k a .env-ben maradnak.", e)
        return False


def delete_cookies() -> bool:
    """Cookie-k törlése a keyringből.

    Returns:
        True ha sikeres (vagy nem volt mit törölni), False ha hiba történt.
    """
    if not KEYRING_AVAILABLE:
        return False
    try:
        for env_name in COOKIE_NAMES.values():
            try:
                keyring.delete_password(KEYRING_SERVICE, env_name)
            except keyring.errors.PasswordDeleteError:
                pass  # Már nem létezik
        logger.info("Cookie-k törölve a keyringből.")
        return True
    except Exception as e:
        logger.warning("Keyring hiba törléskor: %s", e)
        return False


def _get_keyring_backend_name() -> str:
    """Visszaadja a keyring backend nevét (pl. 'Windows Credential Manager')."""
    if not KEYRING_AVAILABLE or keyring is None:
        return "ismeretlen"
    try:
        backend = keyring.get_keyring()
        return type(backend).__name__
    except Exception:
        return "ismeretlen"


# ─── Fő API ──────────────────────────────────────────────────────────────────


def get_cookies() -> tuple[str, str, str]:
    """Cookie-k lekérése: először keyringből, majd .env fallback.

    Returns:
        (secure_1psid, secure_1psidts, source) ahol source lehet:
        - "keyring"  — a keyringből jött
        - "env"      — a .env-ből / környezeti változóból jött
        - "none"     — nincs beállítva
    """
    # 1. Keyring próbálkozás
    if KEYRING_AVAILABLE:
        psid, psidts = _get_cookies_from_keyring()
        if psid:
            return psid, psidts, "keyring"

    # 2. Környezeti változó fallback
    psid = os.getenv("GEMINI_SECURE_1PSID", "")
    psidts = os.getenv("GEMINI_SECURE_1PSIDTS", "")
    if psid:
        return psid, psidts, "env"

    return "", "", "none"
