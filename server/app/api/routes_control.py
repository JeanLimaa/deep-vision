"""Controle e observabilidade: comandos, estado e fluxo de eventos."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status

from app.api.deps import ContainerDep, resolve_session
from app.container import Container
from app.core.schemas import (
    CommandResultOut,
    HealthOut,
    SessionStateOut,
    TextCommandIn,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["control"])


@router.get("/health", response_model=HealthOut)
async def health(container: ContainerDep) -> HealthOut:
    container.registry.expire_stale()
    return HealthOut(
        detector=container.detector.name,
        detector_ready=container.detector.ready,
        tts=container.tts.name,
        stt=container.stt.name,
        audio_sinks=[sink.name for sink in container.sinks],
        devices=container.registry.states(container.audio.volume_step),
    )


@router.post("/command", response_model=CommandResultOut)
async def send_command(payload: TextCommandIn, container: ContainerDep) -> CommandResultOut:
    """Comando em texto -- mesmo caminho de um comando de voz transcrito.

    E por aqui que a interacao por voz e exercitada enquanto o microfone do
    prototipo nao esta disponivel.
    """
    session = resolve_session(container, payload.device_id)
    return await container.commands.handle_text(session, payload.text, payload.source)


@router.get("/state", response_model=list[SessionStateOut])
async def state(container: ContainerDep) -> list[SessionStateOut]:
    container.registry.expire_stale()
    return container.registry.states(container.audio.volume_step)


@router.post("/devices/{device_id}/active", response_model=SessionStateOut)
async def set_active(device_id: str, active: bool, container: ContainerDep) -> SessionStateOut:
    """Equivalente remoto do botao start/stop."""
    session = container.registry.get(device_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispositivo desconhecido")
    session.set_active(active)
    return session.snapshot(container.audio.volume_step)


@router.post("/devices/{device_id}/volume", response_model=SessionStateOut)
async def set_volume(device_id: str, step: int, container: ContainerDep) -> SessionStateOut:
    session = container.registry.get(device_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispositivo desconhecido")
    container.audio.set_volume_step(step)
    return session.snapshot(container.audio.volume_step)


@router.websocket("/events")
async def events(websocket: WebSocket) -> None:
    """Fluxo de eventos em tempo real consumido pelo painel web."""
    container: Container = websocket.app.state.container
    await websocket.accept()
    try:
        async with container.bus.subscription() as queue:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    # Mantem a conexao viva atras de proxies com timeout curto.
                    await websocket.send_text(json.dumps({"type": "keepalive"}))
                    continue
                await websocket.send_text(json.dumps(event.to_json(), ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.debug("Fluxo de eventos encerrado: %s", exc)
