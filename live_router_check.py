"""live_router_check.py — prove the fleet scanner against the real LAN."""
import fleet_multi as fm

hosts = ["192.168.1.1", "127.0.0.1"]
results, started = fm.scan_fleet(hosts)
print(f"scanned: {started}")
for r in results:
    print(f"{r.host:15} status={r.status:5} latency={r.latency_ms}ms "
          f"open={r.open_ports} closed={r.closed_ports}")
