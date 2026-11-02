"""test_fleet_store.py — persistence layer: save, history, delete, diff.
Uses a temp DB via the override hook so tests never touch real appdata."""
import sqlite3
import tempfile
from pathlib import Path

import pytest

import fleet_core as fc
import fleet_multi as fm
import fleet_store as fs


@pytest.fixture()
def temp_db(monkeypatch):
    path = Path(tempfile.mkdtemp()) / "test.db"
    monkeypatch.setattr(fs, "_DB_PATH_OVERRIDE", str(path))
    fs.init_db()
    return path


def _audit(hostname="BOX1", cpu=50.0, warn=False):
    res = fc.AuditResult(scanned_at="2026-11-02 12:00:00")
    res.system = {"hostname": hostname, "cpu_percent": cpu,
                  "ram_percent": 40.0, "ram_total_gb": 16,
                  "uptime_days": 1.0, "boot_time": "2026-11-01 00:00"}
    res.storage = []
    res.top_processes = []
    res.findings = [fc.Finding("CPU load", "WARNING" if warn else "OK",
                               "detail")]
    return res


def test_save_and_last_runs(temp_db):
    fs.save_audit(_audit())
    fs.save_audit(_audit(cpu=80.0))
    runs = fs.last_runs(10)
    assert len(runs) == 2
    assert runs[0]["cpu_percent"] == 80.0  # newest first
    assert runs[1]["hostname"] == "BOX1"


def test_diff_first_run_empty(temp_db):
    assert fs.diff_vs_prev(_audit()) == []


def test_diff_detects_new_and_resolved(temp_db):
    fs.save_audit(_audit(warn=False))
    # Next scan: warning appeared.
    diffs = fs.diff_vs_prev(_audit(warn=True))
    assert any(d.startswith("NEW:") and "WARNING" in d for d in diffs)
    assert any(d.startswith("RESOLVED:") and "OK" in d for d in diffs)


def test_delete_run(temp_db):
    rid = fs.save_audit(_audit())
    fs.delete_run(rid)
    assert fs.last_runs(10) == []


def test_save_fleet_and_rows(temp_db):
    results = [fm.HostResult("h1", "up", 3, ["RDP"], []),
               fm.HostResult("h2", "down", None, [], ["RDP"])]
    n = fs.save_fleet(results)
    assert n == 2
    conn = sqlite3.connect(temp_db)
    rows = conn.execute("SELECT host, status FROM fleet_history"
                        " ORDER BY host").fetchall()
    conn.close()
    assert rows == [("h1", "up"), ("h2", "down")]


def test_history_csv_roundtrip(temp_db):
    fs.save_audit(_audit())
    text = fs.history_to_csv()
    lines = text.strip().splitlines()
    assert lines[0].startswith("id,scanned_at,hostname")
    assert "BOX1" in lines[1]


def test_persistence_across_connections(temp_db):
    fs.save_audit(_audit())
    # Simulate app restart: fresh call reads the same file.
    assert len(fs.last_runs(10)) == 1
