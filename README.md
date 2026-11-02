# FleetAudit Desktop

A desktop app for junior IT support work: one-click health audit and
inventory of a Windows PC, with threshold findings and exportable reports.

Built with **Python 3.10+, Tkinter/ttk, psutil** — zero heavy dependencies,
ships as plain `.py` files.

## What it does

- **Overview** — hostname, OS build, CPU/memory, boot time and uptime
- **Health findings** — threshold engine flags:
  - CPU ≥ 85% sustained → WARNING
  - RAM ≥ 85% → WARNING
  - any disk < 10% free → CRITICAL
  - uptime > 7 days → WARNING (reboot/patch overdue)
  - single process ≥ 25% RAM → WARNING
- **Storage** — per-partition total/used/free
- **Network** — interfaces, IPv4, link state/speed
- **Top processes** — top 10 by memory
- **Fleet (multi-host)** — paste a host list (one per line or CSV first
  column), then batch ping + TCP probes for the ports a helpdesk checks
  first: RDP (3389), SMB (445), WinRM (5985/5986), SSH (22). Status, latency
  and open/closed services land in one table, exportable to CSV
- **Export** — HTML report (styled, severity-colored) + CSV row
- **Probe isolation** — a failing counter (e.g. unreadable disk) is recorded,
  never crashes the audit run

## Run

```
python app.py              # interactive
python app.py --selftest   # headless-ish: auto-scan, auto-close, exit 0
python run_tests.py        # 11 pytest cases on the core engine
```

## Layout

- `fleet_core.py` — pure collection + threshold + export logic (no Tkinter)
- `fleet_multi.py` — multi-host ping + service-port probe layer
  (ThreadPoolExecutor, 16 workers, locale-tolerant ping parsing)
- `app.py` — GUI; scans run in daemon threads, results cross to the UI via
  a queue drained on the Tk main loop (`after()`), so the window never
  freezes during CPU sampling
- `test_fleet_core.py`, `test_fleet_multi.py` — 22 unit + live-probe tests
  (live tests target localhost and TEST-NET addresses; the LAN gateway
  test is skip-safe)

## Verified

- `run_tests.py` → 22 passed
- `app.py --selftest` → local audit + 2-host fleet scan rendered, exit 0
- live LAN check: router 192.168.1.1 up (1 ms), all helpdesk ports correctly
  reported closed; localhost SMB (445) correctly reported open

## Next steps (roadmap)

- Multi-host mode: feed a CSV of hostnames, run over WinRM/SSH
- Email digest of findings
- Diff against previous run (trend arrows)
