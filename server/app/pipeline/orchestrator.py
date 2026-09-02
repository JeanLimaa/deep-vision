"""Orquestrador do pipeline Edge-to-Cloud.

Caminho de um quadro:

    JPEG da borda -> decodificacao -> pre-processamento -> deteccao (YOLO)
    -> rastreamento -> direcao/distancia -> fusao com o sonar
    -> composicao da narracao -> fila de fala -> destinos de audio

O que nao pode acontecer aqui: bloquear o laco de eventos. A inferencia e a
codificacao JPEG rodam em threads, e quadros que chegam acima da taxa util sao
descartados em vez de enfileirados -- em video ao vivo, quadro atrasado e pior
que quadro perdido.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace

from app.audio.output import AudioOutputService
from app.core.events import Event, EventBus, EventType
from app.core.schemas import DetectionOut, FrameResultOut, TelemetryIn
from app.core.types import Frame, Priority, TrackedObject, Utterance, Zone, now_ms
from app.devices.registry import DeviceRegistry
from app.devices.session import DeviceSession
from app.settings import Settings
from app.vision.annotate import draw_tracks, encode_jpeg
from app.vision.detector import ObjectDetector
from app.vision.labels import label_pt
from app.vision.preprocessing import FramePreprocessor, resize_keep_aspect
from app.vision.spatial import SpatialEstimator

log = logging.getLogger(__name__)


class AssistivePipeline:
    """Une percepcao, decisao e saida. Componentes entram por injecao."""

    def __init__(
        self,
        settings: Settings,
        detector: ObjectDetector,
        preprocessor: FramePreprocessor,
        spatial: SpatialEstimator,
        audio: AudioOutputService,
        registry: DeviceRegistry,
        bus: EventBus,
    ) -> None:
        self._settings = settings
        self._detector = detector
        self._preprocessor = preprocessor
        self._spatial = spatial
        self._audio = audio
        self._registry = registry
        self._bus = bus
        # Uma inferencia por vez: o modelo e o gargalo, paralelizar so aumentaria
        # a latencia de cada quadro sem elevar a vazao.
        self._inference_slot = asyncio.Semaphore(1)

    @property
    def detector(self) -> ObjectDetector:
        return self._detector

    # ------------------------------------------------------------------ quadro

    async def process_frame(self, session: DeviceSession, frame: Frame) -> FrameResultOut:
        session.touch()
        session.stats.record_frame()

        if not session.active:
            return self._dropped(frame, session)

        if not session.inference_limiter.allow(session.device_id, frame.received_at_ms):
            session.stats.record_dropped()
            return self._dropped(frame, session)

        image = self._preprocessor.decode(frame.jpeg)
        if image is None:
            session.stats.record_dropped()
            log.debug(
                "Quadro %s de '%s' descartado: JPEG invalido",
                frame.sequence,
                session.device_id,
            )
            return self._dropped(frame, session)

        started = time.perf_counter()
        tracks, inference_ms, width = await self._infer(session, image)
        total_ms = (time.perf_counter() - started) * 1000.0
        # Sem relogios sincronizados, a latencia de rede nao e mensuravel; nesse
        # caso reporta-se apenas o tempo gasto dentro do servidor.
        network_ms = frame.latency_ms()
        end_to_end_ms = (network_ms + total_ms) if network_ms is not None else total_ms
        session.stats.record_processed(inference_ms, end_to_end_ms)
        session.last_tracks = tracks

        await self._render_preview(session, image, tracks)
        self._narrate_objects(session, tracks)

        detections_out = [_to_detection_out(track) for track in tracks]
        await self._bus.publish(
            Event(
                EventType.DETECTIONS,
                {
                    "sequence": frame.sequence,
                    "detections": [d.model_dump() for d in detections_out],
                    "inference_ms": round(inference_ms, 1),
                    "end_to_end_ms": round(end_to_end_ms, 1),
                    "network_ms": None if network_ms is None else round(network_ms, 1),
                    "fps": round(session.stats.fps, 1),
                    "width": width,
                },
                device_id=session.device_id,
            )
        )

        return FrameResultOut(
            device_id=session.device_id,
            sequence=frame.sequence,
            detections=detections_out,
            inference_ms=round(inference_ms, 1),
            total_ms=round(total_ms, 1),
            actions=session.drain_actions(),
        )

    async def _infer(
        self, session: DeviceSession, image
    ) -> tuple[list[TrackedObject], float, int]:
        """Roda o detector fora do laco de eventos e converte para rastros."""
        scaled, scale = resize_keep_aspect(image, self._settings.vision.inference_size)
        processed = self._preprocessor.apply(scaled)

        async with self._inference_slot:
            started = time.perf_counter()
            detections = await asyncio.to_thread(self._detector.detect, processed)
            inference_ms = (time.perf_counter() - started) * 1000.0

        # Reprojeta as caixas para as coordenadas do quadro original.
        if scale != 1.0:
            factor = 1.0 / scale
            detections = [
                replace(d, box=d.box.scaled(factor, factor)) for d in detections
            ]

        ignored = set(self._settings.vision.ignored_labels)
        if ignored:
            detections = [d for d in detections if d.label not in ignored]

        width = image.shape[1]
        at_ms = now_ms()
        tracks = session.tracker.update(detections, at_ms)
        for track in tracks:
            self._spatial.annotate(track, width)
        session.fusion.refine_tracks(tracks, at_ms)
        return tracks, inference_ms, width

    async def _render_preview(self, session: DeviceSession, image, tracks) -> None:
        """Gera o quadro anotado do painel. Sem assinantes, nao gasta CPU."""
        if self._bus.subscriber_count == 0 and not self._settings.storage.save_annotated_frames:
            return
        annotated = await asyncio.to_thread(
            draw_tracks, image, tracks, session.zone, session.nearest_distance_m
        )
        session.last_annotated_jpeg = await asyncio.to_thread(encode_jpeg, annotated, 78)
        session.frame_event.set()
        session.frame_event.clear()

        if self._settings.storage.save_annotated_frames and session.last_annotated_jpeg:
            path = self._settings.storage.snapshots_dir / f"{session.device_id}-{now_ms()}.jpg"
            await asyncio.to_thread(path.write_bytes, session.last_annotated_jpeg)

    def _narrate_objects(self, session: DeviceSession, tracks: list[TrackedObject]) -> None:
        for utterance in session.composer.compose_objects(tracks):
            self._audio.submit(utterance)

    def _dropped(self, frame: Frame, session: DeviceSession) -> FrameResultOut:
        return FrameResultOut(
            device_id=session.device_id,
            sequence=frame.sequence,
            dropped=True,
            actions=session.drain_actions(),
        )

    # -------------------------------------------------------------- telemetria

    async def process_telemetry(self, session: DeviceSession, telemetry: TelemetryIn) -> Zone:
        """Aplica leituras do sonar e dispara alerta se a zona piorar.

        O firmware ja alerta localmente em risco imediato (Secao 5.4); este
        caminho acrescenta a fala descritiva, que depende da rede.
        """
        session.touch()
        session.battery_pct = telemetry.battery_pct
        session.rssi_dbm = telemetry.rssi_dbm
        session.firmware = telemetry.firmware or session.firmware

        previous_zone = session.zone
        for sensor in telemetry.sensors:
            reading = session.apply_sonar(sensor.sensor_id, sensor.distance_cm)
            await self._bus.publish(
                Event(EventType.PROXIMITY, reading, device_id=session.device_id)
            )

        current_zone = session.zone
        if not session.active:
            return current_zone

        if current_zone is not previous_zone:
            await self._bus.publish(
                Event(
                    EventType.ZONE_CHANGED,
                    {"from": previous_zone.value, "to": current_zone.value},
                    device_id=session.device_id,
                )
            )

        # Reanuncia enquanto a zona for de risco; o cooldown da fila evita spam.
        if current_zone is not Zone.SAFE:
            nearest = session.fusion.nearest(now_ms())
            utterance = session.composer.compose_obstacle(nearest, current_zone)
            if utterance is not None:
                self._audio.submit(utterance)

        return current_zone

    # ------------------------------------------------------------------ botoes

    async def handle_button(self, session: DeviceSession, button: str, action: str) -> str:
        """Traduz os botoes fisicos descritos na Secao 5 do TCC."""
        message = ""
        if button == "start_stop":
            session.set_active(not session.active)
            message = "started" if session.active else "stopped"
            from app.narration.phrases import SYSTEM

            self._audio.clear()
            self._audio.submit(
                Utterance(text=SYSTEM[message], priority=Priority.ANSWER, interrupt=True)
            )
        elif button == "volume_up":
            step = self._audio.adjust_volume(+1)
            message = f"volume:{step}"
            self._announce_volume(step)
        elif button == "volume_down":
            step = self._audio.adjust_volume(-1)
            message = f"volume:{step}"
            self._announce_volume(step)
        elif button == "power" and action in ("long_press", "press"):
            session.set_active(False)
            session.reset()
            self._audio.clear()
            message = "power_off"

        await self._bus.publish(
            Event(
                EventType.BUTTON,
                {"button": button, "action": action, "result": message},
                device_id=session.device_id,
            )
        )
        return message

    def _announce_volume(self, step: int) -> None:
        from app.narration.phrases import SYSTEM

        self._audio.submit(
            Utterance(
                text=SYSTEM["volume_changed"].format(step=step),
                priority=Priority.ANSWER,
                interrupt=True,
            )
        )


def _to_detection_out(track: TrackedObject) -> DetectionOut:
    return DetectionOut(
        label=track.label,
        label_pt=label_pt(track.label),
        confidence=round(track.confidence, 3),
        box=[
            round(track.box.x1, 1),
            round(track.box.y1, 1),
            round(track.box.x2, 1),
            round(track.box.y2, 1),
        ],
        direction=track.direction,
        distance_m=track.distance_m,
        track_id=track.track_id,
    )
