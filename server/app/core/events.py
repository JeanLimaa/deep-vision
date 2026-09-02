"""Barramento de eventos assincrono em processo.

O pipeline publica eventos; dashboard, log e testes apenas assinam. Isso evita
que a camada de visao conheca a camada de apresentacao.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any

from app.core.types import now_ms

log = logging.getLogger(__name__)


class EventType(str, Enum):
    DEVICE_CONNECTED = "device.connected"
    DEVICE_DISCONNECTED = "device.disconnected"
    FRAME_RECEIVED = "frame.received"
    DETECTIONS = "vision.detections"
    PROXIMITY = "proximity.reading"
    ZONE_CHANGED = "proximity.zone_changed"
    SPEECH_QUEUED = "audio.speech_queued"
    SPEECH_PLAYED = "audio.speech_played"
    TONE = "audio.tone"
    COMMAND_RECEIVED = "speech.command_received"
    COMMAND_HANDLED = "speech.command_handled"
    BUTTON = "device.button"
    SESSION_STATE = "session.state"
    ERROR = "system.error"


@dataclass(frozen=True, slots=True)
class Event:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    device_id: str | None = None
    at_ms: int = field(default_factory=now_ms)

    def to_json(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "device_id": self.device_id,
            "at_ms": self.at_ms,
            "payload": _jsonable(self.payload),
        }


def _jsonable(value: Any) -> Any:
    """Converte dataclasses/enums aninhados em estruturas serializaveis."""
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return value


class EventBus:
    """Publish/subscribe com filas limitadas por assinante.

    Assinante lento descarta os eventos mais antigos em vez de travar o
    pipeline de video -- prioridade e sempre a latencia da deteccao.
    """

    def __init__(self, queue_size: int = 256) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._lock = asyncio.Lock()

    async def publish(self, event: Event) -> None:
        async with self._lock:
            subscribers = tuple(self._subscribers)
        for queue in subscribers:
            _offer(queue, event)

    def publish_nowait(self, event: Event) -> None:
        """Versao sincrona, segura para chamar de dentro do loop de eventos."""
        for queue in tuple(self._subscribers):
            _offer(queue, event)

    @contextlib.asynccontextmanager
    async def subscription(self) -> AsyncIterator[asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers.add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    async def stream(self) -> AsyncIterator[Event]:
        async with self.subscription() as queue:
            while True:
                yield await queue.get()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


def _offer(queue: asyncio.Queue[Event], event: Event) -> None:
    while True:
        try:
            queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - corrida improvavel
                return
