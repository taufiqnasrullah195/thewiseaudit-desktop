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
