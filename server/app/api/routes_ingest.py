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

import asyncio
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
    # A visao roda numa tarefa propria. Assim o laco abaixo nunca espera a
    # inferencia: telemetria do sonar e comandos sao tratados na hora, e um
    # quadro que chega enquanto outro esta sendo inferido substitui o que
    # estava na espera, em vez de entrar numa fila.
    mailbox = LatestFrame()
    worker = asyncio.create_task(_process_frames(container, session, websocket, mailbox))

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if (payload := message.get("bytes")) is not None:
                await _handle_binary(container, session, payload, websocket, mailbox)
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
        worker.cancel()
        transport.mark_closed()
        await container.registry.detach(device_id)
        log.info("Dispositivo '%s' desconectado", device_id)


class LatestFrame:
    """Caixa de um lugar so: o quadro novo toma o lugar do que ainda espera.

    Antes, o laco de recepcao inferia cada quadro antes de ler a mensagem
    seguinte. Com a inferencia (~140 ms) mais lenta que o intervalo entre
    quadros (125 ms), os quadros se acumulavam no socket e a latencia crescia
    sem limite -- 5 s depois de 100 s de uso, medido. Em video ao vivo, so o
    quadro mais recente interessa.
    """

    def __init__(self) -> None:
        self._frame: Frame | None = None
        self._ready = asyncio.Event()

    def put(self, frame: Frame) -> Frame | None:
        """Guarda o quadro e devolve o que foi substituido, se havia um."""
        replaced, self._frame = self._frame, frame
        self._ready.set()
        return replaced

    async def get(self) -> Frame:
        await self._ready.wait()
        self._ready.clear()
        frame, self._frame = self._frame, None
        assert frame is not None
        return frame


async def _process_frames(
    container: Container, session, websocket: WebSocket, mailbox: LatestFrame
) -> None:
    """Infere o quadro mais recente, um de cada vez, enquanto a conexao durar."""
    try:
        while True:
            frame = await mailbox.get()
            result = await container.pipeline.process_frame(session, frame)
            # Em WebSocket as acoes ja foram entregues em tempo real; devolve so o
            # resumo, util para o firmware ajustar a taxa de captura.
            await _send_ack(
                websocket,
                result.sequence,
                result.dropped,
                len(result.detections),
                result.inference_ms,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - conexao caiu no meio de um envio
        log.debug("Processamento de quadros de '%s' encerrado: %s", session.device_id, exc)


async def _send_ack(
    websocket: WebSocket, sequence: int, dropped: bool, objects: int, inference_ms: float
) -> None:
    await websocket.send_text(
        json.dumps(
            {
                "type": "frame_ack",
                "sequence": sequence,
                "dropped": dropped,
                "objects": objects,
                "inference_ms": inference_ms,
            }
        )
    )


async def _handle_binary(
    container: Container, session, payload: bytes, websocket: WebSocket, mailbox: LatestFrame
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
        replaced = mailbox.put(frame)
        if replaced is not None:
            # O substituido nunca sera inferido. O ack com dropped=True e o
            # sinal para o dispositivo reduzir a taxa de envio.
            session.stats.record_frame()
            session.stats.record_dropped()
            await _send_ack(websocket, replaced.sequence, True, 0, 0.0)
    elif kind == FRAME_MAGIC_AUDIO and container.stt.available:
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
