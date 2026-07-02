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
import sqlite3
import sys
import uuid
import subprocess
import threading
import queue
import time
from pathlib import Path

from flask import Flask, render_template, request, jsonify, Response

# Manifest függvények importálása (az export.py nehéz függőségei miatt try/except)
_init_manifest = _search_chats = _list_tags = _add_tags = _set_project = _toggle_favorite = _manifest_get_stats = _reindex_all_chats = None
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from export import (
        _init_manifest, _search_chats, _list_tags,
        _add_tags, _set_project, _toggle_favorite,
        _manifest_get_stats, _reindex_all_chats,
    )
except ImportError:
    pass  # A manifest funkciók nem elérhetőek, de az export toolok igen

# ─── Inicializálás ───────────────────────────────────────────────────────────

app = Flask(__name__)

# Alapértelmezett output könyvtár
DEFAULT_OUTPUT = "./exports"


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


# ─── Route-ok ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Főoldal — a GUI megjelenítése."""
    return render_template("index.html")


@app.route("/start", methods=["POST"])
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
def dashboard():
    """Dashboard főoldal — tudástár böngésző."""
    return render_template("dashboard.html")


@app.route("/api/chats")
def api_chats():
    """Chat lista a manifestből metaadatokkal."""
    conn = _get_manifest()
    try:
        rows = conn.execute("""
            SELECT e.chat_id, e.title, e.last_exported_at, e.message_count, e.status, e.image_count,
                   m.tags, m.project, m.is_favorite, m.processing_status, m.notes
            FROM exports e
            LEFT JOIN chat_metadata m ON e.chat_id = m.chat_id
            ORDER BY e.last_exported_at DESC
            LIMIT 500
        """).fetchall()

        chats = []
        for row in rows:
            cid, title, exported_at, msg_count, status, img_count, tags_json, project, fav, proc, notes = row
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
                "notes": notes,
            })
        return jsonify(chats)
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
    """FTS5 keresés."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    conn = _get_manifest()
    try:
        results = _search_chats(conn, q, limit=100)
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
            from export import _ensure_metadata_row
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


@app.route("/api/reindex", methods=["POST"])
def api_reindex():
    """Újraindexeli a chat-eket."""
    conn = _get_manifest()
    try:
        count = _reindex_all_chats(conn, Path(DEFAULT_OUTPUT))
        return jsonify({"reindexed": count})
    finally:
        conn.close()


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    start_cleanup_scheduler()
    print("=" * 60)
    print("  Gemini Chat Exporter — Web GUI")
    print("=" * 60)
    print(f"  Nyisd meg a böngészőben: http://localhost:5000")
    print(f"  Kilépés: Ctrl+C")
    print("=" * 60)
    app.run(debug=True, host="127.0.0.1", port=5000, threaded=True)
