"""demo_app.py — FleetAudit UI in demo mode: auto-runs both scans and
cycles through every tab every 4s forever, so an external screen capturer
can grab each tab without synthetic input."""
import time

import app as app_mod

app = app_mod.FleetAuditApp()
app.geometry("980x680+60+40")
app.update()

# fire both real scans immediately
app.start_scan()
app._hosts_text.insert("1.0", "127.0.0.1\n192.0.2.123\n192.168.1.1\n")
app.start_fleet_scan()

nb = app.children["!notebook"]
TAB_NAMES = ["Overview", "Health findings", "Storage", "Network",
             "Top processes", "Fleet multi-host"]
TAB_COUNT = 6
_state = {"i": 0}


def cycle() -> None:
    """Rotate tabs every 4s via after() so the Tk loop stays idle and UIA
    (used by an external capturer) can talk to the window. The window title
    mirrors the current tab so a text-only observer knows what's shown."""
    app.after(4000, cycle)
    idx = _state["i"] % TAB_COUNT
    nb.select(idx)
    app.title(f"FleetAudit — {TAB_NAMES[idx]}")
    _state["i"] += 1


app.after(4000, cycle)
app.mainloop()
