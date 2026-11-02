"""demo_fleet_tab.py — park FleetAudit on the Fleet (multi-host) tab with
fresh scan results, for external capture."""
import time

import app as app_mod

app = app_mod.FleetAuditApp()
app.geometry("980x680+60+40")
app.update()

app.start_scan()
app._hosts_text.insert("1.0", "127.0.0.1\n192.0.2.123\n192.168.1.1\n")
app.start_fleet_scan()

nb = app.children["!notebook"]


def show_fleet() -> None:
    nb.select(5)
    app.title("FleetAudit — Fleet multi-host")


app.after(6000, show_fleet)  # let both scans finish first
app.mainloop()
