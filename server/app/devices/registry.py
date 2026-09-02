"""Registro de dispositivos conectados e canal de saida para a borda.

O transporte e abstrato de proposito: um dispositivo conectado por WebSocket
recebe as acoes na hora; um dispositivo em modo HTTP POST (o modo descrito no
artigo) recebe as mesmas acoes na resposta da proxima requisicao. A camada de
cima nao precisa saber qual dos dois esta em uso.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol, runtime_checkable

from app.audio.tts import SynthesizedAudio
from app.core.events import Event, EventBus, EventType
from app.core.schemas import ActionOut, SessionStateOut
from app.core.types import Utterance
from app.devices.session import DeviceSession
from app.settings import Settings

log = logging.getLogger(__name__)

# Cabecalho binario de audio no sentido servidor -> dispositivo.
AUDIO_FRAME_MAGIC = 0x81


@runtime_checkable
class DeviceTransport(Protocol):
    """Canal em tempo real com um dispositivo (implementado pela camada web)."""

    async def send_json(self, payload: dict) -> None: ...

    async def send_bytes(self, payload: bytes) -> None: ...

    @property
    def alive(self) -> bool: ...


class DeviceRegistry:
    """Cria, guarda e notifica as sessoes de dispositivo."""

    def __init__(self, settings: Settings, bus: EventBus) -> None:
        self._settings = settings
        self._bus = bus
        self._sessions: dict[str, DeviceSession] = {}
        self._lock = asyncio.Lock()

    # ----------------------------------------------------------------- acesso

    def get(self, device_id: str) -> DeviceSession | None:
        return self._sessions.get(device_id)

    def get_or_create(self, device_id: str) -> DeviceSession:
        session = self._sessions.get(device_id)
        if session is None:
            session = DeviceSession(device_id, self._settings)
            self._sessions[device_id] = session
            log.info("Sessao criada para o dispositivo '%s'", device_id)
        return session

    @property
    def sessions(self) -> list[DeviceSession]:
        return list(self._sessions.values())

    def primary(self) -> DeviceSession | None:
        """Dispositivo mais recentemente ativo -- usado por comandos sem alvo."""
        candidates = [s for s in self._sessions.values() if s.connected] or self.sessions
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.last_seen_ms or 0)

    def states(self, volume_step: int) -> list[SessionStateOut]:
        return [session.snapshot(volume_step) for session in self._sessions.values()]

    # ------------------------------------------------------------- ciclo de vida

    async def attach(self, device_id: str, transport: DeviceTransport) -> DeviceSession:
        async with self._lock:
            session = self.get_or_create(device_id)
            session.transport = transport
            session.connected = True
            session.touch()
        await self._bus.publish(
            Event(EventType.DEVICE_CONNECTED, {"device_id": device_id}, device_id=device_id)
        )
        return session

    async def detach(self, device_id: str) -> None:
        async with self._lock:
            session = self._sessions.get(device_id)
            if session is None:
                return
            session.transport = None
            session.connected = False
        await self._bus.publish(
            Event(EventType.DEVICE_DISCONNECTED, {"device_id": device_id}, device_id=device_id)
        )

    def mark_seen(self, device_id: str) -> DeviceSession:
        """Usado no modo HTTP, onde nao ha conexao persistente para observar."""
        session = self.get_or_create(device_id)
        session.connected = True
        session.touch()
        return session

    def expire_stale(self, timeout_ms: int = 5000) -> None:
        """Marca como offline quem parou de enviar quadros (modo HTTP)."""
        from app.core.types import now_ms

        current = now_ms()
        for session in self._sessions.values():
            if session.transport is not None:
                continue
            if session.last_seen_ms and current - session.last_seen_ms > timeout_ms:
                session.connected = False

    # ------------------------------------------------------------------ saida

    async def send_action(self, session: DeviceSession, action: ActionOut) -> bool:
        """Entrega imediata via WebSocket, ou enfileira para o proximo POST."""
        transport = session.transport
        if transport is not None and transport.alive:
            try:
                await transport.send_json(action.model_dump(exclude_none=True))
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning("Falha ao enviar acao para '%s': %s", session.device_id, exc)
        session.queue_action(action)
        return False

    async def broadcast_speech(
        self, utterance: Utterance, audio: SynthesizedAudio | None
    ) -> None:
        """Envia uma locucao a todos os dispositivos ativos.

        O envio e para todos porque o modelo do projeto e de um usuario com um
        unico canal de audio -- a fila de fala tambem e global, por isso. Se um
        dia o servidor atender varios usuarios simultaneos, esta e a primeira
        coisa a mudar: fila e destino de audio passam a ser por sessao.
        """
        action = ActionOut(
            type="speak",
            text=utterance.text,
            tone=utterance.tone,
            priority=int(utterance.priority),
            interrupt=utterance.interrupt,
        )
        for session in self._sessions.values():
            if not session.active:
                continue
            await self.send_action(session, action)
            if audio is not None:
                await self._send_audio(session, audio)

    async def _send_audio(self, session: DeviceSession, audio: SynthesizedAudio) -> None:
        """Empacota o audio como quadro binario para o amplificador I2S da borda.

        Dispositivos sem alto-falante simplesmente descartam esse quadro.
        """
        transport = session.transport
        if transport is None or not transport.alive:
            return
        header = bytes([AUDIO_FRAME_MAGIC, _mime_code(audio.mime)])
        try:
            await transport.send_bytes(header + audio.data)
        except Exception as exc:  # noqa: BLE001
            log.debug("Audio nao entregue a '%s': %s", session.device_id, exc)


def _mime_code(mime: str) -> int:
    return {"audio/wav": 0x01, "audio/mpeg": 0x02}.get(mime, 0x00)
