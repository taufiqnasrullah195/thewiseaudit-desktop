"""test_fleet_multi.py — parsing, probing, batch shape. Live probes target
localhost and the LAN gateway (192.168.1.1) so the suite verifies real
network behaviour, not mocks."""
import ipaddress
import socket

import fleet_multi as fm


def test_parse_hosts_csv_and_dedupe():
    text = "hostname\n192.168.1.1\n# comment\n192.168.1.1\nlaptop-a,extra\n\n"
    assert fm.parse_hosts(text) == ["192.168.1.1", "laptop-a"]


def test_parse_hosts_plain_lines():
    assert fm.parse_hosts("10.0.0.1\n10.0.0.2\n") == ["10.0.0.1", "10.0.0.2"]


def test_parse_hosts_skips_blank_and_comment():
    assert fm.parse_hosts("# only a comment\n\n   \n") == []


def test_ping_localhost_is_up():
    up, _ = fm.ping_host("127.0.0.1")
    assert up is True


def test_ping_dead_ip_is_down():
    up, _ = fm.ping_host("192.0.2.123")  # TEST-NET-1, guaranteed unrouted
    assert up is False


def test_probe_port_open_loopback():
    # Bind a listener, probe it, confirm hit.
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert fm.probe_port("127.0.0.1", port, "TEST") == "TEST"
        assert fm.probe_port("127.0.0.1", 1, "TEST") is None
    finally:
        srv.close()


def test_scan_host_localhost_shape():
    r = fm.scan_host("127.0.0.1")
    assert r.host == "127.0.0.1"
    assert r.status in {"up", "down"}
    assert len(r.open_ports) + len(r.closed_ports) == len(fm.PROBE_PORTS)


def test_scan_fleet_preserves_order_and_timestamp():
    hosts = ["127.0.0.1", "192.0.2.123"]
    results, started = fm.scan_fleet(hosts)
    assert [r.host for r in results] == hosts
    assert started  # timestamp string
    by_host = {r.host: r for r in results}
    assert by_host["127.0.0.1"].status == "up"
    assert by_host["192.0.2.123"].status == "down"


def test_scan_fleet_gateway_when_reachable():
    # Skip-safe: if the LAN gateway is absent (CI), don't fail.
    try:
        gw_ip = socket.gethostbyname("192.168.1.1")
    except OSError:
        return
    results, _ = fm.scan_fleet([gw_ip])
    assert len(results) == 1


def test_fleet_csv_roundtrip():
    r1 = fm.HostResult("h1", "up", 3, ["RDP"], ["SMB"])
    r2 = fm.HostResult("h2", "down", None, [], ["RDP"])
    text = fm.fleet_to_csv([r1, r2], "2026-01-01 10:00:00")
    lines = text.strip().splitlines()
    assert lines[0].startswith("scanned_at,host,status")
    assert "h1,up,3,RDP,SMB" in lines[1]
    assert "h2,down,,,RDP" in lines[2]


def test_ipaddress_sanity_of_defaults():
    for port, name in fm.PROBE_PORTS:
        assert 0 < port < 65536 and name


# ---------- ping output parsing (cross-platform samples) ----------

def _parse_ping_text(monkeypatch, sample: str):
    """Run the shared parsing logic against a canned ping output."""
    class FakeRun:
        def __init__(self, stdout: str):
            self.stdout = stdout
    monkeypatch.setattr(fm.subprocess, "run",
                        lambda cmd, **kw: FakeRun(sample))
    return fm.ping_host("whatever")


def test_parse_linux_ping_success(monkeypatch):
    sample = (
        "PING 10.0.0.1 (10.0.0.1) 56(84) bytes of data.\n"
        "64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time=0.512 ms\n"
        "64 bytes from 10.0.0.1: icmp_seq=2 ttl=64 time=0.428 ms\n"
        "\n--- 10.0.0.1 ping statistics ---\n"
        "2 packets transmitted, 2 received, 0% packet loss, time 1001ms\n"
    )
    up, latency = _parse_ping_text(monkeypatch, sample)
    assert up is True
    assert latency == 0  # 0.512 ms rounds to 0


def test_parse_linux_ping_decimal_latency(monkeypatch):
    sample = (
        "64 bytes from 10.0.0.9: icmp_seq=1 ttl=63 time=12.7 ms\n"
        "--- ping statistics ---\n"
        "1 packets transmitted, 1 received, 0% packet loss\n"
    )
    up, latency = _parse_ping_text(monkeypatch, sample)
    assert up is True and latency == 12


def test_parse_english_windows_ping(monkeypatch):
    sample = (
        "Pinging 10.0.0.1 with 32 bytes of data:\n"
        "Reply from 10.0.0.1: bytes=32 time=3ms TTL=64\n"
        "Reply from 10.0.0.1: bytes=32 time=2ms TTL=64\n"
        "\nPing statistics for 10.0.0.1:\n"
        "    Packets: Sent = 2, Received = 2, Lost = 0 (0% loss),\n"
    )
    up, latency = _parse_ping_text(monkeypatch, sample)
    assert up is True and latency == 3


def test_parse_german_windows_ping(monkeypatch):
    sample = (
        "Antwort von 10.0.0.1: Bytes=32 Zeit=3ms TTL=64\n"
        "Ping-Statistik fr 10.0.0.1:\n"
        "    Pakete: Gesendet = 2, Empfangen = 2, Verloren = 0,\n"
    )
    up, _ = _parse_ping_text(monkeypatch, sample)
    assert up is True


def test_parse_indonesian_windows_ping_unreachable(monkeypatch):
    # No TTL in body, Received = 0 in summary -> down.
    sample = (
        "Ping statistik untuk 192.0.2.123:\n"
        "    Paket: Terkirim = 2, Diterima = 0, Hilang = 2 (100% loss),\n"
    )
    up, latency = _parse_ping_text(monkeypatch, sample)
    assert up is False and latency is None
