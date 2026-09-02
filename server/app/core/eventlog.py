"""Registro dos eventos em JSON Lines.

Serve de trilha de auditoria e, principalmente, de fonte de dados para a
avaliacao quantitativa exigida pela metodologia do TCC: latencias, taxa de
quadros, deteccoes por classe e alertas disparados saem todos deste arquivo.
Ver ``training/analyze_events.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from app.core.events import Event, EventBus, EventType
from app.settings import StorageSettings

log = logging.getLogger(__name__)

# Eventos de alta frequencia que so poluiriam o arquivo.
_SKIPPED = frozenset({EventType.FRAME_RECEIVED})


class EventLogger:
    """Consome o barramento e grava cada evento como uma linha JSON."""

    def __init__(self, bus: EventBus, settings: StorageSettings) -> None:
        self._bus = bus
        self._settings = settings
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if not self._settings.log_events or self._task is not None:
            return
        self._settings.event_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._run(), name="event-logger")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        path = self._settings.event_log_path
        async with self._bus.subscription() as queue:
            while True:
                event: Event = await queue.get()
                if event.type in _SKIPPED:
                    continue
                line = json.dumps(event.to_json(), ensure_ascii=False)
                try:
                    await asyncio.to_thread(_append_line, path, line)
                except OSError as exc:  # pragma: no cover
                    log.warning("Nao foi possivel gravar o log de eventos: %s", exc)


def _append_line(path, line: str) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
