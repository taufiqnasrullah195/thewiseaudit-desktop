"""Probe: what's available in this interpreter for FleetAudit."""
import sys
print("python:", sys.version)
for mod in ("tkinter", "psutil", "wmi", "csv", "socket", "platform", "subprocess"):
    try:
        __import__(mod)
        print(f"{mod}: OK")
    except Exception as exc:
        print(f"{mod}: MISSING ({exc.__class__.__name__})")
