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

import os
import uuid
import subprocess
import threading
import queue
import time
from pathlib import Path

from flask import Flask, render_template, request, jsonify, Response

# ─── Inicializálás ───────────────────────────────────────────────────────────

app = Flask(__name__)

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
