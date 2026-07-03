#!/usr/bin/env python3
"""
Export ütemezés — APScheduler + manifest DB persistencia
==========================================================
Cron-szerű időzített exportálás háttérszálban, a Flask app részeként.

Használat:
    from gemini_export.scheduler import init_scheduler, get_scheduler

    scheduler = init_scheduler(output_dir="./exports")
    scheduler.add_cron("0 */6 * * *", format="all")

API:
    add_cron(cron_expr, **export_kwargs) -> job_id
    remove_job(job_id) -> bool
    list_jobs() -> list[dict]
    pause_job(job_id) / resume_job(job_id)
    run_job_now(job_id) -> bool
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("gemini_export.scheduler")

# ─── APScheduler import (lazy, nem blokkoló ha nincs telepítve) ─────────────

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.jobstores.base import JobLookupError

    SCHEDULER_AVAILABLE = True
except ImportError:
    SCHEDULER_AVAILABLE = False
    BackgroundScheduler = None  # type: ignore
    CronTrigger = None  # type: ignore
    JobLookupError = Exception  # type: ignore


# ─── Scheduler singleton ─────────────────────────────────────────────────────

_scheduler: ExportScheduler | None = None
_scheduler_lock = threading.Lock()


def init_scheduler(
    output_dir: str | Path = "./exports",
    manifest_path: str | Path | None = None,
) -> "ExportScheduler | None":
    """Inicializálja az export ütemezőt (thread-safe singleton).

    Ha az APScheduler nincs telepítve, None-t ad vissza.
    Az EXPORT_SCHEDULE környezeti változó alapján automatikusan hozzáad
    egy alapértelmezett cron job-ot (pl. "0 */12 * * *").

    Args:
        output_dir: Az export kimeneti könyvtára.
        manifest_path: A manifest DB elérési útja (alap: output_dir/manifest.db).

    Returns:
        ExportScheduler példány, vagy None ha nem elérhető.
    """
    global _scheduler
    if not SCHEDULER_AVAILABLE:
        logger.warning("APScheduler not installed — scheduling disabled.")
        return None

    with _scheduler_lock:
        if _scheduler is not None:
            return _scheduler

        output_dir = Path(output_dir)
        if manifest_path is None:
            manifest_path = output_dir / "manifest.db"
        manifest_path = Path(manifest_path)

        _scheduler = ExportScheduler(output_dir, manifest_path)
        _scheduler.start()

        # Automatikus cron job az EXPORT_SCHEDULE env var-ból
        env_schedule = os.getenv("EXPORT_SCHEDULE", "").strip()
        if env_schedule:
            try:
                CronTrigger.from_crontab(env_schedule)  # validálás
                _scheduler.add_cron(
                    env_schedule,
                    format="all",
                    auto_added=True,
                    label="Alapértelmezett (EXPORT_SCHEDULE)",
                )
                logger.info("Auto-schedule from EXPORT_SCHEDULE: %s", env_schedule)
            except Exception as e:
                logger.warning("Invalid EXPORT_SCHEDULE '%s': %s", env_schedule, e)

    return _scheduler


def get_scheduler() -> "ExportScheduler | None":
    """Visszaadja az aktuális scheduler példányt (None ha nincs init)."""
    return _scheduler


def shutdown_scheduler() -> None:
    """Leállítja a schedulert (graceful shutdown)."""
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None:
            _scheduler.shutdown()
            _scheduler = None


# ─── Desktop notification ────────────────────────────────────────────────────

def _send_desktop_notification(title: str, message: str) -> None:
    """Asztali értesítés küldése platform-függő módon.

    Windows: PowerShell toast, macOS: osascript, Linux: notify-send.
    Ha egyik sem elérhető, csak log-ol.
    """
    try:
        if sys.platform == "win32":
            # PowerShell toast notification (Windows 10+)
            safe_title = title.replace("'", "''")
            safe_message = message.replace("'", "''")
            subprocess.run(
                [
                    "powershell",
                    "-Command",
                    f"& {{Add-Type -AssemblyName System.Windows.Forms; "
                    f"$n = New-Object System.Windows.Forms.NotifyIcon; "
                    f"$n.Icon = [System.Drawing.SystemIcons]::Information; "
                    f"$n.BalloonTipTitle = '{safe_title}'; "
                    f"$n.BalloonTipText = '{safe_message}'; "
                    f"$n.Visible = $true; "
                    f"$n.ShowBalloonTip(5000); "
                    f"Start-Sleep -Seconds 6; "
                    f"$n.Dispose()}}",
                ],
                capture_output=True,
                timeout=10,
            )
        elif sys.platform == "darwin":
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{message}" with title "{title}"'],
                capture_output=True,
                timeout=5,
            )
        else:
            # Linux: notify-send
            subprocess.run(
                ["notify-send", title, message],
                capture_output=True,
                timeout=5,
            )
    except Exception:
        pass  # Silent fail — a log már tartalmazza az infót


# ─── ExportScheduler ─────────────────────────────────────────────────────────

class ExportScheduler:
    """Export ütemező: APScheduler wrapper manifest DB persistencia réteggel.

    A job konfigurációkat az export_schedules SQLite táblában tárolja,
    így a scheduler újraindítás után is visszaállítja az ütemezéseket.
    """

    def __init__(self, output_dir: Path, manifest_path: Path):
        self._output_dir = output_dir
        self._manifest_path = manifest_path
        self._running_jobs: dict[str, subprocess.Popen] = {}
        self._jobs_lock = threading.Lock()

        # APScheduler példány
        self._aps = BackgroundScheduler(
            job_defaults={
                "coalesce": True,  # Ha lemaradt egy futtatás, csak egyszer fusson
                "max_instances": 1,  # Egy job-ból egyszerre csak egy példány
                "misfire_grace_time": 300,  # 5 perc türelmi idő
            },
        )

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Elindítja a schedulert és visszatölti a mentett job-okat."""
        self._ensure_schema()
        self._load_saved_jobs()
        self._aps.start()
        logger.info("Export scheduler started with %d job(s).",
                     len(self._aps.get_jobs()))

    def shutdown(self) -> None:
        """Leállítja a schedulert (várakozik a futó job-okra)."""
        # Futó export process-ek terminálása
        with self._jobs_lock:
            jobs_snapshot = list(self._running_jobs.items())
        for job_id, proc in jobs_snapshot:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        with self._jobs_lock:
            self._running_jobs.clear()

        self._aps.shutdown(wait=True)
        logger.info("Export scheduler shut down.")

    # ── Schema ───────────────────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        """Létrehozza az export_schedules táblát, ha még nem létezik."""
        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._manifest_path))
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS export_schedules (
                    id TEXT PRIMARY KEY,
                    cron_expr TEXT NOT NULL,
                    label TEXT,
                    format TEXT DEFAULT 'both',
                    output_dir TEXT,
                    delay REAL DEFAULT 0.5,
                    max_chats INTEGER DEFAULT 2000,
                    concurrency INTEGER DEFAULT 3,
                    no_resume INTEGER DEFAULT 0,
                    auto_cookies INTEGER DEFAULT 0,
                    from_date TEXT,
                    to_date TEXT,
                    keyword_filter TEXT,
                    template TEXT DEFAULT 'dark',
                    enabled INTEGER DEFAULT 1,
                    auto_added INTEGER DEFAULT 0,
                    created_at TEXT,
                    last_run_at TEXT,
                    last_result TEXT
                )
            """)
            conn.commit()
        finally:
            conn.close()

    # ── Persistence ──────────────────────────────────────────────────────

    def _load_saved_jobs(self) -> None:
        """Visszatölti a mentett job-okat a DB-ből."""
        conn = sqlite3.connect(str(self._manifest_path))
        try:
            # Explicit oszloplista a sorrend-függőség elkerülésére
            rows = conn.execute(
                """SELECT id, cron_expr, label, format, output_dir, delay,
                          max_chats, concurrency, no_resume, auto_cookies,
                          from_date, to_date, keyword_filter, template,
                          enabled, auto_added, created_at, last_run_at, last_result
                   FROM export_schedules WHERE enabled = 1"""
            ).fetchall()
            col_names = [
                "id", "cron_expr", "label", "format", "output_dir", "delay",
                "max_chats", "concurrency", "no_resume", "auto_cookies",
                "from_date", "to_date", "keyword_filter", "template",
                "enabled", "auto_added", "created_at", "last_run_at", "last_result",
            ]
        finally:
            conn.close()

        if not rows:
            return

        for row in rows:
            cfg = dict(zip(col_names, row))
            try:
                self._add_job_to_scheduler(cfg)
            except Exception as e:
                logger.warning("Failed to restore job %s: %s", cfg.get("id"), e)

    def _save_job_config(self, job_id: str, config: dict) -> None:
        """Menti (INSERT OR REPLACE) egy job konfigurációját a DB-be."""
        conn = sqlite3.connect(str(self._manifest_path))
        try:
            conn.execute("""
                INSERT OR REPLACE INTO export_schedules
                (id, cron_expr, label, format, output_dir, delay, max_chats,
                 concurrency, no_resume, auto_cookies, from_date, to_date,
                 keyword_filter, template, enabled, auto_added, created_at,
                 last_run_at, last_result)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_id,
                config.get("cron_expr", ""),
                config.get("label"),
                config.get("format", "both"),
                config.get("output_dir"),
                config.get("delay", 0.5),
                config.get("max_chats", 2000),
                config.get("concurrency", 3),
                int(config.get("no_resume", False)),
                int(config.get("auto_cookies", False)),
                config.get("from_date"),
                config.get("to_date"),
                config.get("keyword_filter"),
                config.get("template", "dark"),
                int(config.get("enabled", True)),
                int(config.get("auto_added", False)),
                config.get("created_at") or datetime.now(timezone.utc).isoformat(),
                config.get("last_run_at"),
                config.get("last_result"),
            ))
            conn.commit()
        finally:
            conn.close()

    def _update_job_run_result(self, job_id: str, result: str) -> None:
        """Frissíti egy job utolsó futtatásának eredményét."""
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(self._manifest_path))
        try:
            conn.execute(
                "UPDATE export_schedules SET last_run_at = ?, last_result = ? WHERE id = ?",
                (now, result, job_id),
            )
            conn.commit()
        finally:
            conn.close()

    # ── Public API ───────────────────────────────────────────────────────

    def add_cron(
        self,
        cron_expr: str,
        *,
        format: str = "both",
        label: str | None = None,
        output_dir: str | None = None,
        delay: float = 0.5,
        max_chats: int = 2000,
        concurrency: int = 3,
        no_resume: bool = False,
        auto_cookies: bool = False,
        from_date: str | None = None,
        to_date: str | None = None,
        keyword_filter: str | None = None,
        template: str = "dark",
        auto_added: bool = False,
    ) -> str:
        """Új cron job hozzáadása.

        Args:
            cron_expr: Cron kifejezés (pl. "0 */6 * * *").
            format: Export formátum ("json", "markdown", "html", "csv", "pdf", "all", "both").
            label: Emberi olvasható név.
            output_dir: Kimeneti könyvtár (alap: self._output_dir).
            delay, max_chats, concurrency, no_resume, auto_cookies,
            from_date, to_date, keyword_filter, template: export.py paraméterek.

        Returns:
            A létrehozott job egyedi azonosítója.

        Raises:
            ValueError: Érvénytelen cron kifejezés esetén.
        """
        # Cron kifejezés validálása
        CronTrigger.from_crontab(cron_expr)

        job_id = str(uuid.uuid4())[:12]
        od = output_dir or str(self._output_dir)

        config = {
            "cron_expr": cron_expr,
            "label": label or f"Export ({cron_expr})",
            "format": format,
            "output_dir": od,
            "delay": delay,
            "max_chats": max_chats,
            "concurrency": concurrency,
            "no_resume": no_resume,
            "auto_cookies": auto_cookies,
            "from_date": from_date,
            "to_date": to_date,
            "keyword_filter": keyword_filter,
            "template": template,
            "enabled": True,
            "auto_added": auto_added,
            "id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        self._add_job_to_scheduler(config)
        self._save_job_config(job_id, config)

        logger.info("Schedule job added: %s (%s)", job_id, cron_expr)
        return job_id

    def _add_job_to_scheduler(self, config: dict) -> None:
        """Hozzáad egy job-ot az APScheduler-hez a konfiguráció alapján."""
        job_id = config["id"]
        cron_expr = config["cron_expr"]

        # Export kwargs összeállítása
        export_kwargs = {
            k: config[k]
            for k in (
                "format", "output_dir", "delay", "max_chats", "concurrency",
                "no_resume", "auto_cookies", "from_date", "to_date",
                "keyword_filter", "template",
            )
            if k in config and config[k] is not None
        }

        self._aps.add_job(
            func=self._run_scheduled_export,
            trigger=CronTrigger.from_crontab(cron_expr),
            args=[job_id],
            kwargs=export_kwargs,
            id=job_id,
            name=config.get("label", job_id),
            replace_existing=True,
        )

    def remove_job(self, job_id: str) -> bool:
        """Job eltávolítása a scheduler-ből és a DB-ből.

        Returns:
            True ha a job létezett és törölve lett.
        """
        try:
            self._aps.remove_job(job_id)
        except JobLookupError:
            # DB-ből akkor is töröljük, ha a scheduler-ben nincs meg
            pass
        except Exception:
            pass

        conn = sqlite3.connect(str(self._manifest_path))
        try:
            cursor = conn.execute(
                "DELETE FROM export_schedules WHERE id = ?", (job_id,)
            )
            conn.commit()
            deleted = cursor.rowcount > 0
        finally:
            conn.close()

        if deleted:
            logger.info("Schedule job removed: %s", job_id)
        return deleted

    def pause_job(self, job_id: str) -> bool:
        """Job szüneteltetése."""
        try:
            self._aps.pause_job(job_id)
            self._set_job_enabled(job_id, False)
            logger.info("Schedule job paused: %s", job_id)
            return True
        except JobLookupError:
            return False

    def resume_job(self, job_id: str) -> bool:
        """Job folytatása."""
        try:
            self._aps.resume_job(job_id)
            self._set_job_enabled(job_id, True)
            logger.info("Schedule job resumed: %s", job_id)
            return True
        except JobLookupError:
            return False

    def _set_job_enabled(self, job_id: str, enabled: bool) -> None:
        """Frissíti az enabled flag-et a DB-ben."""
        conn = sqlite3.connect(str(self._manifest_path))
        try:
            conn.execute(
                "UPDATE export_schedules SET enabled = ? WHERE id = ?",
                (int(enabled), job_id),
            )
            conn.commit()
        finally:
            conn.close()

    def list_jobs(self) -> list[dict[str, Any]]:
        """Összes ütemezett job listázása (DB + APScheduler állapot)."""
        conn = sqlite3.connect(str(self._manifest_path))
        try:
            rows = conn.execute(
                """SELECT id, cron_expr, label, format, output_dir, delay,
                          max_chats, concurrency, no_resume, auto_cookies,
                          from_date, to_date, keyword_filter, template,
                          enabled, auto_added, created_at, last_run_at, last_result
                   FROM export_schedules ORDER BY created_at DESC"""
            ).fetchall()
            col_names = [
                "id", "cron_expr", "label", "format", "output_dir", "delay",
                "max_chats", "concurrency", "no_resume", "auto_cookies",
                "from_date", "to_date", "keyword_filter", "template",
                "enabled", "auto_added", "created_at", "last_run_at", "last_result",
            ]
        finally:
            conn.close()

        jobs = []
        for row in rows:
            cfg = dict(zip(col_names, row))
            job_id = cfg["id"]

            # APScheduler státusz
            aps_job = self._aps.get_job(job_id)
            next_run = None
            if aps_job and aps_job.next_run_time:
                next_run = aps_job.next_run_time.isoformat()

            pending = aps_job is not None and aps_job.next_run_time is not None

            jobs.append({
                "id": job_id,
                "cron_expr": cfg["cron_expr"],
                "label": cfg["label"],
                "format": cfg["format"],
                "output_dir": cfg["output_dir"],
                "delay": cfg["delay"],
                "max_chats": cfg["max_chats"],
                "concurrency": cfg["concurrency"],
                "no_resume": bool(cfg["no_resume"]),
                "auto_cookies": bool(cfg["auto_cookies"]),
                "from_date": cfg["from_date"],
                "to_date": cfg["to_date"],
                "keyword_filter": cfg["keyword_filter"],
                "template": cfg["template"],
                "enabled": bool(cfg["enabled"]),
                "auto_added": bool(cfg["auto_added"]),
                "created_at": cfg["created_at"],
                "last_run_at": cfg["last_run_at"],
                "last_result": cfg["last_result"],
                "next_run_at": next_run,
                "pending": pending,
            })

        return jobs

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Egy job részletes adatainak lekérése."""
        jobs = self.list_jobs()
        for job in jobs:
            if job["id"] == job_id:
                return job
        return None

    def run_job_now(self, job_id: str) -> bool:
        """Job azonnali futtatása (a cron ütemezéstől függetlenül)."""
        job = self.get_job(job_id)
        if not job:
            return False

        # Külön szálban futtatjuk, hogy ne blokkoljuk a schedulert
        export_kwargs = {
            k: job[k]
            for k in (
                "format", "output_dir", "delay", "max_chats", "concurrency",
                "no_resume", "auto_cookies", "from_date", "to_date",
                "keyword_filter", "template",
            )
        }

        thread = threading.Thread(
            target=self._run_scheduled_export,
            args=[job_id],
            kwargs=export_kwargs,
            daemon=True,
        )
        thread.start()
        logger.info("Schedule job run-now: %s", job_id)
        return True

    # ── Export execution ─────────────────────────────────────────────────

    def _run_scheduled_export(self, job_id: str, **export_kwargs: Any) -> None:
        """Végrehajt egy ütemezett exportálást: subprocess-ként futtatja az export.py-t.

        Az export.py kimenetét log-olja, és a befejezés után frissíti a DB státuszt.
        """
        start_time = time.time()
        fmt = export_kwargs.get("format", "both")
        od = export_kwargs.get("output_dir", str(self._output_dir))

        logger.info("Scheduled export started: job=%s format=%s output=%s",
                     job_id, fmt, od)

        # Parancssor összeállítása (hasonló az app.py /start endpoint-hoz)
        cmd = [sys.executable, "-u", "export.py"]

        if export_kwargs.get("auto_cookies"):
            cmd.append("--auto-cookies")

        cmd.extend(["--format", fmt])
        cmd.extend(["--output", od])
        cmd.extend(["--delay", str(export_kwargs.get("delay", 0.5))])

        if export_kwargs.get("no_resume"):
            cmd.append("--no-resume")

        max_chats = export_kwargs.get("max_chats", 2000)
        cmd.extend(["--max-chats", str(max_chats)])

        concurrency = export_kwargs.get("concurrency", 3)
        cmd.extend(["--concurrency", str(concurrency)])

        if export_kwargs.get("from_date"):
            cmd.extend(["--from", export_kwargs["from_date"]])
        if export_kwargs.get("to_date"):
            cmd.extend(["--to", export_kwargs["to_date"]])
        if export_kwargs.get("keyword_filter"):
            cmd.extend(["--filter", export_kwargs["keyword_filter"]])

        template = export_kwargs.get("template", "dark")
        if template and "html" in fmt:
            cmd.extend(["--template", template])

        # Környezeti változók
        env = os.environ.copy()

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                cwd=Path(__file__).parent.parent,
            )
            with self._jobs_lock:
                self._running_jobs[job_id] = proc

            # Kimenet olvasása és log-olása
            output_lines = []
            for line in iter(proc.stdout.readline, ""):
                line = line.rstrip()
                if line:
                    output_lines.append(line)
                    logger.debug("[scheduled:%s] %s", job_id[:8], line)

            proc.stdout.close()
            returncode = proc.wait()
            elapsed = time.time() - start_time

        except FileNotFoundError:
            returncode = -1
            elapsed = time.time() - start_time
            logger.error("Scheduled export failed: export.py not found (job=%s)", job_id)
        except Exception as e:
            returncode = -1
            elapsed = time.time() - start_time
            logger.error("Scheduled export error (job=%s): %s", job_id, e)
        finally:
            with self._jobs_lock:
                self._running_jobs.pop(job_id, None)

        # Eredmény rögzítése
        result = f"returncode={returncode} elapsed={elapsed:.1f}s"
        self._update_job_run_result(job_id, result)

        # Értesítés
        job_cfg = self.get_job(job_id)
        job_label = job_cfg.get("label", job_id) if job_cfg else job_id

        if returncode == 0:
            logger.info("Scheduled export completed: job=%s duration=%.1fs",
                        job_id, elapsed)
            _send_desktop_notification(
                "Export kész",
                f"{job_label}: sikeresen befejeződött ({elapsed:.0f}s)",
            )
        else:
            logger.warning("Scheduled export failed: job=%s returncode=%d duration=%.1fs",
                           job_id, returncode, elapsed)
            _send_desktop_notification(
                "Export hiba",
                f"{job_label}: sikertelen (kód: {returncode})",
            )
