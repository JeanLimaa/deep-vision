from __future__ import annotations

from types import SimpleNamespace

from app import discovery


def _adapter(*addresses: str | tuple[str, int, int]) -> SimpleNamespace:
    ips = []
    for address in addresses:
        is_ipv4 = isinstance(address, str)
        ips.append(SimpleNamespace(ip=address, is_IPv4=is_ipv4))
    return SimpleNamespace(ips=ips)


def test_local_addresses_skip_loopback_link_local_and_ipv6(monkeypatch):
    adapters = [
        _adapter("127.0.0.1"),
        _adapter("192.168.0.102", ("fe80::1", 0, 3)),
        _adapter("169.254.10.20"),
        _adapter("172.26.155.146", "192.168.0.102"),
    ]
    monkeypatch.setattr(discovery.ifaddr, "get_adapters", lambda: adapters)

    assert discovery.local_ipv4_addresses() == ["172.26.155.146", "192.168.0.102"]


def test_mdns_hostname_is_a_valid_dns_label(monkeypatch):
    monkeypatch.setattr(discovery.socket, "gethostname", lambda: "PC_do_Jean")

    assert discovery._mdns_hostname() == "pc-do-jean.local."
