"""Midia: previa anotada em MJPEG e audio sintetizado."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import ContainerDep, resolve_session
from app.container import Container

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/media", tags=["media"])

_BOUNDARY = "frame"
# Intervalo maximo entre quadros do MJPEG: mantem a conexao viva quando o
# dispositivo para de transmitir.
_IDLE_TIMEOUT_S = 1.0


@router.get("/preview.mjpg")
async def preview(container: ContainerDep, device_id: str | None = None) -> StreamingResponse:
    """Fluxo MJPEG com as deteccoes desenhadas.

    Usado pelo painel e para gerar as figuras de validacao do trabalho.
    """
    session = resolve_session(container, device_id)
    return StreamingResponse(
        _mjpeg(container, session.device_id),
        media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY}",
        headers={"Cache-Control": "no-store"},
    )


async def _mjpeg(container: Container, device_id: str) -> AsyncIterator[bytes]:
    last_sent: bytes | None = None
    while True:
        session = container.registry.get(device_id)
        if session is None:
            return
        frame = session.last_annotated_jpeg
        if frame and frame is not last_sent:
            last_sent = frame
            yield (
                f"--{_BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                f"Content-Length: {len(frame)}\r\n\r\n"
            ).encode() + frame + b"\r\n"
        try:
            await asyncio.wait_for(session.frame_event.wait(), timeout=_IDLE_TIMEOUT_S)
        except TimeoutError:
            continue


@router.get("/snapshot.jpg")
async def snapshot(container: ContainerDep, device_id: str | None = None) -> Response:
    """Ultimo quadro anotado, como imagem estatica."""
    session = resolve_session(container, device_id)
    if not session.last_annotated_jpeg:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nenhum quadro processado ainda")
    return Response(content=session.last_annotated_jpeg, media_type="image/jpeg")


@router.get("/tts")
async def tts_audio(container: ContainerDep, text: str = Query(min_length=1)) -> Response:
    """Sintetiza (ou recupera do cache) o audio de uma frase.

    O painel usa esta rota para reproduzir a fala no navegador -- o substituto
    do fone de ouvido enquanto o hardware de audio nao esta montado.
    """
    if not container.tts.available:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "TTS indisponivel")
    audio = await asyncio.to_thread(container.tts.synthesize, text)
    if audio is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Falha na sintese")
    return Response(
        content=audio.data,
        media_type=audio.mime,
        headers={"Cache-Control": "public, max-age=3600"},
    )
