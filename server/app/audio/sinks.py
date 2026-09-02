"""Destinos de audio.

O prototipo final entrega o audio por um fone acoplado ao dispositivo. Como
esse hardware ainda nao esta montado, os destinos abaixo permitem validar o
sistema completo hoje -- e, quando o alto-falante chegar, basta acrescentar
``device`` a lista ``AVS_AUDIO__SINKS``; nada mais muda.

    host       alto-falante do proprio servidor
    dashboard  navegador (painel web reproduz o audio recebido)
    device     dispositivo de borda (ESP32 + amplificador I2S)
    null       descarta o audio, mantendo apenas os eventos textuais
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol, runtime_checkable

from app.audio.tts import SynthesizedAudio
from app.core.events import Event, EventBus, EventType
from app.core.types import Utterance

log = logging.getLogger(__name__)


@runtime_checkable
class AudioSink(Protocol):
    name: str

    async def play(self, utterance: Utterance, audio: SynthesizedAudio | None) -> None: ...


class NullSink:
    """Descarta o audio. A locucao ainda aparece nos eventos e nos logs."""

    name = "null"

    async def play(self, utterance: Utterance, audio: SynthesizedAudio | None) -> None:
        log.debug("[null] %s", utterance.text)


class HostSpeakerSink:
    """Reproduz no alto-falante da maquina que roda o servidor.

    Usa ``sounddevice`` quando disponivel (multiplataforma) e cai para
    ``winsound`` no Windows, que nao exige dependencia extra.
    """

    name = "host"

    def __init__(self) -> None:
        self._backend = self._detect_backend()
        # Uma unica reproducao por vez: evita vozes sobrepostas.
        self._lock = asyncio.Lock()

    @staticmethod
    def _detect_backend() -> str:
        try:
            import sounddevice  # noqa: F401
            import soundfile  # noqa: F401

            return "sounddevice"
        except Exception:  # noqa: BLE001
            pass
        try:
            import winsound  # noqa: F401

            return "winsound"
        except Exception:  # noqa: BLE001
            return "none"

    async def play(self, utterance: Utterance, audio: SynthesizedAudio | None) -> None:
        if audio is None or self._backend == "none":
            return
        async with self._lock:
            await asyncio.to_thread(self._play_blocking, audio)

    def _play_blocking(self, audio: SynthesizedAudio) -> None:
        try:
            if self._backend == "sounddevice":
                self._play_sounddevice(audio)
            elif self._backend == "winsound":
                self._play_winsound(audio)
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha ao reproduzir audio no host: %s", exc)

    @staticmethod
    def _play_sounddevice(audio: SynthesizedAudio) -> None:
        import io

        import sounddevice as sd
        import soundfile as sf

        data, samplerate = sf.read(io.BytesIO(audio.data), dtype="float32")
        sd.play(data, samplerate)
        sd.wait()

    @staticmethod
    def _play_winsound(audio: SynthesizedAudio) -> None:
        import winsound

        if audio.mime != "audio/wav":
            log.debug("winsound so reproduz WAV; audio %s ignorado.", audio.mime)
            return
        winsound.PlaySound(audio.data, winsound.SND_MEMORY)


class DashboardSink:
    """Publica a locucao no barramento; o painel web a reproduz no navegador.

    Serve tanto como substituto do fone quanto como registro visual das falas
    durante os testes -- util para as evidencias do trabalho.
    """

    name = "dashboard"

    def __init__(self, bus: EventBus, media_url_builder=None) -> None:
        self._bus = bus
        self._media_url_builder = media_url_builder

    async def play(self, utterance: Utterance, audio: SynthesizedAudio | None) -> None:
        audio_url = None
        if audio is not None and self._media_url_builder is not None:
            audio_url = self._media_url_builder(utterance.text)
        await self._bus.publish(
            Event(
                type=EventType.SPEECH_PLAYED,
                payload={
                    "text": utterance.text,
                    "priority": int(utterance.priority),
                    "tone": utterance.tone,
                    "audio_url": audio_url,
                    "sink": self.name,
                    **utterance.metadata,
                },
            )
        )


class DeviceSink:
    """Envia a locucao para o dispositivo de borda.

    Em WebSocket o audio segue como quadro binario; em HTTP o dispositivo
    recebe a acao na resposta do proximo POST de imagem. Se o dispositivo nao
    tiver alto-falante, ele simplesmente ignora o quadro de audio e emite os
    bipes locais -- por isso este destino ja pode ficar ativo.
    """

    name = "device"

    def __init__(self, registry) -> None:  # app.devices.registry.DeviceRegistry
        self._registry = registry

    async def play(self, utterance: Utterance, audio: SynthesizedAudio | None) -> None:
        await self._registry.broadcast_speech(utterance, audio)


def build_sinks(names, bus: EventBus, registry, media_url_builder=None) -> list[AudioSink]:
    """Cria os destinos configurados, ignorando os que falharem."""
    sinks: list[AudioSink] = []
    for name in names:
        try:
            if name == "host":
                sinks.append(HostSpeakerSink())
            elif name == "dashboard":
                sinks.append(DashboardSink(bus, media_url_builder))
            elif name == "device":
                sinks.append(DeviceSink(registry))
            else:
                sinks.append(NullSink())
        except Exception as exc:  # noqa: BLE001
            log.warning("Destino de audio '%s' indisponivel: %s", name, exc)
    if not sinks:
        sinks.append(NullSink())
    log.info("Destinos de audio ativos: %s", ", ".join(s.name for s in sinks))
    return sinks
