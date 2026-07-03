#!/usr/bin/env python3
"""
Gemini Chat Exporter — Telepítő és Konfigurációs Varázsló
==========================================================
Interaktív varázsló a projekt első beállításához.

Futtatás:
    python setup.py

Ez a varázsló:
1. Ellenőrzi a Python verziót és a függőségeket
2. Létrehozza a .env fájlt (interaktív módon)
3. Teszteli a Gemini API kapcsolatot
4. Beállítja a preferenciákat (AI, formátumok, könyvtárak)
5. Opcionálisan létrehoz egy asztali parancsikont (Windows esetén)

Használat:
    python setup.py              # Interaktív varázsló
    python setup.py --quick      # Gyors telepítés (alapértelmezett értékekkel)
    python setup.py --check      # Csak függőségek ellenőrzése
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

# ─── Színek terminalhoz ──────────────────────────────────────────────────────

class Colors:
    """ANSI színkódok terminálhoz."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"

    @staticmethod
    def disable():
        for attr in dir(Colors):
            if not attr.startswith("_") and attr != "disable":
                setattr(Colors, attr, "")


def cprint(text: str, color: str = "", bold: bool = False) -> None:
    """Színes print."""
    prefix = ""
    if bold:
        prefix += Colors.BOLD
    prefix += color
    print(f"{prefix}{text}{Colors.RESET}")


# ─── Függőség ellenőrzés ─────────────────────────────────────────────────────

def check_python_version() -> bool:
    """Ellenőrzi, hogy a Python verzió legalább 3.10."""
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 10):
        cprint(f"  ✓ Python {major}.{minor} OK", Colors.GREEN)
        return True
    else:
        cprint(f"  ✗ Python {major}.{minor} — legalább 3.10 szükséges!", Colors.RED)
        return False


def check_package(package: str, import_name: str | None = None) -> bool:
    """Ellenőrzi, hogy egy csomag telepítve van-e."""
    import_name = import_name or package.replace("-", "_")
    try:
        __import__(import_name)
        cprint(f"  ✓ {package} OK", Colors.GREEN)
        return True
    except ImportError:
        cprint(f"  ✗ {package} nincs telepítve", Colors.RED)
        return False


def install_requirements() -> bool:
    """Telepíti a requirements.txt-ben lévő csomagokat."""
    req_path = Path(__file__).parent / "requirements.txt"
    if not req_path.exists():
        cprint("  ✗ requirements.txt nem található", Colors.RED)
        return False

    cprint("\n  Csomagok telepítése...", Colors.CYAN)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req_path)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            cprint("  ✓ Telepítés kész", Colors.GREEN)
            return True
        else:
            cprint("  ✗ Telepítés sikertelen", Colors.RED)
            if result.stderr:
                # Utolsó 3 sor a hibából
                lines = result.stderr.strip().split("\n")
                for line in lines[-3:]:
                    cprint(f"    {line}", Colors.DIM)
            return False
    except Exception as e:
        cprint(f"  ✗ Telepítés sikertelen: {e}", Colors.RED)
        return False


def check_dependencies() -> bool:
    """Összes függőség ellenőrzése."""
    cprint("\n📦 Függőségek ellenőrzése:", Colors.BOLD)
    all_ok = check_python_version()

    packages = [
        ("gemini-webapi", "gemini_webapi"),
        ("python-dotenv", "dotenv"),
        ("browser-cookie3", "browser_cookie3"),
        ("flask", "flask"),
        ("orjson", "orjson"),
        ("aiohttp", "aiohttp"),
        ("openai", "openai"),
        ("pytest", "pytest"),
    ]

    all_installed = all(check_package(pkg, imp) for pkg, imp in packages)

    if not all_installed:
        cprint("\n  Hiányzó csomagok! Telepítés? [i/n]", Colors.YELLOW)
        if input("  > ").strip().lower() in ("i", "y", "yes"):
            all_ok = install_requirements() and all_ok
        else:
            all_ok = False

    return all_ok and all_installed


# ─── .env létrehozása ──────────────────────────────────────────────────────

def create_env_file(quick: bool = False) -> bool:
    """Interaktívan létrehozza a .env fájlt."""
    env_path = Path(__file__).parent / ".env"
    example_path = Path(__file__).parent / ".env.example"

    if env_path.exists() and not quick:
        cprint(f"\n  A .env fájl már létezik: {env_path}", Colors.YELLOW)
        overwrite = input("  Felülírod? [i/n]: ").strip().lower()
        if overwrite not in ("i", "y", "yes"):
            cprint("  Meglévő .env megtartva.", Colors.CYAN)
            return True

    cprint("\n🔑 Cookie-k beállítása:", Colors.BOLD)
    cprint("  (gemini.google.com → F12 → Application → Cookies)", Colors.DIM)

    if quick:
        secure_1psid = ""
        secure_1psidts = ""
    else:
        cprint("\n  Add meg a GEMINI_SECURE_1PSID értéket:", Colors.CYAN)
        secure_1psid = input("  > ").strip()
        cprint("  Add meg a GEMINI_SECURE_1PSIDTS értéket:", Colors.CYAN)
        secure_1psidts = input("  > ").strip()

    # Export beállítások
    cprint("\n📁 Export beállítások:", Colors.BOLD)
    output_dir = "./exports"
    delay = "0.5"

    if not quick:
        out = input(f"  Kimeneti könyvtár [{output_dir}]: ").strip()
        if out:
            output_dir = out
        d = input(f"  Késleltetés mp-ben [{delay}]: ").strip()
        if d:
            delay = d

    # AI beállítások
    cprint("\n🤖 AI beállítások (OpenAI API / Ollama / LM Studio):", Colors.BOLD)

    if quick:
        openai_key = ""
        openai_model = ""
        openai_embedding = ""
        openai_base = ""
    else:
        cprint("  Válassz AI szolgáltatót:", Colors.CYAN)
        cprint("    [1] OpenAI (felhő)", Colors.DIM)
        cprint("    [2] Ollama (helyi) — http://localhost:11434/v1", Colors.DIM)
        cprint("    [3] LM Studio (helyi) — http://localhost:1234/v1", Colors.DIM)
        cprint("    [4] Egyéni endpoint", Colors.DIM)
        cprint("    [Enter] Kihagyás", Colors.DIM)
        provider_choice = input("  > ").strip()

        if provider_choice == "1":
            cprint("  OpenAI API kulcs:", Colors.CYAN)
            openai_key = input("  OPENAI_API_KEY: ").strip()
            openai_base = ""
            if openai_key:
                openai_model = input("  OPENAI_MODEL [gpt-4o-mini]: ").strip() or "gpt-4o-mini"
                openai_embedding = input("  OPENAI_EMBEDDING_MODEL [text-embedding-3-small]: ").strip() or "text-embedding-3-small"
            else:
                openai_model = ""
                openai_embedding = ""
        elif provider_choice == "2":
            openai_key = "ollama"
            openai_base = "http://localhost:11434/v1"
            openai_model = input("  Ollama chat modell [llama3.2]: ").strip() or "llama3.2"
            openai_embedding = input("  Ollama embedding modell [nomic-embed-text]: ").strip() or "nomic-embed-text"
            cprint("  ⚠ Futtasd először: ollama pull llama3.2 nomic-embed-text", Colors.YELLOW)
        elif provider_choice == "3":
            openai_key = "lm-studio"
            openai_base = "http://localhost:1234/v1"
            openai_model = input("  LM Studio modell név: ").strip() or "local-model"
            openai_embedding = input("  Embedding modell név [text-embedding-nomic-embed-text-v1.5]: ").strip() or "text-embedding-nomic-embed-text-v1.5"
        elif provider_choice == "4":
            openai_key = input("  API kulcs (opcionális): ").strip()
            openai_base = input("  OPENAI_BASE_URL: ").strip()
            openai_model = input("  OPENAI_MODEL: ").strip() or "gpt-4o-mini"
            openai_embedding = input("  OPENAI_EMBEDDING_MODEL [text-embedding-3-small]: ").strip() or "text-embedding-3-small"
        else:
            openai_key = ""
            openai_model = ""
            openai_embedding = ""
            openai_base = ""

    # .env fájl írása
    lines = [
        "# Gemini Chat Exporter — Környezeti változók",
        f"GEMINI_SECURE_1PSID={secure_1psid}",
        f"GEMINI_SECURE_1PSIDTS={secure_1psidts}",
        "",
        f"EXPORT_OUTPUT_DIR={output_dir}",
        f"EXPORT_DELAY={delay}",
    ]

    if openai_key:
        lines.extend([
            "",
            f"OPENAI_API_KEY={openai_key}",
            f"OPENAI_MODEL={openai_model}",
            f"OPENAI_EMBEDDING_MODEL={openai_embedding}",
        ])
        if openai_base:
            lines.append(f"OPENAI_BASE_URL={openai_base}")

    lines.append("")  # trailing newline

    env_path.write_text("\n".join(lines), encoding="utf-8")
    cprint(f"\n  ✓ .env fájl létrehozva: {env_path}", Colors.GREEN)

    if not secure_1psid:
        cprint("  ⚠ A cookie értékek üresek — töltsd ki őket később!", Colors.YELLOW)
    else:
        # Keyring opció
        try:
            from gemini_export.cookie_store import KEYRING_AVAILABLE, set_cookies
            if KEYRING_AVAILABLE:
                cprint("\n  🔐 Elmented a cookie-kat a rendszer kulcstartóba is?", Colors.CYAN)
                cprint("  A keyring biztonságosabb, mint a .env fájl.", Colors.DIM)
                cprint("  [i] Igen — keyring + .env", Colors.DIM)
                cprint("  [n] Nem  — csak .env fájl (plaintext)", Colors.DIM)
                use_keyring = input("  > ").strip().lower()
                if use_keyring in ("i", "y", "yes"):
                    if set_cookies(secure_1psid, secure_1psidts):
                        cprint("  ✓ Cookie-k elmentve a keyringbe is.", Colors.GREEN)
                    else:
                        cprint("  ⚠ Keyring mentés sikertelen — a .env megmarad.", Colors.YELLOW)
        except ImportError:
            pass  # cookie_store nem elérhető

    return True


# ─── API kapcsolat teszt ─────────────────────────────────────────────────────

def test_gemini_connection() -> bool:
    """Teszteli a Gemini API kapcsolatot."""
    from dotenv import load_dotenv
    load_dotenv()

    cprint("\n🌐 Gemini API kapcsolat tesztelése...", Colors.BOLD)

    secure_1psid = os.getenv("GEMINI_SECURE_1PSID", "")
    secure_1psidts = os.getenv("GEMINI_SECURE_1PSIDTS", "")

    if not secure_1psid:
        cprint("  ⚠ GEMINI_SECURE_1PSID nincs beállítva — teszt kihagyva", Colors.YELLOW)
        return True  # Nem kritikus hiba

    try:
        import asyncio
        from gemini_webapi import GeminiClient

        async def _test():
            client = GeminiClient(secure_1psid, secure_1psidts)
            await client.init(timeout=30, auto_close=True)

        asyncio.run(_test())
        cprint("  ✓ API kapcsolat OK", Colors.GREEN)
        return True
    except Exception as e:
        cprint(f"  ✗ API hiba: {e}", Colors.RED)
        cprint("  Ellenőrizd a cookie értékeket — lehet, hogy lejártak.", Colors.DIM)
        return False


def test_ai_connection() -> bool:
    """Teszteli az OpenAI API kapcsolatot (ha van kulcs)."""
    from dotenv import load_dotenv
    load_dotenv()

    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key:
        return True  # Nem kötelező

    cprint("\n🤖 OpenAI API kapcsolat tesztelése...", Colors.BOLD)

    try:
        from openai import OpenAI

        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        client = OpenAI(api_key=openai_key, base_url=base_url)
        client.models.list(limit=1)  # Gyors teszt
        cprint("  ✓ AI API kapcsolat OK", Colors.GREEN)
        return True
    except Exception as e:
        cprint(f"  ⚠ AI API hiba: {e}", Colors.YELLOW)
        cprint("  Az AI funkciók nem lesznek elérhetőek.", Colors.DIM)
        return True  # Nem kritikus hiba


# ─── Könyvtárszerkezet ───────────────────────────────────────────────────────

def create_directory_structure() -> bool:
    """Létrehozza a szükséges könyvtárakat."""
    cprint("\n📁 Könyvtárszerkezet ellenőrzése...", Colors.BOLD)

    dirs = ["exports", "templates", "tests"]

    for d in dirs:
        path = Path(__file__).parent / d
        if not path.exists():
            path.mkdir(parents=True)
            cprint(f"  ✓ {d}/ létrehozva", Colors.GREEN)
        else:
            cprint(f"  ✓ {d}/ OK", Colors.GREEN)

    return True


# ─── Windows asztali parancsikon ─────────────────────────────────────────────

def create_desktop_shortcut() -> bool:
    """Létrehoz egy Windows parancsikont az app.py indításához."""
    if sys.platform != "win32":
        return True

    cprint("\n🖥️ Asztali parancsikon:", Colors.BOLD)
    create = input("  Létrehozol parancsikont az asztalon? [i/n]: ").strip().lower()
    if create not in ("i", "y", "yes"):
        return True

    try:
        import pythoncom
        from win32com.client import Dispatch

        project_dir = Path(__file__).parent.resolve()
        python_exe = sys.executable
        app_script = project_dir / "app.py"

        desktop = Path.home() / "Desktop"
        shortcut_path = desktop / "Gemini Chat Exporter.lnk"

        shell = Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(str(shortcut_path))
        shortcut.TargetPath = str(python_exe)
        shortcut.Arguments = f'"{app_script}"'
        shortcut.WorkingDirectory = str(project_dir)
        shortcut.Description = "Gemini beszélgetések exportálása és tudástár"
        shortcut.IconLocation = str(python_exe)
        shortcut.Save()

        cprint(f"  ✓ Parancsikon létrehozva: {shortcut_path}", Colors.GREEN)
        return True
    except ImportError:
        cprint("  ⚠ pywin32 nincs telepítve (parancsikon kihagyva)", Colors.YELLOW)
        cprint("  Telepítés: pip install pywin32", Colors.DIM)
        return True
    except Exception as e:
        cprint(f"  ⚠ Parancsikon hiba: {e}", Colors.YELLOW)
        return True


# ─── Fő belépési pont ────────────────────────────────────────────────────────

def main():
    """Telepítő varázsló fő függvénye."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Gemini Chat Exporter — Telepítő és Konfigurációs Varázsló"
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Gyors telepítés (alapértelmezett értékekkel, interakció nélkül)"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Csak függőségek ellenőrzése, telepítés nélkül"
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="Színek kikapcsolása"
    )
    args = parser.parse_args()

    if args.no_color:
        Colors.disable()

    # Fejléc
    print()
    cprint("╔══════════════════════════════════════════════════════════╗", Colors.CYAN)
    cprint("║        Gemini Chat Exporter — Telepítő Varázsló         ║", Colors.CYAN)
    cprint("╚══════════════════════════════════════════════════════════╝", Colors.CYAN)

    # 1. Függőségek ellenőrzése
    deps_ok = check_dependencies()

    if args.check:
        if deps_ok:
            cprint("\n✅ Minden függőség rendben!", Colors.GREEN)
        else:
            cprint("\n❌ Hiányzó függőségek!", Colors.RED)
        return

    # 2. Könyvtárszerkezet
    create_directory_structure()

    # 3. .env fájl létrehozása
    create_env_file(quick=args.quick)

    # 4. API kapcsolat teszt
    if not args.quick:
        test_gemini_connection()
        test_ai_connection()

    # 5. Windows parancsikon
    if not args.quick:
        create_desktop_shortcut()

    # Befejezés
    cprint("\n╔══════════════════════════════════════════════════════════╗", Colors.GREEN)
    cprint("║                  ✨ Telepítés kész! ✨                  ║", Colors.GREEN)
    cprint("╚══════════════════════════════════════════════════════════╝", Colors.GREEN)

    cprint("\n  Gyors indítás:", Colors.BOLD)
    cprint("    python export.py              # CLI export", Colors.CYAN)
    cprint("    python app.py                 # Webes felület → http://localhost:5000", Colors.CYAN)
    cprint("    python export.py --ai-analyze # Export + AI elemzés", Colors.CYAN)
    cprint("    python export.py --browse     # Interaktív böngésző", Colors.CYAN)

    cprint("\n  Docker:", Colors.BOLD)
    cprint("    docker compose up -d          # Konténeres futtatás", Colors.CYAN)

    cprint("\n  Dokumentáció: README.md", Colors.DIM)
    print()


if __name__ == "__main__":
    main()
