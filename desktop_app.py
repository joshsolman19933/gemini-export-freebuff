#!/usr/bin/env python3
"""
Gemini Chat Exporter — Desktop Application
============================================
Natív desktop alkalmazás pywebview ablakban, a Flask webes felületet
futtatva a háttérben. System tray ikonnal a gyors eléréshez.

Használat:
    python desktop_app.py

Függőségek:
    pip install pywebview pystray pillow
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import webbrowser
from pathlib import Path

from app import app, start_cleanup_scheduler

# ─── Függőségek (lazy, a desktop módhoz nem feltétlenül kellenek a szerver módban) ──

try:
    import webview  # type: ignore[import-untyped]
    WEBVIEW_AVAILABLE = True
except ImportError:
    WEBVIEW_AVAILABLE = False

try:
    import pystray  # type: ignore[import-untyped]
    PYSTRAY_AVAILABLE = True
except ImportError:
    PYSTRAY_AVAILABLE = False

try:
    from PIL import Image  # type: ignore[import-untyped]
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ─── Konfiguráció ──────────────────────────────────────────────────────────

HOST = os.getenv("DESKTOP_HOST", "127.0.0.1")
PORT = int(os.getenv("DESKTOP_PORT", "5000"))
APP_TITLE = "Gemini Chat Exporter"
ICON_PATH = Path(__file__).parent / "static" / "icons" / "icon-192.png"


# ─── System Tray ────────────────────────────────────────────────────────────

def _load_icon() -> Image.Image | None:
    """Betölti az app ikont, vagy generál egy egyszerű placeholder-t."""
    if PIL_AVAILABLE and ICON_PATH.exists():
        return Image.open(ICON_PATH)
    if PIL_AVAILABLE:
        # Placeholder: 64×64-es kék négyzet "G" betűvel
        try:
            from PIL import ImageDraw, ImageFont

            img = Image.new("RGBA", (64, 64), (108, 142, 255, 255))
            draw = ImageDraw.Draw(img)
            # Próbálunk betűtípust találni
            font = None
            for font_path in [
                "C:\\Windows\\Fonts\\segoeui.ttf",
                "C:\\Windows\\Fonts\\segoeuib.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/System/Library/Fonts/Helvetica.ttc",
            ]:
                if Path(font_path).exists():
                    try:
                        font = ImageFont.truetype(font_path, 36)
                    except Exception:
                        pass
                    break
            # "G" betű középre
            text_bbox = draw.textbbox((0, 0), "G", font=font)
            tw = text_bbox[2] - text_bbox[0]
            th = text_bbox[3] - text_bbox[1]
            draw.text(((64 - tw) // 2, (64 - th) // 2 - 2), "G", fill="white", font=font)
            return img
        except Exception:
            pass
    return None


def _create_tray_icon() -> pystray.Icon | None:
    """Létrehozza a system tray ikont menüvel."""
    if not PYSTRAY_AVAILABLE:
        return None

    icon_image = _load_icon()
    if icon_image is None:
        return None

    def open_dashboard() -> None:
        """Megnyitja a dashboardot a böngészőben."""
        webbrowser.open(f"http://{HOST}:{PORT}/dashboard")

    def open_export() -> None:
        """Megnyitja az export felületet a böngészőben."""
        webbrowser.open(f"http://{HOST}:{PORT}")

    def quit_app(icon: pystray.Icon) -> None:
        """Kilépés: tray ikon leállítása + webview bezárása, majd graceful shutdown."""
        icon.stop()
        if WEBVIEW_AVAILABLE:
            try:
                for w in webview.windows:
                    w.destroy()
            except Exception:
                pass
        # Graceful shutdown: SIGINT a Flask leállításához, majd sys.exit
        if hasattr(signal, "SIGINT"):
            os.kill(os.getpid(), signal.SIGINT)
        else:
            sys.exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("📤 Export felület", lambda: open_export(), default=True),
        pystray.MenuItem("📊 Dashboard", lambda: open_dashboard()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("❌ Kilépés", lambda icon: quit_app(icon)),
    )

    return pystray.Icon("gemini-export", icon_image, APP_TITLE, menu)


def _start_tray() -> None:
    """Háttérszálban elindítja a system tray ikont."""
    tray = _create_tray_icon()
    if tray is not None:
        tray.run()


# ─── Flask indítása háttérszálban ──────────────────────────────────────────


def _run_flask() -> None:
    """Háttérszálban futtatja a Flask szervert."""
    start_cleanup_scheduler()
    app.run(host=HOST, port=PORT, debug=False, threaded=True, use_reloader=False)


# ─── Desktop mód ────────────────────────────────────────────────────────────


def run_desktop() -> None:
    """Elindítja a desktop alkalmazást pywebview-ben."""
    if not WEBVIEW_AVAILABLE:
        print(
            "A pywebview csomag nincs telepítve. Telepítsd: pip install pywebview\n"
            "Addig a Flask szerver elindul, nyisd meg böngészőben: "
            f"http://{HOST}:{PORT}"
        )
        _run_flask()
        return

    # Flask indítása háttérszálban
    flask_thread = threading.Thread(target=_run_flask, daemon=True, name="flask-server")
    flask_thread.start()

    # System tray indítása háttérszálban (ha elérhető)
    if PYSTRAY_AVAILABLE:
        tray_thread = threading.Thread(target=_start_tray, daemon=True, name="system-tray")
        tray_thread.start()

    # pywebview ablak létrehozása és indítása
    window = webview.create_window(
        title=APP_TITLE,
        url=f"http://{HOST}:{PORT}",
        width=1280,
        height=800,
        min_size=(900, 600),
        text_select=True,
        confirm_close=False,
    )

    webview.start(gui="cef" if sys.platform == "win32" else None)


# ─── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"🚀 {APP_TITLE} — Desktop mód")
    print(f"   Ha a pywebview nincs telepítve, a Flask szerver elindul http://{HOST}:{PORT}")
    run_desktop()
