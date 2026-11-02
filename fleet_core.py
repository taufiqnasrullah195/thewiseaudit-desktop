"""
fleet_core.py — data collection layer for FleetAudit Desktop.

Pure logic, no Tkinter imports: everything here is unit-testable and feeds
the GUI via plain dicts. Collection functions never raise on individual
probe failure — they record an error string per probe so one flaky counter
cannot kill a whole audit run.

Python 3.10+, psutil.
"""
from __future__ import annotations

import csv
import html
import io
import platform
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import psutil

# Health thresholds — deliberately conservative for a junior-support audit:
# flag only states that genuinely page a human.
THRESHOLDS = {
    "cpu_percent": 85.0,      # sustained over the sampling window
    "ram_percent": 85.0,
    "disk_free_percent": 10.0,
    "uptime_days": 7.0,       # reboot pending / patch hygiene smell
    "top_proc_mem_percent": 25.0,
}

SEVERITY_ORDER = {"OK": 0, "INFO": 1, "WARNING": 2, "CRITICAL": 3}


@dataclass
class Finding:
    check: str
    severity: str          # OK | INFO | WARNING | CRITICAL
    detail: str

    def as_row(self) -> list[str]:
        return [self.check, self.severity, self.detail]


@dataclass
class AuditResult:
    scanned_at: str = ""
    system: dict = field(default_factory=dict)
    storage: list = field(default_factory=list)      # list[dict] per partition
    network: dict = field(default_factory=dict)
    top_processes: list = field(default_factory=list)  # list[dict]
    findings: list = field(default_factory=list)     # list[Finding]
    errors: list = field(default_factory=list)       # per-probe error strings

    @property
    def worst_severity(self) -> str:
        worst = "OK"
        for f in self.findings:
            if SEVERITY_ORDER[f.severity] > SEVERITY_ORDER[worst]:
                worst = f.severity
        return worst

    def findings_rows(self) -> list[list[str]]:
        return [f.as_row() for f in self.findings]


def _safe(probe_name: str, fn):
    """Run one probe; on failure record the error and return None."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - probe isolation is the point
        return f"<probe failed: {probe_name}: {exc.__class__.__name__}: {exc}>"


def collect_system() -> dict:
    boot = psutil.boot_time()
    uptime = datetime.now() - datetime.fromtimestamp(boot)
    uname = platform.uname()
    return {
        "hostname": uname.node or socket.gethostname(),
        "os": f"{uname.system} {uname.release}",
        "os_version": uname.version,
        "machine": uname.machine,
        "processor": uname.processor or platform.machine(),
        "python": platform.python_version(),
        "cpu_count_physical": psutil.cpu_count(logical=False) or 0,
        "cpu_count_logical": psutil.cpu_count(logical=True) or 0,
        "cpu_percent": psutil.cpu_percent(interval=0.4),
        "ram_total_gb": round(psutil.virtual_memory().total / 2**30, 2),
        "ram_used_gb": round(psutil.virtual_memory().used / 2**30, 2),
        "ram_percent": psutil.virtual_memory().percent,
        "boot_time": datetime.fromtimestamp(boot).strftime("%Y-%m-%d %H:%M"),
        "uptime_days": round(uptime.total_seconds() / 86400, 2),
    }


def collect_storage() -> list[dict]:
    parts = []
    for p in psutil.disk_partitions(all=False):
        usage = _safe(f"disk_usage({p.mountpoint})",
                      lambda: psutil.disk_usage(p.mountpoint))
        if isinstance(usage, str):
            parts.append({"device": p.device, "mount": p.mountpoint,
                          "fstype": p.fstype, "error": usage})
            continue
        parts.append({
            "device": p.device,
            "mount": p.mountpoint,
            "fstype": p.fstype,
            "total_gb": round(usage.total / 2**30, 2),
            "used_gb": round(usage.used / 2**30, 2),
            "free_gb": round(usage.free / 2**30, 2),
            "free_percent": round(usage.free / usage.total * 100, 1)
            if usage.total else 0.0,
        })
    return parts


def collect_network() -> dict:
    ifs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    row: dict[str, object] = {}
    interfaces: list[dict] = []
    row["interfaces"] = interfaces
    for name, addrs in ifs.items():
        ipv4 = next((a.address for a in addrs if a.family == socket.AF_INET), "")
        try:
            state = "up" if stats[name].isup else "down"
            speed = stats[name].speed or 0
        except (KeyError, AttributeError):
            state, speed = "?", 0
        row["interfaces"].append(
            {"name": name, "ipv4": ipv4, "state": state, "speed_mbit": speed})
    row["hostname_resolves"] = _safe("gethostbyname",
                                     lambda: socket.gethostbyname(socket.gethostname()))
    return row


def collect_top_processes(limit: int = 10, by: str = "memory") -> list[dict]:
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_percent", "cpu_percent"]):
        info = p.info
        procs.append({
            "pid": info["pid"],
            "name": info["name"] or "?",
            "mem_percent": round(info["memory_percent"] or 0.0, 1),
            "cpu_percent": round(info["cpu_percent"] or 0.0, 1),
        })
    key = "mem_percent" if by == "memory" else "cpu_percent"
    procs.sort(key=lambda d: d[key], reverse=True)
    return procs[:limit]


def evaluate_health(system: dict, storage: list[dict],
                    top_processes: list[dict]) -> list[Finding]:
    """Threshold engine. Deterministic, pure: same inputs -> same findings."""
    out: list[Finding] = []

    if system["cpu_percent"] >= THRESHOLDS["cpu_percent"]:
        out.append(Finding("CPU load", "WARNING",
                           f"{system['cpu_percent']:.0f}% sustained >= "
                           f"{THRESHOLDS['cpu_percent']:.0f}% threshold"))
    else:
        out.append(Finding("CPU load", "OK",
                           f"{system['cpu_percent']:.0f}% (threshold "
                           f"{THRESHOLDS['cpu_percent']:.0f}%)"))

    if system["ram_percent"] >= THRESHOLDS["ram_percent"]:
        out.append(Finding("Memory pressure", "WARNING",
                           f"{system['ram_percent']:.0f}% used of "
                           f"{system['ram_total_gb']} GB"))
    else:
        out.append(Finding("Memory pressure", "OK",
                           f"{system['ram_percent']:.0f}% used of "
                           f"{system['ram_total_gb']} GB"))

    for part in storage:
        if "error" in part:
            out.append(Finding(f"Disk {part['mount']}", "INFO",
                               part["error"]))
            continue
        if part["free_percent"] < THRESHOLDS["disk_free_percent"]:
            out.append(Finding(
                f"Disk {part['mount']}", "CRITICAL",
                f"only {part['free_percent']}% free "
                f"({part['free_gb']} GB of {part['total_gb']} GB)"))
        else:
            out.append(Finding(f"Disk {part['mount']}", "OK",
                               f"{part['free_percent']}% free "
                               f"({part['free_gb']} GB)"))

    if system["uptime_days"] > THRESHOLDS["uptime_days"]:
        out.append(Finding("Uptime / patch hygiene", "WARNING",
                           f"{system['uptime_days']:.1f} days since "
                           f"{system['boot_time']} — reboot/patch overdue"))
    else:
        out.append(Finding("Uptime / patch hygiene", "OK",
                           f"{system['uptime_days']:.1f} days since boot"))

    hog = top_processes[0] if top_processes else None
    if hog and hog["mem_percent"] >= THRESHOLDS["top_proc_mem_percent"]:
        out.append(Finding("Top memory consumer", "WARNING",
                           f"{hog['name']} (pid {hog['pid']}) at "
                           f"{hog['mem_percent']}% of RAM"))
    elif hog:
        out.append(Finding("Top memory consumer", "OK",
                           f"{hog['name']} (pid {hog['pid']}) at "
                           f"{hog['mem_percent']}% of RAM"))
    return out


def run_audit() -> AuditResult:
    """Full sweep. Thread-safe: call from a worker thread, hand the result back."""
    res = AuditResult(scanned_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    sysd = _safe("system", collect_system)
    if isinstance(sysd, str):
        res.errors.append(sysd)
        sysd = {"cpu_percent": 0.0, "ram_percent": 0.0, "ram_total_gb": 0.0,
                "uptime_days": 0.0, "boot_time": "?"}
    res.system = sysd

    stor = _safe("storage", collect_storage)
    res.storage = stor if isinstance(stor, list) else []
    if isinstance(stor, str):
        res.errors.append(stor)

    net = _safe("network", collect_network)
    res.network = net if isinstance(net, dict) else {}
    if isinstance(net, str):
        res.errors.append(net)

    procs = _safe("top_processes", collect_top_processes)
    res.top_processes = procs if isinstance(procs, list) else []
    if isinstance(procs, str):
        res.errors.append(procs)

    res.findings = evaluate_health(sysd, res.storage, res.top_processes)
    return res


# ---------- export ----------

CSV_FIELDS = ["scanned_at", "hostname", "os", "cpu_percent", "ram_percent",
              "ram_total_gb", "uptime_days", "worst_severity", "errors"]


def to_csv(res: AuditResult) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_FIELDS)
    w.writerow([
        res.scanned_at, res.system.get("hostname", "?"),
        res.system.get("os", "?"), res.system.get("cpu_percent", 0),
        res.system.get("ram_percent", 0), res.system.get("ram_total_gb", 0),
        res.system.get("uptime_days", 0), res.worst_severity,
        " | ".join(res.errors),
    ])
    return buf.getvalue()


def to_html(res: AuditResult) -> str:
    sysd = res.system
    sev_color = {"OK": "#2e7d32", "INFO": "#1565c0",
                 "WARNING": "#ef6c00", "CRITICAL": "#c62828"}

    def esc(v) -> str:
        return html.escape(str(v))

    rows = "\n".join(
        f"<tr><td>{esc(f.check)}</td>"
        f"<td style='color:{sev_color.get(f.severity, '#333')};font-weight:600'>"
        f"{esc(f.severity)}</td><td>{esc(f.detail)}</td></tr>"
        for f in res.findings)

    disks = "\n".join(
        f"<tr><td>{esc(p['mount'])}</td><td>{esc(p.get('fstype', '?'))}</td>"
        f"<td>{esc(p.get('total_gb', '?'))} GB</td>"
        f"<td>{esc(p.get('free_gb', '?'))} GB</td>"
        f"<td>{esc(p.get('free_percent', '?'))}%</td></tr>"
        for p in res.storage)

    procs = "\n".join(
        f"<tr><td>{p['pid']}</td><td>{esc(p['name'])}</td>"
        f"<td>{p['cpu_percent']}%</td><td>{p['mem_percent']}%</td></tr>"
        for p in res.top_processes[:8])

    return f"""<!doctype html><html><head><meta charset='utf-8'>
<title>FleetAudit — {esc(sysd.get('hostname', '?'))}</title>
<style>body{{font-family:Segoe UI,system-ui,sans-serif;margin:32px;color:#212121}}
h1{{font-size:1.4rem}} .meta{{color:#666;font-family:Consolas,monospace}}
table{{border-collapse:collapse;margin:16px 0 28px;width:100%}}
td,th{{border:1px solid #ddd;padding:6px 10px;font-size:13px;text-align:left}}
th{{background:#f5f5f5}}</style></head><body>
<h1>FleetAudit report — {esc(sysd.get('hostname', '?'))}</h1>
<p class='meta'>scanned {esc(res.scanned_at)} · worst severity:
<b>{esc(res.worst_severity)}</b></p>
<h2>Health findings</h2><table><tr><th>Check</th><th>Severity</th><th>Detail</th></tr>{rows}</table>
<h2>Storage</h2><table><tr><th>Mount</th><th>Fs</th><th>Total</th><th>Free</th><th>Free %</th></tr>{disks}</table>
<h2>Top processes</h2><table><tr><th>PID</th><th>Name</th><th>CPU</th><th>Mem</th></tr>{procs}</table>
</body></html>"""
