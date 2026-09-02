"""Montagem da aplicacao (injecao de dependencias).

Um unico lugar decide quais implementacoes concretas serao usadas. Trocar YOLO
pelo detector simulado, o alto-falante do servidor pelo do dispositivo ou o TTS
offline pelo online e uma mudanca de configuracao, nao de codigo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.audio.output import AudioOutputService
from app.audio.sinks import AudioSink, build_sinks
from app.audio.tts import TextToSpeech, build_tts
from app.core.eventlog import EventLogger
from app.core.events import EventBus
from app.devices.registry import DeviceRegistry
from app.narration.scheduler import SpeechScheduler
from app.pipeline.orchestrator import AssistivePipeline
from app.settings import Settings
from app.speech.commands import CommandRouter
from app.speech.intents import IntentMatcher
from app.speech.stt import SpeechToText, build_stt
from app.vision.detector import ObjectDetector, build_detector
from app.vision.preprocessing import FramePreprocessor
from app.vision.spatial import SpatialEstimator

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Container:
    """Grafo de objetos da aplicacao, com ciclo de vida explicito."""

    settings: Settings
    bus: EventBus
    registry: DeviceRegistry
    detector: ObjectDetector
    preprocessor: FramePreprocessor
    spatial: SpatialEstimator
    tts: TextToSpeech
    stt: SpeechToText
    sinks: list[AudioSink]
    audio: AudioOutputService
    pipeline: AssistivePipeline
    commands: CommandRouter
    event_logger: EventLogger

    async def start(self) -> None:
        await self.audio.start()
        await self.event_logger.start()
        if self.settings.vision.warmup_on_startup:
            import asyncio

            await asyncio.to_thread(self.detector.warmup)
            log.info("Detector '%s' aquecido.", self.detector.name)

    async def stop(self) -> None:
        await self.audio.stop()
        await self.event_logger.stop()


def build_container(settings: Settings) -> Container:
    """Constroi o grafo completo a partir das configuracoes."""
    settings.ensure_directories()

    bus = EventBus()
    registry = DeviceRegistry(settings, bus)

    detector = build_detector(settings.vision)
    preprocessor = FramePreprocessor(settings.preprocess)
    spatial = SpatialEstimator(settings.camera)

    tts = build_tts(settings.tts)
    stt = build_stt(settings.speech)
    sinks = build_sinks(settings.audio.sinks, bus, registry, _media_url_for)
    scheduler = SpeechScheduler(settings.narration, settings.audio.max_queue)
    audio = AudioOutputService(
        scheduler, tts, sinks, bus, settings.audio.default_volume_step
    )

    pipeline = AssistivePipeline(settings, detector, preprocessor, spatial, audio, registry, bus)
    commands = CommandRouter(IntentMatcher(), pipeline, audio, bus)
    event_logger = EventLogger(bus, settings.storage)

    return Container(
        settings=settings,
        bus=bus,
        registry=registry,
        detector=detector,
        preprocessor=preprocessor,
        spatial=spatial,
        tts=tts,
        stt=stt,
        sinks=sinks,
        audio=audio,
        pipeline=pipeline,
        commands=commands,
        event_logger=event_logger,
    )


def _media_url_for(text: str) -> str:
    """URL estavel para o painel buscar o audio de uma frase ja sintetizada."""
    from urllib.parse import quote

    return f"/api/v1/media/tts?text={quote(text)}"
