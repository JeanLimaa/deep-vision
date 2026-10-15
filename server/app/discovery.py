"""Anuncio do servidor na rede local (mDNS / DNS-SD).

O firmware procura o servico ``_avs._tcp`` e se conecta ao IP anunciado. Assim
o endereco do PC nao fica gravado na placa: trocar de rede, ou o roteador dar
outro IP ao PC, nao exige regravar nada.

O PC costuma ter varias interfaces (Wi-Fi, VirtualBox, WSL, VPN). Todas sao
anunciadas; o firmware escolhe a que esta na mesma sub-rede que ele.
"""

from __future__ import annotations

import logging
import re
import socket

import ifaddr
from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

SERVICE_TYPE = "_avs._tcp.local."

log = logging.getLogger(__name__)


def local_ipv4_addresses() -> list[str]:
    """IPv4 das interfaces do PC, sem loopback e sem link-local (169.254)."""
    addresses: set[str] = set()
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            if not ip.is_IPv4:
                continue
            address = str(ip.ip)
            if address.startswith(("127.", "169.254.")):
                continue
            addresses.add(address)
    return sorted(addresses)


def _mdns_hostname() -> str:
    # Rotulo DNS so aceita letras, digitos e hifen; nomes de PC no Windows
    # costumam ter maiusculas e sublinhado.
    label = re.sub(r"[^a-z0-9-]", "-", socket.gethostname().lower()).strip("-") or "avs"
    return f"{label}.local."


class ServiceAnnouncer:
    def __init__(self, name: str, port: int) -> None:
        self._name = name
        self._port = port
        self._zeroconf: AsyncZeroconf | None = None
        self._info: AsyncServiceInfo | None = None

    async def start(self) -> None:
        addresses = local_ipv4_addresses()
        if not addresses:
            log.warning("Sem interface de rede IPv4; o servidor nao sera anunciado (mDNS)")
            return
        self._info = AsyncServiceInfo(
            SERVICE_TYPE,
            f"{self._name}.{SERVICE_TYPE}",
            port=self._port,
            parsed_addresses=addresses,
            server=_mdns_hostname(),
            properties={"path": "/api/v1/stream"},
        )
        try:
            self._zeroconf = AsyncZeroconf(ip_version=IPVersion.V4Only)
            await self._zeroconf.async_register_service(self._info, allow_name_change=True)
        except OSError as exc:
            # Sem o anuncio o sistema continua funcionando: o firmware ainda
            # aceita o IP digitado no portal ou fixado em secrets.h.
            log.warning("Falha ao anunciar o servidor na rede (mDNS): %s", exc)
            await self._close()
            return
        log.info(
            "Anunciado na rede como %s | porta %d | IPs %s",
            SERVICE_TYPE,
            self._port,
            ", ".join(addresses),
        )

    async def stop(self) -> None:
        if self._zeroconf is not None and self._info is not None:
            await self._zeroconf.async_unregister_service(self._info)
        await self._close()

    async def _close(self) -> None:
        if self._zeroconf is not None:
            await self._zeroconf.async_close()
            self._zeroconf = None
