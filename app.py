#!/usr/bin/env python3
"""
Gemini Chat Exporter — Web GUI
================================
Flask alapú webes felület a Gemini beszélgetések exportálásához.
Az export.py-t alprocesszként futtatja, a kimenetet SSE-n keresztül
streameli a böngészőbe.

Használat:
    python app.py
    # Majd nyisd meg: http://localhost:5000
"""

import json
import os
import queue
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from gemini_export.logging_config import get_logger

logger = get_logger(__name__)

# Manifest függvények importálása (az export.py nehéz függőségei miatt try/except)
_search_chats = _list_tags = _add_tags = _set_project = _toggle_favorite = _reindex_all_chats = None
_init_manifest = _manifest_get_stats = None
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from gemini_export.manifest import _init_manifest, _manifest_get_stats
    from gemini_export.search import (
        _add_tags,
        _list_tags,
        _reindex_all_chats,
        _search_chats,
        _set_project,
        _toggle_favorite,
    )
except ImportError:
    pass  # A manifest funkciók nem elérhetőek, de az export toolok igen

# AI réteg importálása
try:
    from ai_layer import (
        _ensure_ai_schema,
        _get_ai_results,
        analyze_chat_from_json,
    )
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

# ─── Inicializálás ───────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# Rate limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["120 per minute"],
    storage_uri="memory://",
    headers_enabled=True,
)


def _safe_redirect(target: str, fallback: str) -> str:
    """Csak relatív vagy same-origin átirányítást engedélyez."""
    if not target:
        return fallback
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return target if test_url.netloc == ref_url.netloc else fallback


# Dashboard hitelesítés
DASHBOARD_TOKEN = os.getenv("DASHBOARD_ACCESS_TOKEN", "")
AUTH_ENABLED = bool(DASHBOARD_TOKEN)

# Alapértelmezett output könyvtár
DEFAULT_OUTPUT = "./exports"

# Publikus route-ok (nem igényelnek auth-ot)
PUBLIC_ROUTES = {"/", "/login", "/start"}
PUBLIC_PREFIXES = ("/stream/", "/status/")


# ─── Hitelesítés middleware ──────────────────────────────────────────────

# Egyszerű in-memory rate limiter a login endpointra
_login_attempts: dict[str, list[float]] = {}


def _check_rate_limit(ip: str, max_attempts: int = 5, window: int = 60) -> bool:
    """Ellenőrzi, hogy egy IP nem lépte-e túl a limitet. Returns True ha OK."""
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Ablakon kívüli próbálkozások törlése
    attempts = [t for t in attempts if now - t < window]
    _login_attempts[ip] = attempts
    return len(attempts) < max_attempts


@app.before_request
def check_auth():
    """Auth middleware: védi a /dashboard és /api/* route-okat."""
    if not AUTH_ENABLED:
        return None  # Nincs auth beállítva → minden nyitott

    path = request.path

    # Publikus route-ok átengedése
    if path in PUBLIC_ROUTES:
        return None
    if path.startswith(PUBLIC_PREFIXES):
        return None

    # Static fájlok átengedése
    if path.startswith("/static/"):
        return None

    # Session alapú auth (login után)
    if session.get("dashboard_authenticated"):
        return None

    # Token query param vagy Bearer header
    token = request.args.get("token") or ""
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if token and secrets.compare_digest(token, DASHBOARD_TOKEN):
        # Automatikus session létrehozás token esetén
        session["dashboard_authenticated"] = True
        session.permanent = True
        return None

    # API hívásoknál JSON hibát, oldal betöltésnél redirectet küldünk
    if path.startswith("/api/"):
        return jsonify({"error": "Unauthorized", "auth_required": True}), 401
    if path.startswith("/dashboard"):
        return redirect(url_for("login", next=request.url))

    return abort(401)


def _get_manifest():
    """Visszaad egy manifest kapcsolatot."""
    output_dir = Path(DEFAULT_OUTPUT)
    output_dir.mkdir(parents=True, exist_ok=True)
    return _init_manifest(output_dir)


def _get_chat_data(cid: str) -> dict | None:
    """Betölti egy chat JSON adatait."""
    output_dir = Path(DEFAULT_OUTPUT)
    pattern = f"*_{cid[:8]}.json"
    json_files = list(output_dir.glob(f"json/{pattern}"))
    if not json_files:
        return None
    try:
        return json.loads(json_files[0].read_text(encoding="utf-8"))
    except Exception:
        return None

# Aktív taskok tárolása: { task_id: { "proc": Popen, "queue": Queue, "output_dir": str } }
tasks: dict[str, dict] = {}
tasks_lock = threading.Lock()


def cleanup_old_tasks():
    """Eltávolítja a már lefutott taskokat 1 óra után. Periodikusan hívódik."""
    with tasks_lock:
        to_remove = []
        for tid, task in list(tasks.items()):
            if task.get("finished") and time.time() - task.get("finished_at", 0) > 3600:
                to_remove.append(tid)
        for tid in to_remove:
            del tasks[tid]


def start_cleanup_scheduler():
    """Háttérszál, ami rendszeresen takarítja a régi taskokat."""
    def _run():
        while True:
            time.sleep(300)  # 5 percenként
            cleanup_old_tasks()
    t = threading.Thread(target=_run, daemon=True)
    t.start()


# ─── Rate limit error handler ──────────────────────────────────────────

@app.errorhandler(429)
def ratelimit_error(e):
    """Rate limit túllépés kezelése — JSON válasz API hívásoknál."""
    retry_after = e.description if isinstance(e.description, str) else "60"
    if request.path.startswith("/api/"):
        return jsonify({
            "error": "Rate limit exceeded",
            "retry_after_seconds": retry_after,
        }), 429
    return render_template("login.html", error="Túl sok kérés. Próbáld újra később."), 429


# ─── Hitelesítés route-ok ───────────────────────────────────────────────


def _record_export_start(task_id: str, output_dir: str, fmt: str, data: dict) -> None:
    """Rögzíti az export session kezdetét az export_history táblában."""
    try:
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        settings = {
            k: data.get(k)
            for k in ("format", "delay", "max_chats", "concurrency",
                      "output_dir", "from_date", "to_date", "keyword_filter")
            if data.get(k) is not None
        }
        conn = _get_manifest()
        try:
            conn.execute(
                """INSERT INTO export_history
                   (task_id, started_at, output_dir, format, settings_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (task_id, now, output_dir, fmt, json.dumps(settings)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.debug("Failed to record export start: %s", e)


def _record_export_end(task_id: str, returncode: int) -> None:
    """Frissíti az export session befejezési idejét az export_history táblában."""
    try:
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        conn = _get_manifest()
        try:
            # Lekérjük a manifest statisztikákat az eredményekhez
            row = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) FROM exports"
            ).fetchone()
            total = row[0] or 0
            ok_count = row[1] or 0
            fail_count = row[2] or 0
            conn.execute(
                "UPDATE export_history SET finished_at = ?, total_chats = ?, "
                "exported = ?, failed = ? WHERE task_id = ?",
                (now, total, ok_count, fail_count, task_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.debug("Failed to record export end: %s", e)


# ─── Hitelesítés route-ok ───────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
@limiter.exempt
def login():
    """Login oldal — ha nincs auth beállítva, átirányít a dashboardra."""
    if not AUTH_ENABLED:
        return redirect(url_for("dashboard"))

    error = None
    next_url = _safe_redirect(request.args.get("next", ""), url_for("dashboard"))

    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        if not _check_rate_limit(ip):
            error = "Túl sok próbálkozás. Próbáld újra 1 perc múlva."
            logger.warning("Rate limit exceeded for IP: %s", ip)
        else:
            _login_attempts.setdefault(ip, []).append(time.time())
            token = request.form.get("token", "").strip()
            if token and secrets.compare_digest(token, DASHBOARD_TOKEN):
                session["dashboard_authenticated"] = True
                session.permanent = True
                logger.info("Successful dashboard login")
                return redirect(next_url)
            error = "Érvénytelen hozzáférési token."
            logger.warning("Failed login attempt from IP: %s", ip)

    return render_template("login.html", error=error, next_url=next_url)


@app.route("/logout")
@limiter.exempt
def logout():
    """Kijelentkezés."""
    session.pop("dashboard_authenticated", None)
    return redirect(url_for("login"))


# ─── Route-ok ────────────────────────────────────────────────────────────────

@app.route("/")
@limiter.exempt
def index():
    """Főoldal — a GUI megjelenítése."""
    return render_template("index.html")


@app.route("/sw.js")
@limiter.exempt
def service_worker():
    """Service worker kiszolgálása a gyökér útvonalról (scope miatt)."""
    return app.send_static_file("sw.js")


@app.route("/manifest.json")
@limiter.exempt
def web_manifest():
    """PWA manifest kiszolgálása a gyökér útvonalról."""
    return app.send_static_file("manifest.json")


@app.route("/start", methods=["POST"])
@limiter.exempt
def start_export():
    """Exportálás indítása. A konfigurációt a request body-ból olvassa."""
    data = request.get_json(force=True)

    # ── Parancssor összeállítása ────────────────────────────────────────
    cmd = ["python", "-u", "export.py"]  # -u: unbuffered stdout

    if data.get("auto_cookies"):
        cmd.append("--auto-cookies")

    fmt = data.get("format", "both")
    cmd.extend(["--format", fmt])

    output_dir = data.get("output_dir", "./exports")
    cmd.extend(["--output", output_dir])

    delay = data.get("delay", 0.5)
    cmd.extend(["--delay", str(delay)])

    if data.get("no_resume"):
        cmd.append("--no-resume")

    max_chats = data.get("max_chats", 2000)
    cmd.extend(["--max-chats", str(max_chats)])

    if data.get("from_date"):
        cmd.extend(["--from", data["from_date"]])
    if data.get("to_date"):
        cmd.extend(["--to", data["to_date"]])
    if data.get("keyword_filter"):
        cmd.extend(["--filter", data["keyword_filter"]])
    if data.get("list_only"):
        cmd.append("--list-chats")

    # Template téma HTML exportnál
    template = data.get("template", "dark")
    if template and "html" in fmt:
        cmd.extend(["--template", template])

    concurrency = data.get("concurrency", 3)
    cmd.extend(["--concurrency", str(concurrency)])

    # ── Környezeti változók ─────────────────────────────────────────────
    env = os.environ.copy()
    if data.get("secure_1psid"):
        env["GEMINI_SECURE_1PSID"] = data["secure_1psid"]
    if data.get("secure_1psidts"):
        env["GEMINI_SECURE_1PSIDTS"] = data["secure_1psidts"]

    # ── Alprocessz indítása ─────────────────────────────────────────────
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=Path(__file__).parent,
        )
    except FileNotFoundError:
        return jsonify({"error": "export.py nem található. Ellenőrizd, hogy a projekt könyvtárában vagy."}), 500
    except Exception as e:
        return jsonify({"error": f"Nem sikerült indítani az exportot: {e}"}), 500

    # ── Task regisztrálása ──────────────────────────────────────────────
    task_id = str(uuid.uuid4())[:8]

    # Export history rögzítése
    _record_export_start(task_id, output_dir, fmt, data)

    # Queue a stdout sorok gyűjtésére (külön szál olvassa)
    line_queue: queue.Queue = queue.Queue()

    def reader_thread():
        """Külön szálban olvassa a subprocess stdout-ját és Queue-ba teszi."""
        try:
            for line in iter(proc.stdout.readline, ""):
                line_queue.put(line)
        except Exception:
            pass
        finally:
            proc.stdout.close()
            returncode = proc.wait()
            line_queue.put(None)  # Sentinel: vége
            with tasks_lock:
                if task_id in tasks:
                    tasks[task_id]["returncode"] = returncode
                    tasks[task_id]["finished"] = True
                    tasks[task_id]["finished_at"] = time.time()
            # Frissítsük az export history-t
            _record_export_end(task_id, returncode)

    with tasks_lock:
        tasks[task_id] = {
            "proc": proc,
            "queue": line_queue,
            "output_dir": str(Path(output_dir).resolve()),
        }

    thread = threading.Thread(target=reader_thread, daemon=True)
    thread.start()

    return jsonify({"task_id": task_id})


@app.route("/stream/<task_id>")
@limiter.exempt
def stream_export(task_id: str):
    """SSE végpont — valós időben streameli az export kimenetét."""

    def generate():
        with tasks_lock:
            task = tasks.get(task_id)

        if not task:
            yield "event: error\ndata: A task nem található (lehet, hogy lejárt).\n\n"
            return

        # Kimentjük a queue referenciát, hogy a lock elengedése után is biztonságos legyen
        line_queue = task["queue"]
        output_dir = task.get("output_dir", "./exports")

        while True:
            try:
                line = line_queue.get(timeout=30)
            except queue.Empty:
                # Timeout: küldjünk egy ping-et, hogy a kapcsolat élő maradjon
                yield ": ping\n\n"
                continue

            if line is None:
                # Sentinel: a subprocess befejeződött
                break

            # Escape HTML/SSE speciális karaktereket — rstrip() minden whitespace-t levág
            safe_line = line.rstrip()
            yield f"data: {safe_line}\n\n"

        # Befejezés
        with tasks_lock:
            returncode = task.get("returncode", -1)
        yield f"event: complete\ndata: {{\"returncode\": {returncode}, \"output_dir\": \"{output_dir}\"}}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/status/<task_id>")
@limiter.exempt
def task_status(task_id: str):
    """Egy task státuszának lekérdezése."""
    with tasks_lock:
        task = tasks.get(task_id)

    if not task:
        return jsonify({"error": "Task not found"}), 404

    return jsonify({
        "task_id": task_id,
        "finished": task.get("finished", False),
        "returncode": task.get("returncode"),
        "output_dir": task.get("output_dir"),
    })


# ─── Dashboard / Tudástár API ──────────────────────────────────────────────

@app.route("/dashboard")
@limiter.exempt
def dashboard():
    """Dashboard főoldal — tudástár böngésző."""
    return render_template("dashboard.html")


@app.route("/api/chats")
def api_chats():
    """Chat lista a manifestből metaadatokkal, opcionális lapozással."""
    offset = request.args.get("offset", 0, type=int)
    limit = request.args.get("limit", 50, type=int)
    if limit > 500:
        limit = 500
    conn = _get_manifest()
    try:
        rows = conn.execute("""
            SELECT e.chat_id, e.title, e.last_exported_at, e.message_count, e.status, e.image_count,
                   m.tags, m.project, m.is_favorite, m.processing_status, m.notes, m.analyzed_at
            FROM exports e
            LEFT JOIN chat_metadata m ON e.chat_id = m.chat_id
            ORDER BY e.last_exported_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

        total = conn.execute("SELECT COUNT(*) FROM exports").fetchone()[0]

        chats = []
        for row in rows:
            cid, title, exported_at, msg_count, status, img_count, tags_json, project, fav, proc, notes, analyzed_at = row
            try:
                tags = json.loads(tags_json) if tags_json else []
            except json.JSONDecodeError:
                tags = []
            chats.append({
                "cid": cid, "title": title or "Untitled",
                "exported_at": exported_at, "message_count": msg_count or 0,
                "status": status, "image_count": img_count or 0,
                "tags": tags, "project": project,
                "is_favorite": bool(fav), "processing_status": proc or "new",
                "notes": notes, "analyzed_at": analyzed_at,
            })
        return jsonify({"chats": chats, "total": total, "offset": offset, "limit": limit})
    finally:
        conn.close()


@app.route("/api/chat/<cid>")
def api_chat_detail(cid: str):
    """Egy chat részletes adatainak lekérése."""
    data = _get_chat_data(cid)
    if not data:
        return jsonify({"error": "Chat not found"}), 404
    return jsonify(data)


@app.route("/api/search")
def api_search():
    """Hibrid keresés: FTS5 + embedding kombinált rangsorolás."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    conn = _get_manifest()
    try:
        # Hibrid keresés (embedding + FTS5), ha az AI réteg elérhető
        try:
            from ai_layer import hybrid_search
            results = hybrid_search(conn, q, limit=50)
        except ImportError:
            results = _search_chats(conn, q, limit=50)
        return jsonify(results)
    finally:
        conn.close()


@app.route("/api/tags")
def api_tags():
    """Összes egyedi címke."""
    conn = _get_manifest()
    try:
        tags = _list_tags(conn)
        return jsonify(tags)
    finally:
        conn.close()


@app.route("/api/chat/<cid>/metadata", methods=["GET", "POST"])
def api_chat_metadata(cid: str):
    """Metaadatok lekérése és módosítása."""
    conn = _get_manifest()
    try:
        if request.method == "GET":
            row = conn.execute(
                "SELECT tags, project, is_favorite, processing_status, notes FROM chat_metadata WHERE chat_id = ?",
                (cid,),
            ).fetchone()
            if not row:
                return jsonify({"tags": [], "project": None, "is_favorite": False, "processing_status": "new", "notes": None})
            tags_json, project, fav, proc, notes = row
            try:
                tags = json.loads(tags_json) if tags_json else []
            except json.JSONDecodeError:
                tags = []
            return jsonify({
                "tags": tags, "project": project, "is_favorite": bool(fav),
                "processing_status": proc or "new", "notes": notes,
            })

        # POST: módosítás
        data = request.get_json(force=True)
        action = data.get("action", "")

        if action == "add_tags":
            _add_tags(conn, cid, data.get("tags", []))
        elif action == "remove_tags":
            from gemini_export.search import _ensure_metadata_row
            _ensure_metadata_row(conn, cid)
            row = conn.execute("SELECT tags FROM chat_metadata WHERE chat_id = ?", (cid,)).fetchone()
            existing = []
            if row and row[0]:
                try:
                    existing = json.loads(row[0])
                except json.JSONDecodeError:
                    existing = []
            remove = set(t.lower() for t in data.get("tags", []))
            new_tags = [t for t in existing if t not in remove]
            conn.execute(
                "UPDATE chat_metadata SET tags = ?, updated_at = ? WHERE chat_id = ?",
                (json.dumps(new_tags), __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), cid),
            )
            conn.commit()
        elif action == "set_project":
            _set_project(conn, cid, data.get("project", ""))
        elif action == "toggle_favorite":
            _toggle_favorite(conn, cid)
        elif action == "set_notes":
            conn.execute(
                "INSERT OR REPLACE INTO chat_metadata (chat_id, notes, updated_at) VALUES (?, ?, datetime('now'))",
                (cid, data.get("notes", ""), __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()),
            )
            conn.commit()
        elif action == "toggle_todo":
            _ensure_ai_schema(conn)
            todo_id = data.get("todo_id")
            done = 1 if data.get("done") else 0
            conn.execute(
                "UPDATE chat_todos SET done = ? WHERE id = ? AND chat_id = ?",
                (done, todo_id, cid),
            )
            conn.commit()

        # Visszaadjuk a frissített metaadatokat
        row = conn.execute(
            "SELECT tags, project, is_favorite, processing_status, notes FROM chat_metadata WHERE chat_id = ?",
            (cid,),
        ).fetchone()
        if not row:
            return jsonify({"error": "Metadata not found"}), 404
        tags_json, project, fav, proc, notes = row
        try:
            tags = json.loads(tags_json) if tags_json else []
        except json.JSONDecodeError:
            tags = []
        return jsonify({
            "tags": tags, "project": project, "is_favorite": bool(fav),
            "processing_status": proc or "new", "notes": notes,
        })
    finally:
        conn.close()


@app.route("/api/stats")
def api_stats():
    """Statisztikák a manifestből."""
    conn = _get_manifest()
    try:
        mstats = _manifest_get_stats(conn)
        total_msgs = conn.execute("SELECT COALESCE(SUM(message_count), 0) FROM exports").fetchone()[0]
        total_imgs = conn.execute("SELECT COALESCE(SUM(image_count), 0) FROM exports").fetchone()[0]
        tag_count = len(_list_tags(conn))
        fav_count = conn.execute(
            "SELECT COUNT(*) FROM chat_metadata WHERE is_favorite = 1"
        ).fetchone()[0]
        return jsonify({
            "total_chats": mstats["total"],
            "ok": mstats["ok"],
            "failed": mstats["failed"],
            "total_messages": total_msgs,
            "total_images": total_imgs,
            "tag_count": tag_count,
            "favorite_count": fav_count,
        })
    finally:
        conn.close()


@app.route("/api/stats/history")
def api_stats_history():
    """Idősoros adatok a diagramokhoz."""
    conn = _get_manifest()
    try:
        # Export aktivitás időben (utolsó 365 nap, napi bontás)
        rows = conn.execute("""
            SELECT DATE(last_exported_at) as day,
                   COUNT(*) as chat_count,
                   COALESCE(SUM(message_count), 0) as msg_total
            FROM exports
            WHERE last_exported_at IS NOT NULL
            GROUP BY day
            ORDER BY day
        """).fetchall()

        export_timeline = []
        for day, count, msgs in rows:
            export_timeline.append({
                "date": day,
                "chats": count,
                "messages": msgs,
            })

        # Címke eloszlás
        tag_rows = conn.execute(
            "SELECT tags FROM chat_metadata WHERE tags IS NOT NULL AND tags != '[]'"
        ).fetchall()
        tag_counts = {}
        for (tags_json,) in tag_rows:
            try:
                for t in json.loads(tags_json):
                    tag_counts[t] = tag_counts.get(t, 0) + 1
            except json.JSONDecodeError:
                pass
        top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:15]

        # Üzenetszám hisztogram
        hist_rows = conn.execute("""
            SELECT message_count FROM exports WHERE message_count > 0
        """).fetchall()
        msg_counts = [r[0] for r in hist_rows]
        buckets = {"1-5": 0, "6-15": 0, "16-30": 0, "31-60": 0, "61-100": 0, "101+": 0}
        for mc in msg_counts:
            if mc <= 5:
                buckets["1-5"] += 1
            elif mc <= 15:
                buckets["6-15"] += 1
            elif mc <= 30:
                buckets["16-30"] += 1
            elif mc <= 60:
                buckets["31-60"] += 1
            elif mc <= 100:
                buckets["61-100"] += 1
            else:
                buckets["101+"] += 1

        # Projekt eloszlás
        proj_rows = conn.execute(
            "SELECT project, COUNT(*) FROM chat_metadata WHERE project IS NOT NULL AND project != '' GROUP BY project"
        ).fetchall()
        projects = [{"name": r[0], "count": r[1]} for r in proj_rows]

        return jsonify({
            "export_timeline": export_timeline,
            "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
            "message_histogram": [{"bucket": b, "count": c} for b, c in buckets.items()],
            "projects": projects,
        })
    finally:
        conn.close()


@app.route("/api/stats/timeline")
def api_stats_timeline():
    """Részletes idősoros statisztikák (utolsó 90 nap, heti bontás)."""
    conn = _get_manifest()
    try:
        # Heti aktivitás az utolsó 90 napban
        rows = conn.execute("""
            SELECT DATE(last_exported_at) as day,
                   COUNT(*),
                   COALESCE(SUM(message_count), 0),
                   COALESCE(SUM(image_count), 0)
            FROM exports
            WHERE last_exported_at >= DATE('now', '-90 days')
            GROUP BY day
            ORDER BY day
        """).fetchall()

        daily = []
        running_total_messages = 0
        for day, chats, msgs, imgs in rows:
            running_total_messages += msgs
            daily.append({
                "date": day,
                "chats": chats,
                "messages": msgs,
                "images": imgs,
                "cumulative_messages": running_total_messages,
            })

        # Legaktívabb napok (top 10)
        top_days = sorted(daily, key=lambda d: d["messages"], reverse=True)[:10]

        return jsonify({
            "daily": daily,
            "top_days": top_days,
        })
    finally:
        conn.close()



# ─── AI Provider API ──────────────────────────────────────────────────

@app.route("/api/ai/provider")
def api_ai_provider():
    """AI szolgáltató detektálása és állapot ellenőrzése."""
    try:
        from ai_layer import detect_ai_provider
        info = detect_ai_provider()
        return jsonify(info)
    except ImportError:
        return jsonify({"provider": "unavailable", "error": "AI layer not installed"})
    except Exception as e:
        return jsonify({"provider": "error", "error": str(e)})


# ─── Knowledge Graph / Related Chats API ──────────────────────────────

@app.route("/api/chat/<cid>/related")
def api_related_chats(cid: str):
    """Kapcsolódó chat-ek keresése embedding hasonlóság alapján."""
    try:
        from ai_layer import find_related_chats
    except ImportError:
        return jsonify({"error": "AI layer not available. Install openai package."}), 503

    top_k = request.args.get("top_k", 8, type=int)
    conn = _get_manifest()
    try:
        related = find_related_chats(conn, cid, top_k=min(top_k, 20))
        return jsonify(related)
    finally:
        conn.close()


@app.route("/api/chat/<cid>/related/graph")
def api_related_graph(cid: str):
    """Tudásgráf adatok D3.js force-directed vizualizációhoz."""
    try:
        from ai_layer import build_knowledge_graph
    except ImportError:
        return jsonify({"error": "AI layer not available."}), 503

    conn = _get_manifest()
    try:
        graph = build_knowledge_graph(conn, cid)
        return jsonify(graph)
    finally:
        conn.close()


# ─── RAG (Ask Your Archive) API ────────────────────────────────────────

@app.route("/api/rag/query", methods=["POST"])
@limiter.limit("10 per minute")
def api_rag_query():
    """RAG Q&A: természetes nyelvű kérdés a chat archívum alapján, SSE streaming válasszal."""
    data = request.get_json(force=True)
    question = data.get("question", "").strip()
    top_k = min(data.get("top_k", 5), 10)

    if not question:
        return jsonify({"error": "A kérdés mező kötelező."}), 400

    # RAG importok
    try:
        from ai_layer import get_rag_index_status, rag_query_stream
    except ImportError:
        return jsonify({"error": "AI layer not available."}), 503

    conn = _get_manifest()
    output_dir = Path(DEFAULT_OUTPUT)

    def generate():
        try:
            for event in rag_query_stream(conn, output_dir, question, top_k):
                yield event
        finally:
            conn.close()

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/rag/index", methods=["POST"])
def api_rag_index():
    """Embedding indexelés indítása az összes exportált chat-hez."""
    try:
        from ai_layer import get_rag_index_status, index_all_embeddings
    except ImportError:
        return jsonify({"error": "AI layer not available."}), 503

    conn = _get_manifest()
    try:
        result = index_all_embeddings(conn, Path(DEFAULT_OUTPUT))
        return jsonify(result)
    finally:
        conn.close()


@app.route("/api/rag/status")
def api_rag_status():
    """RAG index állapotának lekérése."""
    try:
        from ai_layer import get_rag_index_status
    except ImportError:
        return jsonify({"error": "AI layer not available."}), 503

    conn = _get_manifest()
    try:
        status = get_rag_index_status(conn)
        return jsonify(status)
    finally:
        conn.close()


# ─── AI elemzés API ────────────────────────────────────────────────────────

@app.route("/api/chat/<cid>/analyze", methods=["POST"])
@limiter.limit("30 per minute")
def api_analyze_chat(cid: str):
    """AI elemzés indítása egy chat-en."""
    if not AI_AVAILABLE:
        return jsonify({"error": "AI layer not available. Install openai package and set OPENAI_API_KEY."}), 503

    data = request.get_json(silent=True) or {}
    options = {
        "summarize": data.get("summarize", True),
        "todos": data.get("todos", True),
        "tags": data.get("tags", True),
    }

    conn = _get_manifest()
    try:
        _ensure_ai_schema(conn)
        result = analyze_chat_from_json(conn, Path(DEFAULT_OUTPUT), cid, options)
        return jsonify(result)
    finally:
        conn.close()


@app.route("/api/chat/<cid>/ai-results")
def api_ai_results(cid: str):
    """AI elemzés eredményeinek lekérése."""
    conn = _get_manifest()
    try:
        _ensure_ai_schema(conn)
        results = _get_ai_results(conn, cid)
        return jsonify(results)
    finally:
        conn.close()


@app.route("/api/ai/batch-analyze", methods=["POST"])
@limiter.limit("5 per minute")
def api_batch_analyze():
    """Batch AI elemzés: az összes még nem elemzett chat elemzése SSE streaming progress-szel."""
    try:
        from ai_layer import batch_analyze_all
    except ImportError:
        return jsonify({"error": "AI layer not available."}), 503

    data = request.get_json(silent=True) or {}
    options = {
        "summarize": data.get("summarize", True),
        "todos": data.get("todos", True),
        "tags": data.get("tags", True),
    }

    conn = _get_manifest()
    output_dir = Path(DEFAULT_OUTPUT)

    def generate():
        try:
            for event in batch_analyze_all(conn, output_dir, options):
                yield event
        finally:
            conn.close()

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/reindex", methods=["POST"])
def api_reindex():
    """Újraindexeli a chat-eket."""
    conn = _get_manifest()
    try:
        count = _reindex_all_chats(conn, Path(DEFAULT_OUTPUT))
        return jsonify({"reindexed": count})
    finally:
        conn.close()


# ─── Chat Comparison API ──────────────────────────────────────────────

@app.route("/api/ai/compare", methods=["POST"])
@limiter.limit("10 per minute")
def api_ai_compare():
    """Két chat AI összehasonlítása SSE streaming válasszal."""
    data = request.get_json(force=True)
    cid_a = data.get("cid_a", "").strip()
    cid_b = data.get("cid_b", "").strip()
    perspective = data.get("perspective", "general").strip()

    if not cid_a or not cid_b:
        return jsonify({"error": "Mindkét chat ID megadása kötelező (cid_a, cid_b)."}), 400
    if cid_a == cid_b:
        return jsonify({"error": "Két különböző chat-et kell megadni."}), 400

    valid_perspectives = {"general", "differences", "similarities", "detail"}
    if perspective not in valid_perspectives:
        perspective = "general"

    try:
        from ai_layer import compare_chats
    except ImportError:
        return jsonify({"error": "AI layer not available."}), 503

    conn = _get_manifest()
    output_dir = Path(DEFAULT_OUTPUT)

    def generate():
        try:
            for event in compare_chats(conn, output_dir, cid_a, cid_b, perspective):
                yield event
        finally:
            conn.close()

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ─── Template engine API ───────────────────────────────────────────────

@app.route("/api/templates")
def api_templates():
    """Elérhető HTML export témák listázása."""
    themes = [
        {"id": "dark", "name": "Sötét", "description": "Alapértelmezett sötét téma — gradiens címsorok, animációk"},
        {"id": "light", "name": "Világos", "description": "Tiszta világos téma — indigó akcentus, fehér háttér"},
        {"id": "minimal", "name": "Minimál", "description": "Letisztult, szürkeárnyalatos — nincs zavaró elem"},
        {"id": "academic", "name": "Akadémiai", "description": "Akadémiai stílus — serif betűtípus, meleg tónusok"},
    ]
    try:
        from gemini_export.template_engine import get_template_engine
        engine = get_template_engine()
        using_jinja = engine.available
    except Exception:
        using_jinja = False

    return jsonify({
        "themes": themes,
        "engine": "jinja2" if using_jinja else "string",
    })


# ─── Export Schedule API ──────────────────────────────────────────

@app.route("/api/schedule", methods=["GET", "POST"])
def api_schedule():
    """Ütemezett export job-ok listázása és létrehozása.

    GET:  Aktuális ütemezések listája.
    POST: Új cron job létrehozása (JSON body: cron_expr, format, label, ...).
    """
    try:
        from gemini_export.scheduler import get_scheduler, SCHEDULER_AVAILABLE
    except ImportError:
        return jsonify({"error": "Scheduler module not available."}), 503

    if not SCHEDULER_AVAILABLE:
        return jsonify({"error": "APScheduler not installed. Run: pip install apscheduler"}), 503

    sched = get_scheduler()
    if not sched:
        return jsonify({"error": "Scheduler not initialized. Start the app first."}), 503

    if request.method == "GET":
        jobs = sched.list_jobs()
        return jsonify({
            "jobs": jobs,
            "available": True,
            "count": len(jobs),
        })

    # POST: új job létrehozása
    data = request.get_json(force=True)
    cron_expr = (data.get("cron_expr", "") or "").strip()
    if not cron_expr:
        return jsonify({"error": "A cron_expr mező kötelező."}), 400

    try:
        job_id = sched.add_cron(
            cron_expr,
            format=data.get("format", "both"),
            label=data.get("label") or None,
            output_dir=data.get("output_dir") or None,
            delay=float(data.get("delay", 0.5)),
            max_chats=int(data.get("max_chats", 2000)),
            concurrency=int(data.get("concurrency", 3)),
            no_resume=bool(data.get("no_resume", False)),
            auto_cookies=bool(data.get("auto_cookies", False)),
            from_date=data.get("from_date") or None,
            to_date=data.get("to_date") or None,
            keyword_filter=data.get("keyword_filter") or None,
            template=data.get("template", "dark"),
        )
        return jsonify({"id": job_id, "cron_expr": cron_expr}), 201
    except ValueError as e:
        return jsonify({"error": f"Érvénytelen cron kifejezés: {e}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/schedule/<job_id>", methods=["DELETE"])
def api_schedule_job(job_id: str):
    """Job törlése."""
    try:
        from gemini_export.scheduler import get_scheduler, SCHEDULER_AVAILABLE
    except ImportError:
        return jsonify({"error": "Scheduler module not available."}), 503

    if not SCHEDULER_AVAILABLE:
        return jsonify({"error": "APScheduler not installed."}), 503

    sched = get_scheduler()
    if not sched:
        return jsonify({"error": "Scheduler not initialized."}), 503

    if sched.remove_job(job_id):
        return jsonify({"deleted": job_id})
    return jsonify({"error": "Job not found"}), 404


@app.route("/api/schedule/<job_id>/pause", methods=["POST"])
def api_schedule_pause(job_id: str):
    """Job szüneteltetése."""
    try:
        from gemini_export.scheduler import get_scheduler, SCHEDULER_AVAILABLE
    except ImportError:
        return jsonify({"error": "Scheduler module not available."}), 503

    if not SCHEDULER_AVAILABLE:
        return jsonify({"error": "APScheduler not installed."}), 503

    sched = get_scheduler()
    if not sched:
        return jsonify({"error": "Scheduler not initialized."}), 503

    if sched.pause_job(job_id):
        return jsonify({"paused": job_id})
    return jsonify({"error": "Job not found"}), 404


@app.route("/api/schedule/<job_id>/resume", methods=["POST"])
def api_schedule_resume(job_id: str):
    """Job folytatása."""
    try:
        from gemini_export.scheduler import get_scheduler, SCHEDULER_AVAILABLE
    except ImportError:
        return jsonify({"error": "Scheduler module not available."}), 503

    if not SCHEDULER_AVAILABLE:
        return jsonify({"error": "APScheduler not installed."}), 503

    sched = get_scheduler()
    if not sched:
        return jsonify({"error": "Scheduler not initialized."}), 503

    if sched.resume_job(job_id):
        return jsonify({"resumed": job_id})
    return jsonify({"error": "Job not found"}), 404


@app.route("/api/schedule/<job_id>/run-now", methods=["POST"])
def api_schedule_run_now(job_id: str):
    """Job azonnali futtatása."""
    try:
        from gemini_export.scheduler import get_scheduler, SCHEDULER_AVAILABLE
    except ImportError:
        return jsonify({"error": "Scheduler module not available."}), 503

    if not SCHEDULER_AVAILABLE:
        return jsonify({"error": "APScheduler not installed."}), 503

    sched = get_scheduler()
    if not sched:
        return jsonify({"error": "Scheduler not initialized."}), 503

    if sched.run_job_now(job_id):
        return jsonify({"triggered": job_id})
    return jsonify({"error": "Job not found"}), 404


# ─── Prompt Templates API ──────────────────────────────────────────

@app.route("/api/prompts")
def api_prompts():
    """Az összes elérhető AI prompt template listázása."""
    try:
        from gemini_export.prompt_templates import list_prompts
        prompts = list_prompts()
        return jsonify(prompts)
    except ImportError:
        return jsonify({"error": "Prompt templates module not available."}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/prompts/<name>", methods=["GET", "POST", "DELETE"])
def api_prompt_detail(name: str):
    """Egyedi prompt template lekérése, mentése vagy visszaállítása.

    GET:  Template lekérése (content, custom flag, variables).
    POST: Template mentése (JSON body: {"content": "..."}).
    DELETE: Template visszaállítása az alapértelmezettre.
    """
    try:
        from gemini_export.prompt_templates import (
            get_prompt,
            reset_prompt,
            save_prompt,
        )
    except ImportError:
        return jsonify({"error": "Prompt templates module not available."}), 503

    allowed = {"summarize", "todos", "tags", "rag_query"}
    if name not in allowed:
        return jsonify({
            "error": f"Ismeretlen prompt név: '{name}'. "
                     f"Engedélyezett: {', '.join(sorted(allowed))}"
        }), 404

    if request.method == "GET":
        from gemini_export.prompt_templates import (
            _extract_variables,
            _resolve_prompt_dir,
        )

        content = get_prompt(name)
        prompt_dir = _resolve_prompt_dir()
        file_path = prompt_dir / f"{name}.txt"
        custom = file_path.exists()
        variables = _extract_variables(content or "")

        return jsonify({
            "name": name,
            "custom": custom,
            "path": str(file_path) if custom else None,
            "content": content,
            "variables": variables,
        })

    if request.method == "POST":
        data = request.get_json(force=True)
        content = data.get("content", "").strip()
        if not content:
            return jsonify({"error": "A content mező kötelező."}), 400

        try:
            ok = save_prompt(name, content)
            return jsonify({"saved": ok, "name": name})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    if request.method == "DELETE":
        try:
            ok = reset_prompt(name)
            return jsonify({"reset": ok, "name": name})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    return jsonify({"error": "Method not allowed"}), 405


# ─── Export History & Presets API ──────────────────────────────────

@app.route("/api/exports/history")
def api_exports_history():
    """Korábbi export session-ök listája."""
    conn = _get_manifest()
    try:
        rows = conn.execute(
            """SELECT id, task_id, started_at, finished_at, output_dir, format,
                      total_chats, exported, failed, skipped, settings_json
               FROM export_history
               ORDER BY started_at DESC
               LIMIT 50"""
        ).fetchall()
        history = []
        for row in rows:
            hid, tid, started, finished, out_dir, fmt, total, exp, fail, skip, settings_json = row
            settings = {}
            if settings_json:
                try:
                    settings = json.loads(settings_json)
                except json.JSONDecodeError:
                    pass
            history.append({
                "id": hid,
                "task_id": tid,
                "started_at": started,
                "finished_at": finished,
                "output_dir": out_dir,
                "format": fmt,
                "total_chats": total or 0,
                "exported": exp or 0,
                "failed": fail or 0,
                "skipped": skip or 0,
                "settings": settings,
            })
        return jsonify(history)
    finally:
        conn.close()


@app.route("/api/exports/presets", methods=["GET", "POST"])
def api_exports_presets():
    """Export preset-ek listázása és mentése."""
    conn = _get_manifest()
    try:
        if request.method == "GET":
            rows = conn.execute(
                """SELECT id, name, settings_json, created_at, last_used_at
                   FROM export_presets
                   ORDER BY last_used_at DESC"""
            ).fetchall()
            presets = []
            for row in rows:
                pid, name, settings_json, created, last_used = row
                try:
                    settings = json.loads(settings_json) if settings_json else {}
                except json.JSONDecodeError:
                    settings = {}
                presets.append({
                    "id": pid,
                    "name": name,
                    "settings": settings,
                    "created_at": created,
                    "last_used_at": last_used,
                })
            return jsonify(presets)

        # POST: new preset
        data = request.get_json(force=True)
        name = (data.get("name", "") or "").strip()
        if not name:
            return jsonify({"error": "A név megadása kötelező."}), 400
        settings = data.get("settings", {})
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        try:
            conn.execute(
                """INSERT INTO export_presets (name, settings_json, created_at)
                   VALUES (?, ?, ?)""",
                (name, json.dumps(settings), now),
            )
            conn.commit()
            new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            return jsonify({"id": new_id, "name": name, "settings": settings, "created_at": now}), 201
        except Exception as e:
            if "UNIQUE" in str(e):
                return jsonify({"error": "Már létezik ilyen nevű preset."}), 409
            raise
    finally:
        conn.close()


@app.route("/api/exports/presets/<int:preset_id>", methods=["DELETE"])
def api_exports_preset_delete(preset_id: int):
    """Preset törlése."""
    conn = _get_manifest()
    try:
        conn.execute("DELETE FROM export_presets WHERE id = ?", (preset_id,))
        conn.commit()
        return jsonify({"deleted": preset_id})
    finally:
        conn.close()


# ─── Batch műveletek ─────────────────────────────────────────────────────────────────────

@app.route("/api/chat/batch/tags", methods=["POST"])
def api_batch_tags():
    """Tömeges címkézés: címkék hozzáadása több chat-hez."""
    data = request.get_json(force=True)
    cids = data.get("cids", [])
    tags = data.get("tags", [])
    if not cids or not tags:
        return jsonify({"error": "cids és tags kötelező."}), 400
    conn = _get_manifest()
    try:
        for cid in cids:
            _add_tags(conn, cid, tags)
        return jsonify({"tagged": len(cids), "tags": tags})
    finally:
        conn.close()


@app.route("/api/chat/batch/delete", methods=["POST"])
def api_batch_delete():
    """Tömeges törlés: chat-ek eltávolítása a manifestből és az FTS indexből."""
    data = request.get_json(force=True)
    cids = data.get("cids", [])
    if not cids:
        return jsonify({"error": "cids kötelező."}), 400

    conn = _get_manifest()
    try:
        placeholders = ",".join("?" for _ in cids)
        # FTS index törlése
        conn.execute(
            f"DELETE FROM chats_fts WHERE chat_id IN ({placeholders})", cids
        )
        # Kapcsolódó táblák törlése (CASCADE nélküli SQLite miatt manuálisan)
        for table in ("chat_embeddings", "chat_todos", "chat_metadata"):
            try:
                conn.execute(
                    f"DELETE FROM {table} WHERE chat_id IN ({placeholders})",
                    cids,
                )
            except sqlite3.OperationalError:
                pass  # A tábla lehet, hogy nem létezik
        # Export rekord törlése
        conn.execute(
            f"DELETE FROM exports WHERE chat_id IN ({placeholders})", cids
        )
        conn.commit()
        logger.info("Batch delete: %d chat törölve.", len(cids))
        return jsonify({"deleted": len(cids)})
    finally:
        conn.close()


@app.route("/api/chat/batch/ai-analyze", methods=["POST"])
@limiter.limit("5 per minute")
def api_batch_ai_analyze():
    """Tömeges AI elemzés: kijelölt chat-ek elemzése SSE streaming progress-szel."""
    data = request.get_json(force=True)
    cids = data.get("cids", [])
    if not cids:
        return jsonify({"error": "cids kötelező."}), 400

    try:
        from ai_layer import (
            _ensure_ai_schema,
            analyze_chat_from_json,
        )
    except ImportError:
        return jsonify({"error": "AI layer not available."}), 503

    conn = _get_manifest()
    output_dir = Path(DEFAULT_OUTPUT)
    _ensure_ai_schema(conn)

    def generate():
        total = len(cids)
        analyzed = 0
        failed = 0
        skipped = 0
        try:
            yield f"event: start\ndata: {{\"total\": {total}}}\n\n"
            for i, cid in enumerate(cids):
                chat_data = _get_chat_data(cid)
                if not chat_data:
                    skipped += 1
                    yield (
                        f"event: progress\n"
                        f"data: {{\"current\": {i + 1}, \"total\": {total},"
                        f" \"cid\": \"{cid}\", \"status\": \"skipped\"}}\n\n"
                    )
                    continue

                try:
                    result = analyze_chat_from_json(conn, output_dir, cid)
                    if result.get("error"):
                        failed += 1
                        status = "failed"
                    else:
                        analyzed += 1
                        status = "done"
                except Exception as e:
                    failed += 1
                    status = "failed"
                    result = {"error": str(e)}

                yield (
                    f"event: progress\n"
                    f"data: {{\"current\": {i+1},"
                    f" \"total\": {total},"
                    f" \"cid\": \"{cid}\","
                    f" \"status\": \"{status}\","
                    f" \"title\":"
                    f" {json.dumps(chat_data.get('title','')[:60])},"
                    f" \"analyzed\": {analyzed},"
                    f" \"failed\": {failed},"
                    f" \"skipped\": {skipped}""}}\n\n"
                )

                if status == "done":
                    yield (
                        f"event: result\n"
                        f"data: {{\"cid\": \"{cid}\","
                        f" \"tags\": {json.dumps(result.get('tags', []))}""}}\n\n"
                    )

            yield (
                f"event: done\n"
                f"data: {{\"total\": {total}, \"analyzed\": {analyzed},"
                f" \"failed\": {failed}, \"skipped\": {skipped}""}}\n\n"
            )
        except Exception as e:
            yield f"event: error\ndata: {json.dumps(str(e))}\n\n"
        finally:
            conn.close()

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    start_cleanup_scheduler()

    # Export ütemező inicializálása
    try:
        from gemini_export.scheduler import init_scheduler, SCHEDULER_AVAILABLE
        if SCHEDULER_AVAILABLE:
            init_scheduler(output_dir=DEFAULT_OUTPUT)
            logger.info("Export scheduler: initialized")
        else:
            logger.info("Export scheduler: APScheduler not installed — skipping")
    except Exception as e:
        logger.warning("Export scheduler init failed: %s", e)

    logger.info("=" * 50)
    logger.info("Gemini Chat Exporter — Web GUI")
    logger.info("=" * 50)
    logger.info("Nyisd meg a böngészőben: http://localhost:5000")
    if AUTH_ENABLED:
        logger.info("Dashboard auth: BEKAPCSOLVA (DASHBOARD_ACCESS_TOKEN)")
    else:
        logger.info("Dashboard auth: KIKAPCSOLVA (nincs DASHBOARD_ACCESS_TOKEN)")
    logger.info("Kilépés: Ctrl+C")
    logger.info("=" * 50)
    app.run(debug=True, host="127.0.0.1", port=5000, threaded=True)
