"""Servico de saida de audio: consome a fila de fala e entrega aos destinos.

Roda como uma tarefa unica em segundo plano. Concentrar a reproducao em um so
consumidor garante que as locucoes saiam em ordem de prioridade e nunca
sobrepostas, o que e requisito de usabilidade assistiva.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from app.audio.sinks import AudioSink
from app.audio.tts import TextToSpeech
from app.core.events import Event, EventBus, EventType
from app.core.types import Priority, Utterance
from app.narration.scheduler import SpeechScheduler

log = logging.getLogger(__name__)


class AudioOutputService:
    """Ponte entre ``SpeechScheduler`` (o que falar) e ``AudioSink`` (onde falar)."""

    def __init__(
        self,
        scheduler: SpeechScheduler,
        tts: TextToSpeech,
        sinks: list[AudioSink],
        bus: EventBus,
        default_volume_step: int = 7,
    ) -> None:
        self._scheduler = scheduler
        self._tts = tts
        self._sinks = sinks
        self._bus = bus
        self._volume_step = default_volume_step
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._muted = False
        self._history: list[str] = []

    # ------------------------------------------------------------ ciclo de vida

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="audio-output")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    # ------------------------------------------------------------------ fala

    def submit(self, utterance: Utterance) -> bool:
        """Enfileira uma locucao e acorda o consumidor."""
        accepted = self._scheduler.submit(utterance)
        if accepted:
            self._wakeup.set()
            self._bus.publish_nowait(
                Event(
                    type=EventType.SPEECH_QUEUED,
                    payload={
                        "text": utterance.text,
                        "priority": int(utterance.priority),
                        "tone": utterance.tone,
                        **utterance.metadata,
                    },
                )
            )
        return accepted

    def submit_all(self, utterances: list[Utterance]) -> int:
        return sum(1 for u in utterances if self.submit(u))

    async def say_now(self, text: str, priority: Priority = Priority.ANSWER) -> None:
        """Atalho para respostas diretas a comandos do usuario."""
        self.submit(Utterance(text=text, priority=priority, interrupt=True))

    # --------------------------------------------------------------- controle

    @property
    def volume_step(self) -> int:
        return self._volume_step

    def set_volume_step(self, step: int) -> int:
        self._volume_step = max(0, min(10, step))
        self._muted = self._volume_step == 0
        return self._volume_step

    def adjust_volume(self, delta: int) -> int:
        return self.set_volume_step(self._volume_step + delta)

    @property
    def history(self) -> list[str]:
        return list(self._history)

    def clear(self) -> None:
        self._scheduler.clear()

    # --------------------------------------------------------------- interno

    async def _run(self) -> None:
        while True:
            utterance = self._scheduler.pop()
            if utterance is None:
                self._wakeup.clear()
                await self._wakeup.wait()
                continue
            try:
                await self._deliver(utterance)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("Falha ao entregar locucao: %s", exc)

    async def _deliver(self, utterance: Utterance) -> None:
        log.info("[fala] %s", utterance.text)
        self._history.append(utterance.text)
        del self._history[:-50]

        audio = None
        if not self._muted and self._tts.available:
            audio = await asyncio.to_thread(self._tts.synthesize, utterance.text)

        results = await asyncio.gather(
            *(sink.play(utterance, audio) for sink in self._sinks),
            return_exceptions=True,
        )
        for sink, result in zip(self._sinks, results, strict=False):
            if isinstance(result, Exception):
                log.warning("Destino de audio '%s' falhou: %s", sink.name, result)
