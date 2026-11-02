"""
fleet_multi.py — multi-host reachability & service probe layer.

For each host in a CSV/text list: ICMP ping (via system ping, no admin
rights needed) and TCP connect-probes on the ports an IT support desk
actually cares about (RDP, SMB, WinRM, SSH). Results are dataclass rows
consumed by the GUI and the exporters.

Design notes:
- Sockets run with 0.6s connect timeout per port; hosts are probed in a
  ThreadPoolExecutor (max 16) so a 50-host file finishes in ~5-8s instead
  of stalling the UI worker.
- A dead host short-circuits: ping fails -> ports probed anyway (some
  stacks drop ICMP), but latency is recorded as None.
"""
from __future__ import annotations

import csv
import io
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime

# The ports a helpdesk checks first when a user says "I can't reach X".
PROBE_PORTS: list[tuple[int, str]] = [
    (3389, "RDP"),
    (445, "SMB"),
    (5985, "WinRM-HTTP"),
    (5986, "WinRM-HTTPS"),
    (22, "SSH"),
]
CONNECT_TIMEOUT_S = 0.6
PING_TIMEOUT_FLAG = "/w" if sys.platform.startswith("win") else "-W"

# Windows localizes ping output, so parse the *summary* lines instead:
# English "Received = 3", German "Empfangen = 3", Indonesian "Diterima = 3".
# The " = <n>" pattern after a Received-like token is stable across locales.
_REPLY_RE = re.compile(r"=\s*(\d+)")
_TTL_RE = re.compile(r"[tT][tT][lL][^\d]*(\d+)")


@dataclass
class HostResult:
    host: str
    status: str = "down"          # up | down | error
    latency_ms: int | None = None
    open_ports: list[str] = field(default_factory=list)
    closed_ports: list[str] = field(default_factory=list)
    error: str = ""

    def as_row(self) -> list[object]:
        return [self.host, self.status,
                self.latency_ms if self.latency_ms is not None else "—",
                ", ".join(self.open_ports) or "—",
                ", ".join(self.closed_ports) or "—"]


def parse_hosts(text: str) -> list[str]:
    """Accept a CSV or newline list; keep first column, skip comments."""
    hosts: list[str] = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if not row:
            continue
        cell = row[0].strip()
        if not cell or cell.startswith("#") or cell.lower() == "hostname":
            continue
        if cell not in hosts:
            hosts.append(cell)
    return hosts


def ping_host(host: str) -> tuple[bool, int | None]:
    """System ping, locale-tolerant. Returns (reachable, latency_ms)."""
    cmd = ["ping", "-n", "2", host]
    if sys.platform.startswith("win"):
        cmd = ["ping", "-n", "2", "-w", "1200", host]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=8, errors="replace").stdout
    except (subprocess.TimeoutExpired, OSError):
        return False, None
    # Summary always contains pairs like "Sent = 2" / "Received = 0".
    nums = [int(m) for m in _REPLY_RE.findall(out.splitlines()[-3:].__str__())
            ] or [int(m) for m in _REPLY_RE.findall(out)]
    # Fallback: any "TTL=" in body means at least one reply arrived.
    if _TTL_RE.search(out):
        # Best-effort latency: first "time[= ]Xms" occurrence.
        m = re.search(r"[tT]ime[<=> ]*(\d+)\s*ms", out)
        return True, int(m.group(1)) if m else 0
    if len(nums) >= 2 and nums[-1] > 0:
        return True, None
    return False, None


def probe_port(host: str, port: int, name: str) -> str | None:
    """Return service name if TCP connect succeeds, else None."""
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_S):
            return name
    except OSError:
        return None


def scan_host(host: str) -> HostResult:
    res = HostResult(host=host)
    try:
        up, latency = ping_host(host)
        res.status = "up" if up else "down"
        res.latency_ms = latency
    except Exception as exc:  # noqa: BLE001 — record, don't crash the batch
        res.status = "error"
        res.error = f"ping: {exc.__class__.__name__}"
    for port, name in PROBE_PORTS:
        (res.open_ports if probe_port(host, port, name)
         else res.closed_ports).append(name)
    return res


def scan_fleet(hosts: list[str], max_workers: int = 16
               ) -> tuple[list[HostResult], str]:
    """Batch scan. Order of results follows input order (futures map)."""
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results: dict[str, HostResult] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(hosts))
                                           ) or 1) as pool:
        futures = {pool.submit(scan_host, h): h for h in hosts}
        for fut in as_completed(futures):
            r = fut.result()
            results[r.host] = r
    ordered = [results[h] for h in hosts if h in results]
    return ordered, started


def fleet_to_csv(results: list[HostResult], started: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["scanned_at", "host", "status", "latency_ms",
                "open", "closed"])
    for r in results:
        w.writerow([started, r.host, r.status,
                    r.latency_ms if r.latency_ms is not None else "",
                    ";".join(r.open_ports), ";".join(r.closed_ports)])
    return buf.getvalue()
