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

COL_OK, COL_WARN, COL_CRIT, COL_INFO = "#4caf50", "#ff9800", "#ef5350", "#42a5f5"

# Dark palette — one source of truth for the whole UI.
BG = "#16181d"          # window background
BG_PANEL = "#1e2128"    # cards / tables
BG_ROW_ALT = "#232732"  # zebra stripe
BG_INPUT = "#262a33"
FG = "#e8eaf0"          # primary text
FG_SOFT = "#9aa3b2"     # secondary text
FG_FAINT = "#6b7381"
ACCENT = "#7c9cff"      # brand blue-violet
LINE = "#2e3340"        # separators / borders
SEV_TAG = {"OK": "ok", "INFO": "info", "WARNING": "warn", "CRITICAL": "crit"}


class FleetAuditApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("FleetAudit — PC health & inventory")
        self.geometry("1000x700")
        self.minsize(840, 580)
        self.configure(bg=BG)

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
        style.theme_use("clam")  # fully themeable; vista is not
        self.option_add("*Font", ("Segoe UI", 10))

        style.configure(".", background=BG, foreground=FG,
                        bordercolor=LINE, troughcolor=BG_PANEL)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=BG_PANEL)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Soft.TLabel", background=BG, foreground=FG_SOFT)
        style.configure("Faint.TLabel", background=BG, foreground=FG_FAINT)
        style.configure("Status.TLabel", background=BG, foreground=FG_SOFT)

        # Flat accent buttons: filled primary, quiet secondary.
        style.configure("TButton", background=BG_INPUT, foreground=FG,
                        borderwidth=0, focusthickness=1,
                        focuscolor=ACCENT, padding=(14, 7))
        style.map("TButton",
                  background=[("active", "#333845"),
                              ("pressed", "#2c313c")])
        style.configure("Scan.TButton", background=ACCENT, foreground="#0f1116",
                        font=("Segoe UI", 10, "bold"), padding=(18, 7))
        style.map("Scan.TButton",
                  background=[("active", "#93adff"), ("pressed", "#6a89e8")])

        # Notebook: dark tabs, accent underline on the active one.
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG_PANEL, foreground=FG_SOFT,
                        padding=(16, 8), borderwidth=0, font=("Segoe UI", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", BG_PANEL)],
                  foreground=[("selected", FG)],
                  expand=[("selected", (1, 1, 1, 0))])

        # Treeviews: dark body, zebra stripes, no borders.
        style.configure("Treeview", background=BG_PANEL, foreground=FG,
                        fieldbackground=BG_PANEL, rowheight=26,
                        borderwidth=0, bordercolor=LINE)
        style.configure("Treeview.Heading",
                        background="#2a2f3a", foreground=FG_SOFT,
                        borderwidth=0, padding=(8, 6),
                        font=("Segoe UI", 9, "bold"))
        style.map("Treeview",
                  background=[("selected", "#31405f")],
                  foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", "#323848")])

    def _build_ui(self) -> None:
        # Header: wordmark + tagline + primary actions.
        header = ttk.Frame(self, padding=(16, 12, 16, 6))
        header.pack(fill="x")
        left = ttk.Frame(header)
        left.pack(side="left")
        ttk.Label(left, text="FleetAudit", foreground=ACCENT,
                  font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(left, text="PC health & inventory · fleet scanner",
                  style="Faint.TLabel",
                  font=("Segoe UI", 9)).pack(anchor="w")
        actions = ttk.Frame(header)
        actions.pack(side="right")
        self._status = tk.StringVar(
            value="Ready — hit Scan now to audit this machine.")
        ttk.Label(actions, textvariable=self._status, style="Status.TLabel"
                  ).pack(anchor="e", pady=(0, 6))
        btnrow = ttk.Frame(actions)
        btnrow.pack()
        ttk.Button(btnrow, text="Export HTML…", command=self._export_html
                   ).pack(side="right", padx=(8, 0))
        ttk.Button(btnrow, text="Export CSV…", command=self._export_csv
                   ).pack(side="right")
        ttk.Button(btnrow, text="Scan now  ▶", style="Scan.TButton",
                   command=self.start_scan).pack(side="right", padx=(0, 8))

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
        fleet_frame = ttk.Frame(nb, padding=12)
        nb.add(fleet_frame, text="Fleet (multi-host)")
        ttk.Label(
            fleet_frame, style="Soft.TLabel",
            text="Hosts — one per line or CSV first column (IP or hostname):"
        ).pack(anchor="w")
        self._hosts_text = tk.Text(
            fleet_frame, height=6, width=70, undo=True,
            font=("Consolas", 10), bg=BG_INPUT, fg=FG,
            insertbackground=FG, relief="flat", padx=10, pady=8,
            selectbackground="#31405f")
        self._hosts_text.pack(fill="x", pady=(4, 8))
        btns = ttk.Frame(fleet_frame)
        btns.pack(fill="x")
        ttk.Button(btns, text="Scan fleet", style="Scan.TButton",
                   command=self.start_fleet_scan).pack(side="left")
        ttk.Button(btns, text="Export fleet CSV…",
                   command=self._export_fleet_csv).pack(side="left",
                                                        padx=(10, 0))
        self._fleet_status = tk.StringVar(
            value="Probe ports: " + ", ".join(
                f"{n} ({p})" for p, n in fleet_multi.PROBE_PORTS))
        ttk.Label(btns, textvariable=self._fleet_status, style="Status.TLabel"
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
            self, padding=(16, 0, 16, 10), style="Faint.TLabel",
            font=("Segoe UI", 9),
            text="FleetAudit v1.1 · thresholds: "
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
        # zebra stripes keep long tables scannable on the dark body
        tv.tag_configure("odd", background=BG_ROW_ALT)
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
                row_i = len(self._tab_overview.get_children())
                self._tab_overview.insert("", "end", values=(k, v),
                                          tags=("odd",) if row_i % 2 else ())

        for f in res.findings:
            self._tab_findings.insert(
                "", "end", values=f.as_row(), tags=(SEV_TAG[f.severity],))

        for p in res.storage:
            row_i = len(self._tab_storage.get_children())
            stripe = ("odd",) if row_i % 2 else ()
            if "error" in p:
                self._tab_storage.insert("", "end",
                                         values=(p["mount"], p["fstype"],
                                                 "—", "—", p["error"]),
                                         tags=stripe)
            else:
                self._tab_storage.insert(
                    "", "end",
                    values=(p["mount"], p["fstype"], p["total_gb"],
                            p["free_gb"], p["free_percent"]),
                    tags=stripe)

        for i in res.network.get("interfaces", []):
            row_i = len(self._tab_net.get_children())
            self._tab_net.insert("", "end", values=(
                i["name"], i["ipv4"] or "—", i["state"], i["speed_mbit"]),
                tags=("odd",) if row_i % 2 else ())

        for p in res.top_processes:
            row_i = len(self._tab_procs.get_children())
            self._tab_procs.insert("", "end", values=(
                p["pid"], p["name"], p["cpu_percent"], p["mem_percent"]),
                tags=("odd",) if row_i % 2 else ())

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
