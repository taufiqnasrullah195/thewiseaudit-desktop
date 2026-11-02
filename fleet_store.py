"""
fleet_store.py — persistence layer: SQLite history for local audits and
fleet scans, plus diff-against-previous-run.

Storage lives in %LOCALAPPDATA%/TheWiseAudit/thewiseaudit.db (created on first
use). All writes are parameterized; connection is per-call (SQLite handles
concurrency via file locks — adequate for a single-user desktop tool).

Pure logic, no Tkinter: unit-testable headless.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime

from fleet_core import AuditResult
import fleet_multi

# Explicit override for tests; None -> per-user appdata location.
_DB_PATH_OVERRIDE: str | None = None


def _db_path() -> str:
    if _DB_PATH_OVERRIDE:
        return _DB_PATH_OVERRIDE
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    folder = os.path.join(base, "TheWiseAudit")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "thewiseaudit.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute("PRAGMA journal_mode=WAL")  # readers never block the writer
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at TEXT NOT NULL,
            hostname TEXT NOT NULL,
            worst_severity TEXT NOT NULL,
            cpu_percent REAL,
            ram_percent REAL,
            uptime_days REAL,
            findings_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS fleet_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at TEXT NOT NULL,
            host TEXT NOT NULL,
            status TEXT NOT NULL,
            latency_ms INTEGER,
            open_ports TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_scan_time ON scan_history(scanned_at);
        CREATE INDEX IF NOT EXISTS idx_fleet_time ON fleet_history(scanned_at);
        """)


def save_audit(res: AuditResult) -> int:
    init_db()
    s = res.system
    findings = [
        {"check": f.check, "severity": f.severity, "detail": f.detail}
        for f in res.findings
    ]
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO scan_history (scanned_at, hostname, worst_severity,"
            " cpu_percent, ram_percent, uptime_days, findings_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (res.scanned_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             s.get("hostname", "?"), res.worst_severity,
             s.get("cpu_percent", 0), s.get("ram_percent", 0),
             s.get("uptime_days", 0), json.dumps(findings)))
        return cur.lastrowid


def save_fleet(results: list[fleet_multi.HostResult]) -> int:
    init_db()
    scanned_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        # One row per host, sharing one timestamp.
        conn.executemany(
            "INSERT INTO fleet_history (scanned_at, host, status,"
            " latency_ms, open_ports) VALUES (?, ?, ?, ?, ?)",
            [(scanned_at, r.host, r.status, r.latency_ms,
              ";".join(r.open_ports)) for r in results])
    return len(results)


def last_runs(n: int = 20) -> list[dict]:
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, scanned_at, hostname, worst_severity, cpu_percent,"
            " ram_percent, uptime_days FROM scan_history"
            " ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    return [dict(r) for r in rows]


def delete_run(run_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM scan_history WHERE id = ?", (run_id,))


def diff_vs_prev(res: AuditResult) -> list[str]:
    """Human-readable lines describing what changed vs the previous scan
    of the same hostname: new findings, resolved findings."""
    init_db()
    with _connect() as conn:
        # res has NOT been saved yet — the latest stored row IS the
        # previous run for this hostname.
        row = conn.execute(
            "SELECT findings_json FROM scan_history"
            " WHERE hostname = ?"
            " ORDER BY id DESC LIMIT 1",
            (res.system.get("hostname", "?"),)
        ).fetchone()
    if not row:
        return []
    prev = {(f["check"], f["severity"]): f["detail"]
            for f in json.loads(row[0])}
    curr = {(f.check, f.severity): f.detail for f in res.findings}
    lines: list[str] = []
    for key, detail in curr.items():
        if key not in prev:
            lines.append(f"NEW: {key[0]} = {key[1]} ({detail})")
    for key in prev:
        if key not in curr:
            lines.append(f"RESOLVED: {key[0]} {key[1]}")
    return lines


def history_to_csv() -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "scanned_at", "hostname", "worst_severity",
                "cpu_percent", "ram_percent", "uptime_days"])
    for r in last_runs(1000):
        w.writerow([r["id"], r["scanned_at"], r["hostname"],
                    r["worst_severity"], r["cpu_percent"],
                    r["ram_percent"], r["uptime_days"]])
    return buf.getvalue()
