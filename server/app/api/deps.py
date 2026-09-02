"""Dependencias compartilhadas pelas rotas."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from app.container import Container
from app.devices.session import DeviceSession


def get_container(request: Request) -> Container:
    return request.app.state.container


ContainerDep = Annotated[Container, Depends(get_container)]


def verify_token(
    container: ContainerDep,
    x_device_token: Annotated[str | None, Header()] = None,
) -> None:
    """Token compartilhado com o firmware.

    Vazio na configuracao = autenticacao desligada, o que e o padrao em
    desenvolvimento e em rede local isolada.
    """
    expected = container.settings.server.device_token
    if expected and x_device_token != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token de dispositivo invalido")


TokenDep = Annotated[None, Depends(verify_token)]


def resolve_session(container: Container, device_id: str | None) -> DeviceSession:
    """Sessao pelo id; sem id, usa o dispositivo ativo mais recente."""
    if device_id:
        return container.registry.get_or_create(device_id)
    session = container.registry.primary()
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nenhum dispositivo conectado")
    return session
