"""Entrada de dados vindos da borda (ESP32-CAM).

Dois transportes atendem ao mesmo pipeline:

``POST /api/v1/ingest/frame``
    Exatamente o que o artigo descreve (Secao 5.3): um POST HTTP por quadro
    JPEG. Simples, funciona com qualquer cliente, e a resposta ja devolve as
    acoes pendentes -- e o canal de retorno do modo HTTP.

``WS /api/v1/stream``
    Conexao persistente, quadro binario sem cabecalho HTTP por imagem. Elimina
    o custo de handshake por quadro e abre um canal de volta em tempo real
    (fala e bipes chegam sem esperar o proximo POST). E o transporte
    recomendado, e o que o firmware usa por padrao.
"""

from __future__ import annotations

import json
import logging
import struct

from fastapi import (
    APIRouter,
    Header,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from app.api.deps import ContainerDep, TokenDep
from app.container import Container
from app.core.events import Event, EventType
from app.core.schemas import ButtonEventIn, CommandResultOut, FrameResultOut, TelemetryIn
from app.core.types import Frame, epoch_ms, now_ms

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["ingest"])

# Tipos de quadro binario no sentido dispositivo -> servidor.
FRAME_MAGIC_IMAGE = 0x01
FRAME_MAGIC_AUDIO = 0x02
# type(1) + sequence(8) + timestamp(8)
BINARY_HEADER = struct.Struct(">BQQ")


# --------------------------------------------------------------------- HTTP


@router.post("/ingest/frame", response_model=FrameResultOut)
async def ingest_frame(
    request: Request,
    container: ContainerDep,
    _: TokenDep,
    x_device_id: str = Header(default="esp32cam-01"),
    x_frame_seq: int = Header(default=0),
    x_captured_at: int = Header(default=0),
) -> FrameResultOut:
    """Recebe um quadro JPEG no corpo da requisicao."""
    jpeg = await request.body()
    if not jpeg:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Corpo vazio")

    session = container.registry.mark_seen(x_device_id)
    frame = Frame(
        device_id=x_device_id,
        sequence=x_frame_seq,
        jpeg=jpeg,
        captured_at_ms=x_captured_at or epoch_ms(),
    )
    return await container.pipeline.process_frame(session, frame)


@router.post("/ingest/telemetry")
async def ingest_telemetry(
    telemetry: TelemetryIn, container: ContainerDep, _: TokenDep
) -> dict:
    """Recebe as leituras dos sensores ultrassonicos e o estado do dispositivo."""
    session = container.registry.mark_seen(telemetry.device_id)
    zone = await container.pipeline.process_telemetry(session, telemetry)
    return {
        "zone": zone.value,
        "nearest_distance_m": session.nearest_distance_m,
        "actions": [a.model_dump(exclude_none=True) for a in session.drain_actions()],
    }


@router.post("/ingest/button")
async def ingest_button(event: ButtonEventIn, container: ContainerDep, _: TokenDep) -> dict:
    session = container.registry.mark_seen(event.device_id)
    result = await container.pipeline.handle_button(session, event.button, event.action)
    return {"result": result, "active": session.active}


@router.post("/ingest/audio", response_model=CommandResultOut)
async def ingest_audio(
    request: Request,
    container: ContainerDep,
    _: TokenDep,
    x_device_id: str = Header(default="esp32cam-01"),
    content_type: str = Header(default="audio/wav"),
) -> CommandResultOut:
    """Recebe o audio do comando de voz, transcreve e executa a intencao.

    Enquanto o microfone nao esta montado, este endpoint fica ocioso -- o mesmo
    fluxo e alcancado por ``POST /api/v1/command`` com o texto do comando.
    """
    if not container.stt.available:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Reconhecimento de fala indisponivel; use /api/v1/command",
        )
    audio = await request.body()
    if not audio:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Audio vazio")

    import asyncio

    transcript = await asyncio.to_thread(container.stt.transcribe, audio, content_type)
    if transcript is None:
        return CommandResultOut(understood=False, reply="Nao entendi o comando.")

    session = container.registry.mark_seen(x_device_id)
    return await container.commands.handle_text(session, transcript.text, source="device")


# ---------------------------------------------------------------- WebSocket


@router.websocket("/stream")
async def stream(websocket: WebSocket) -> None:
    """Canal bidirecional com o dispositivo."""
    container: Container = websocket.app.state.container
    device_id = websocket.query_params.get("device_id", "esp32cam-01")
    token = websocket.query_params.get("token", "")

    expected = container.settings.server.device_token
    if expected and token != expected:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    transport = WebSocketTransport(websocket)
    session = await container.registry.attach(device_id, transport)
    log.info("Dispositivo '%s' conectado por WebSocket", device_id)

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if (payload := message.get("bytes")) is not None:
                await _handle_binary(container, session, payload, websocket)
            elif (text := message.get("text")) is not None:
                await _handle_text(container, session, text, websocket)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("Erro na conexao com '%s': %s", device_id, exc)
        await container.bus.publish(
            Event(EventType.ERROR, {"error": str(exc)}, device_id=device_id)
        )
    finally:
        transport.mark_closed()
        await container.registry.detach(device_id)
        log.info("Dispositivo '%s' desconectado", device_id)


async def _handle_binary(
    container: Container, session, payload: bytes, websocket: WebSocket
) -> None:
    """Quadro binario: cabecalho fixo + corpo (JPEG ou audio)."""
    if len(payload) <= BINARY_HEADER.size:
        return
    kind, sequence, timestamp = BINARY_HEADER.unpack_from(payload, 0)
    body = payload[BINARY_HEADER.size :]

    if kind == FRAME_MAGIC_IMAGE:
        frame = Frame(
            device_id=session.device_id,
            sequence=sequence,
            jpeg=body,
            captured_at_ms=timestamp or epoch_ms(),
        )
        result = await container.pipeline.process_frame(session, frame)
        # Em WebSocket as acoes ja foram entregues em tempo real; devolve so o
        # resumo, util para o firmware ajustar a taxa de captura.
        await websocket.send_text(
            json.dumps(
                {
                    "type": "frame_ack",
                    "sequence": result.sequence,
                    "dropped": result.dropped,
                    "objects": len(result.detections),
                    "inference_ms": result.inference_ms,
                }
            )
        )
    elif kind == FRAME_MAGIC_AUDIO and container.stt.available:
        import asyncio

        transcript = await asyncio.to_thread(container.stt.transcribe, body, "audio/wav")
        if transcript is not None:
            await container.commands.handle_text(session, transcript.text, source="device")


async def _handle_text(container: Container, session, raw: str, websocket: WebSocket) -> None:
    """Mensagens de controle em JSON."""
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        return

    kind = message.get("type")
    if kind == "telemetry":
        telemetry = TelemetryIn.model_validate({**message, "device_id": session.device_id})
        await container.pipeline.process_telemetry(session, telemetry)
    elif kind == "button":
        await container.pipeline.handle_button(
            session, message.get("button", ""), message.get("action", "press")
        )
    elif kind == "command":
        await container.commands.handle_text(session, message.get("text", ""), source="device")
    elif kind == "hello":
        session.firmware = message.get("firmware")
        await websocket.send_text(
            json.dumps(
                {
                    "type": "config",
                    "max_fps": container.settings.vision.max_inference_fps,
                    "warning_cm": int(container.settings.proximity.warning_distance_m * 100),
                    "critical_cm": int(container.settings.proximity.critical_distance_m * 100),
                }
            )
        )
    elif kind == "ping":
        await websocket.send_text(json.dumps({"type": "pong", "at_ms": now_ms()}))


class WebSocketTransport:
    """Adaptador de ``WebSocket`` para o protocolo ``DeviceTransport``."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._alive = True

    @property
    def alive(self) -> bool:
        return self._alive

    def mark_closed(self) -> None:
        self._alive = False

    async def send_json(self, payload: dict) -> None:
        await self._websocket.send_text(json.dumps(payload, ensure_ascii=False))

    async def send_bytes(self, payload: bytes) -> None:
        await self._websocket.send_bytes(payload)


__all__ = ["router", "Response"]
