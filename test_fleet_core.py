"""test_fleet_core.py — FleetAudit core: thresholds, isolation, export shapes."""
import csv
import io

import fleet_core as fc


# ---------- evaluate_health: pure threshold engine ----------

def _sys(cpu=10.0, ram=40.0, uptime=1.0):
    return {"cpu_percent": cpu, "ram_percent": ram, "ram_total_gb": 16.0,
            "uptime_days": uptime, "boot_time": "2026-01-01 00:00"}


def _disk(free_pct=50.0):
    return [{"device": "C:", "mount": "C:\\", "fstype": "NTFS",
             "total_gb": 500.0, "used_gb": 250.0, "free_gb": 250.0,
             "free_percent": free_pct}]


def _proc(mem=5.0):
    return [{"pid": 42, "name": "svc.exe", "mem_percent": mem,
             "cpu_percent": 1.0}]


def test_all_green_when_healthy():
    findings = fc.evaluate_health(_sys(), _disk(), _proc())
    assert all(f.severity == "OK" for f in findings)
    assert len(findings) >= 4  # cpu, ram, disk, uptime, top-proc


def test_cpu_and_ram_warning():
    f = fc.evaluate_health(_sys(cpu=91.0, ram=93.0), _disk(), _proc())
    assert {x.check for x in f if x.severity == "WARNING"} >= {
        "CPU load", "Memory pressure"}


def test_disk_critical_when_under_10pct_free():
    f = fc.evaluate_health(_sys(), _disk(free_pct=4.0), _proc())
    disk = next(x for x in f if x.check == "Disk C:\\")
    assert disk.severity == "CRITICAL"


def test_uptime_warning_after_7_days():
    f = fc.evaluate_health(_sys(uptime=12.5), _disk(), _proc())
    assert any(x.severity == "WARNING" and "Uptime" in x.check for x in f)


def test_top_process_memory_hog_flagged():
    f = fc.evaluate_health(_sys(), _disk(), _proc(mem=38.2))
    assert any("Top memory consumer" in x.check and x.severity == "WARNING"
               for x in f)


def test_worst_severity_ordering():
    res = fc.AuditResult(findings=[
        fc.Finding("a", "OK", ""), fc.Finding("b", "WARNING", ""),
        fc.Finding("c", "CRITICAL", "")])
    assert res.worst_severity == "CRITICAL"


# ---------- live probes (local machine, read-only) ----------

def test_run_audit_returns_complete_shape():
    res = fc.run_audit()
    assert res.scanned_at
    assert isinstance(res.system, dict)
    assert res.system["cpu_count_logical"] >= 1
    assert isinstance(res.storage, list) and res.storage
    assert any("C:" in p.get("device", "") or p.get("mount") for p in res.storage)
    assert isinstance(res.network.get("interfaces"), list)
    assert isinstance(res.top_processes, list) and res.top_processes
    assert res.findings and res.worst_severity in {"OK", "INFO", "WARNING",
                                                   "CRITICAL"}


def test_top_processes_sorted_desc():
    procs = fc.collect_top_processes(limit=8)
    mems = [p["mem_percent"] for p in procs]
    assert mems == sorted(mems, reverse=True)
    assert len(procs) <= 8


# ---------- export ----------

def _fake_result():
    res = fc.AuditResult(scanned_at="2026-01-01 10:00:00")
    res.system = _sys()
    res.system["hostname"] = "TESTBOX"
    res.system["os"] = "TestOS 1"
    res.storage = _disk()
    res.top_processes = _proc()
    res.findings = fc.evaluate_health(res.system, res.storage,
                                      res.top_processes)
    return res


def test_csv_has_header_and_one_row():
    text = fc.to_csv(_fake_result())
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == fc.CSV_FIELDS
    assert len(rows) == 2
    assert rows[1][1] == "TESTBOX"
    assert rows[1][7] == "OK"


def test_html_escapes_and_contains_sections():
    res = _fake_result()
    res.system["hostname"] = "<b>x&y</b>"
    page = fc.to_html(res)
    assert "&lt;b&gt;x&amp;y&lt;/b&gt;" in page
    for section in ("Health findings", "Storage", "Top processes"):
        assert section in page
    assert page.lstrip().startswith("<!doctype html>")


def test_probe_error_recorded_not_raised(monkeypatch):
    def boom():
        raise RuntimeError("kernel said no")
    monkeypatch.setattr(fc.psutil, "boot_time", boom)
    # _safe must swallow and return a string marker
    out = fc._safe("boot_time", fc.collect_system)
    assert isinstance(out, str) and "boot_time" in out and "RuntimeError" in out
