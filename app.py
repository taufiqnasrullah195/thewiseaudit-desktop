"""
app.py — FleetAudit Desktop GUI (Tkinter/ttk).

Threading contract: the audit sweep runs in one daemon worker thread; the
worker never touches widgets. It puts the AuditResult on a queue and the UI
drains the queue on the Tk main loop via after(). This keeps the window
responsive during psutil's blocking cpu_percent(interval=...) sampling.

Run:  python app.py            (interactive)
      python app.py --selftest (builds UI, auto-scan, auto-close, exit 0)
"""
from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk

import fleet_core
import fleet_multi
from fleet_core import AuditResult

COL_OK, COL_WARN, COL_CRIT, COL_INFO = "#2e7d32", "#ef6c00", "#c62828", "#1565c0"

SEV_TAG = {"OK": "ok", "INFO": "info", "WARNING": "warn", "CRITICAL": "crit"}


class FleetAuditApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("FleetAudit — PC health & inventory")
        self.geometry("980x680")
        self.minsize(820, 560)

        self._q: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._fleet_worker: threading.Thread | None = None
        self._latest: AuditResult | None = None

        self._build_style()
        self._build_ui()
        self.after(80, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- UI construction ----------

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            style.theme_use("clam")
        self.option_add("*Font", ("Segoe UI", 10))
        style.configure("Treeview", rowheight=24)
        style.configure("TButton", padding=(14, 6))
        style.configure("Scan.TButton", font=("Segoe UI", 10, "bold"))

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=(12, 10, 12, 4))
        top.pack(fill="x")
        ttk.Button(top, text="Scan now", style="Scan.TButton",
                   command=self.start_scan).pack(side="left")
        ttk.Button(top, text="Export HTML…", command=self._export_html
                   ).pack(side="left", padx=(10, 0))
        ttk.Button(top, text="Export CSV…", command=self._export_csv
                   ).pack(side="left")
        self._status = tk.StringVar(
            value="Ready — hit Scan now to audit this machine.")
        ttk.Label(top, textvariable=self._status, anchor="e"
                  ).pack(side="right", fill="x", expand=True)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=(4, 6))
        self._tab_overview = self._tree(nb, ("Property", "Value"))
        nb.add(self._tab_overview, text="Overview")
        self._tab_findings = self._tree(
            nb, ("Check", "Severity", "Detail"), height=10)
        self._tab_findings.tag_configure("ok", foreground=COL_OK)
        self._tab_findings.tag_configure("info", foreground=COL_INFO)
        self._tab_findings.tag_configure("warn", foreground=COL_WARN)
        self._tab_findings.tag_configure("crit", foreground=COL_CRIT,
                                         font=("Segoe UI", 10, "bold"))
        nb.add(self._tab_findings, text="Health findings")
        self._tab_storage = self._tree(
            nb, ("Mount", "Fs", "Total GB", "Free GB", "Free %"))
        nb.add(self._tab_storage, text="Storage")
        self._tab_net = self._tree(
            nb, ("Interface", "IPv4", "State", "Speed Mbit"))
        nb.add(self._tab_net, text="Network")
        self._tab_procs = self._tree(
            nb, ("PID", "Name", "CPU %", "Mem %"))
        nb.add(self._tab_procs, text="Top processes")

        # --- Fleet tab: multi-host reachability & service probes ---
        fleet_frame = ttk.Frame(nb, padding=10)
        nb.add(fleet_frame, text="Fleet (multi-host)")
        ttk.Label(
            fleet_frame,
            text="Hosts — one per line or CSV first column (IP or hostname):"
        ).pack(anchor="w")
        self._hosts_text = tk.Text(fleet_frame, height=6, width=70,
                                   font=("Consolas", 10), undo=True)
        self._hosts_text.pack(fill="x", pady=(4, 8))
        btns = ttk.Frame(fleet_frame)
        btns.pack(fill="x")
        ttk.Button(btns, text="Scan fleet",
                   command=self.start_fleet_scan).pack(side="left")
        ttk.Button(btns, text="Export fleet CSV…",
                   command=self._export_fleet_csv).pack(side="left",
                                                        padx=(10, 0))
        self._fleet_status = tk.StringVar(
            value="Probe ports: " + ", ".join(
                f"{n} ({p})" for p, n in fleet_multi.PROBE_PORTS))
        ttk.Label(btns, textvariable=self._fleet_status
                  ).pack(side="right")
        self._tab_fleet = self._tree(
            fleet_frame, ("Host", "Status", "Latency ms",
                          "Open services", "Closed/filtered"))
        self._tab_fleet.pack(fill="both", expand=True, pady=(8, 0))
        self._tab_fleet.tag_configure("up", foreground=COL_OK)
        self._tab_fleet.tag_configure("down", foreground=COL_WARN)
        self._tab_fleet.tag_configure("error", foreground=COL_CRIT)
        self._fleet_results: list[fleet_multi.HostResult] = []
        self._fleet_started = ""

        self._footer = ttk.Label(
            self, padding=(12, 0, 12, 8),
            text="FleetAudit v1.0 · thresholds: "
                 + ", ".join(f"{k}={v}" for k, v
                             in fleet_core.THRESHOLDS.items()))
        self._footer.pack(fill="x")

    def _tree(self, parent, cols, height: int = 14) -> ttk.Treeview:
        tv = ttk.Treeview(parent, columns=cols, show="headings", height=height)
        for i, c in enumerate(cols):
            stretch = i == len(cols) - 1
            tv.heading(c, text=c)
            tv.column(c, width=120 if not stretch else 380,
                      stretch=stretch)
        return tv

    # ---------- scan lifecycle ----------

    def start_scan(self) -> None:
        if self._worker and self._worker.is_alive():
            self._status.set("Scan already running…")
            return
        self._status.set("Scanning (CPU sampling takes ~1s)…")
        self._worker = threading.Thread(
            target=self._worker_scan, daemon=True)
        self._worker.start()

    def _worker_scan(self) -> None:
        try:
            self._q.put(fleet_core.run_audit())
        except Exception as exc:  # noqa: BLE001 — surfaced in status bar
            self._q.put(AuditResult(errors=[f"scan crashed: {exc!r}"]))

    def _drain_queue(self) -> None:
        try:
            while True:
                item = self._q.get_nowait()
                if isinstance(item, AuditResult):
                    self._latest = item
                    self._render(item)
                else:  # ("fleet", results, started)
                    _, results, started = item
                    self._render_fleet(results, started)
        except queue.Empty:
            pass
        self.after(80, self._drain_queue)

    # ---------- rendering ----------

    def _render(self, res: AuditResult) -> None:
        for tv in (self._tab_overview, self._tab_findings, self._tab_storage,
                   self._tab_net, self._tab_procs):
            tv.delete(*tv.get_children())

        s = res.system
        for k, v in (("Hostname", s.get("hostname")),
                     ("OS", s.get("os")),
                     ("OS build", s.get("os_version")),
                     ("CPU", f"{s.get('cpu_count_physical')} phys / "
                             f"{s.get('cpu_count_logical')} logical"),
                     ("CPU load", f"{s.get('cpu_percent')}%"),
                     ("Memory", f"{s.get('ram_used_gb')} / "
                                f"{s.get('ram_total_gb')} GB "
                                f"({s.get('ram_percent')}%)"),
                     ("Boot", f"{s.get('boot_time')} "
                              f"(uptime {s.get('uptime_days')} d)"),
                     ("Scanned", res.scanned_at)):
            if v is not None:
                self._tab_overview.insert("", "end", values=(k, v))

        for f in res.findings:
            self._tab_findings.insert(
                "", "end", values=f.as_row(), tags=(SEV_TAG[f.severity],))

        for p in res.storage:
            if "error" in p:
                self._tab_storage.insert("", "end",
                                         values=(p["mount"], p["fstype"],
                                                 "—", "—", p["error"]))
            else:
                self._tab_storage.insert(
                    "", "end",
                    values=(p["mount"], p["fstype"], p["total_gb"],
                            p["free_gb"], p["free_percent"]))

        for i in res.network.get("interfaces", []):
            self._tab_net.insert("", "end", values=(
                i["name"], i["ipv4"] or "—", i["state"], i["speed_mbit"]))

        for p in res.top_processes:
            self._tab_procs.insert("", "end", values=(
                p["pid"], p["name"], p["cpu_percent"], p["mem_percent"]))

        n_warn = sum(1 for f in res.findings if f.severity == "WARNING")
        n_crit = sum(1 for f in res.findings if f.severity == "CRITICAL")
        extra = f" · {len(res.errors)} probe errors" if res.errors else ""
        self._status.set(
            f"Done · worst: {res.worst_severity} · "
            f"{n_crit} critical / {n_warn} warning{extra}")

    # ---------- export ----------

    def _export_html(self) -> None:
        if not self._latest:
            self._status.set("Nothing to export yet — run a scan first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".html",
            initialfile="fleetaudit_report.html",
            filetypes=[("HTML report", "*.html")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(fleet_core.to_html(self._latest))
        self._status.set(f"HTML written → {path}")

    def _export_csv(self) -> None:
        if not self._latest:
            self._status.set("Nothing to export yet — run a scan first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile="fleetaudit_report.csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(fleet_core.to_csv(self._latest))
        self._status.set(f"CSV written → {path}")

    # ---------- fleet (multi-host) lifecycle ----------

    def start_fleet_scan(self) -> None:
        hosts = fleet_multi.parse_hosts(self._hosts_text.get("1.0", "end"))
        if not hosts:
            self._fleet_status.set("Enter at least one host first.")
            return
        if self._fleet_worker and self._fleet_worker.is_alive():
            self._fleet_status.set("Busy — wait for the running scan.")
            return
        self._fleet_status.set(f"Scanning {len(hosts)} host(s)…")
        self._fleet_worker = threading.Thread(
            target=self._worker_fleet, args=(hosts,), daemon=True)
        self._fleet_worker.start()

    def _worker_fleet(self, hosts: list[str]) -> None:
        try:
            results, started = fleet_multi.scan_fleet(hosts)
            self._q.put(("fleet", results, started))
        except Exception as exc:  # noqa: BLE001
            self._q.put(("fleet", [], f"fleet scan crashed: {exc!r}"))

    def _render_fleet(self, results: list[fleet_multi.HostResult],
                      started: str) -> None:
        self._fleet_results = results
        self._fleet_started = started
        self._tab_fleet.delete(*self._tab_fleet.get_children())
        for r in results:
            self._tab_fleet.insert("", "end", values=r.as_row(),
                                   tags=(r.status,))
        n_up = sum(1 for r in results if r.status == "up")
        self._fleet_status.set(
            f"{n_up}/{len(results)} up · scanned {started}")

    def _export_fleet_csv(self) -> None:
        if not self._fleet_results:
            self._fleet_status.set("Nothing to export — run a fleet scan.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile="fleet_scan.csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(fleet_multi.fleet_to_csv(self._fleet_results,
                                              self._fleet_started))
        self._fleet_status.set(f"CSV written → {path}")

    def _on_close(self) -> None:
        self._closing = True
        self.destroy()


def main() -> int:
    app = FleetAuditApp()
    if "--selftest" in sys.argv:
        app.start_scan()
        app._hosts_text.insert("1.0", "127.0.0.1\n192.0.2.123\n")
        app.start_fleet_scan()
        app.after(8000, app.destroy)  # workers + render settle time
    app.mainloop()
    if "--selftest" in sys.argv:
        assert app._latest is not None, "selftest: no result rendered"
        assert app._latest.findings, "selftest: no findings rendered"
        assert app._fleet_results, "selftest: no fleet results rendered"
        assert app._fleet_results[0].status == "up"
        print("SELFTEST OK · local worst severity:",
              app._latest.worst_severity, "· fleet:",
              len(app._fleet_results), "hosts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
