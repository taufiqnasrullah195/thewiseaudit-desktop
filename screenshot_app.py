"""screenshot_app.py — boot FleetAudit UI, run scans, capture each tab.

Runs the real app (not a mock): local audit + fleet scan against
127.0.0.1/192.0.2.123/192.168.1.1, then screenshots every notebook tab
with PIL ImageGrab before closing. Exit 0 = six PNGs written.
"""
import sys
import time

from PIL import ImageGrab

import app as app_mod


def snap(win, name: str) -> None:
    win.update_idletasks()
    win.update()
    x, y = win.winfo_rootx(), win.winfo_rooty()
    w, h = win.winfo_width(), win.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, h))
    img.save(f"shot_{name}.png")
    print(f"saved shot_{name}.png ({img.width}x{img.height})")


def main() -> int:
    app = app_mod.FleetAuditApp()
    app.geometry("980x680+60+40")
    app.update()

    # fire both real scans
    app.start_scan()
    app._hosts_text.insert("1.0", "127.0.0.1\n192.0.2.123\n192.168.1.1\n")
    app.start_fleet_scan()

    # wait for both workers to report through the queue
    deadline = time.time() + 30
    while time.time() < deadline:
        app.update()
        time.sleep(0.1)
        if app._latest and app._fleet_results:
            break
    app.update()
    time.sleep(0.5)

    nb = app.children["!notebook"]
    tabs = ["overview", "findings", "storage", "network", "procs"]
    for i, name in enumerate(tabs):
        nb.select(i)
        time.sleep(0.25)
        snap(app, name)
    nb.select(5)  # Fleet tab
    time.sleep(0.25)
    snap(app, "fleet")

    app.destroy()
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
