"""Estado por dispositivo de borda.

Cada ESP32 conectado tem seu proprio rastreador, filtro de sonar e estatisticas.
Isolar esse estado aqui permite mais de um dispositivo no mesmo servidor (por
exemplo, prototipo e simulador lado a lado durante os testes) sem que um
interfira no outro.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field

from app.core.schemas import ActionOut, SessionStateOut
from app.core.throttle import MovingAverage, RateLimiter
from app.core.types import Milliseconds, ProximityReading, TrackedObject, Zone, now_ms
from app.narration.composer import NarrationComposer
from app.proximity.fusion import ProximityFusion
from app.proximity.zones import MedianFilter, ZoneClassifier
from app.settings import Settings
from app.vision.tracking import IouTracker


@dataclass(slots=True)
class DeviceStats:
    frames_received: int = 0
    frames_processed: int = 0
    frames_dropped: int = 0
    inference_ms: MovingAverage = field(default_factory=lambda: MovingAverage(30))
    end_to_end_ms: MovingAverage = field(default_factory=lambda: MovingAverage(30))
    _fps_window: deque[int] = field(default_factory=lambda: deque(maxlen=30))

    def record_frame(self) -> None:
        self.frames_received += 1
        self._fps_window.append(now_ms())

    def record_processed(self, inference_ms: float, end_to_end_ms: float) -> None:
        self.frames_processed += 1
        self.inference_ms.add(inference_ms)
        self.end_to_end_ms.add(end_to_end_ms)

    def record_dropped(self) -> None:
        self.frames_dropped += 1

    @property
    def fps(self) -> float:
        if len(self._fps_window) < 2:
            return 0.0
        span_ms = self._fps_window[-1] - self._fps_window[0]
        return (len(self._fps_window) - 1) * 1000.0 / span_ms if span_ms > 0 else 0.0


class DeviceSession:
    """Sessao de um dispositivo: percepcao, estado e canal de saida."""

    def __init__(self, device_id: str, settings: Settings) -> None:
        self.device_id = device_id
        self.settings = settings

        # Percepcao (estado temporal proprio de cada dispositivo).
        self.tracker = IouTracker(settings.tracking)
        self.zones = ZoneClassifier(settings.proximity)
        self.sonar_filter = MedianFilter(window=5)
        self.fusion = ProximityFusion(settings.proximity)
        self.composer = NarrationComposer(settings.narration, settings.proximity)

        # Controle de fluxo: descarta quadros acima da taxa configurada em vez
        # de acumular fila -- em video ao vivo, quadro antigo nao tem valor.
        self.inference_limiter = RateLimiter(settings.vision.max_inference_fps)

        # Estado operacional.
        self.active = True
        self.connected = False
        self.last_seen_ms: Milliseconds | None = None
        self.battery_pct: float | None = None
        self.rssi_dbm: int | None = None
        self.firmware: str | None = None
        self.stats = DeviceStats()

        # Ultimo resultado, para o painel e para os comandos de voz.
        self.last_tracks: list[TrackedObject] = []
        self.last_annotated_jpeg: bytes | None = None
        self.last_raw_jpeg: bytes | None = None
        self.frame_event = asyncio.Event()

        # Canal de saida. Em WebSocket o transporte esta presente; em HTTP as
        # acoes ficam pendentes ate o proximo POST do dispositivo.
        self.transport = None
        self.pending_actions: deque[ActionOut] = deque(maxlen=16)

    # ------------------------------------------------------------------ estado

    def touch(self) -> None:
        self.last_seen_ms = now_ms()

    def apply_sonar(self, sensor_id: str, distance_cm: float | None) -> ProximityReading:
        """Filtra e classifica uma leitura crua vinda do firmware."""
        distance_m = None if distance_cm is None else distance_cm / 100.0
        filtered = self.sonar_filter.push(sensor_id, distance_m)
        reading = ProximityReading(
            sensor_id=sensor_id,
            distance_m=None if filtered is None else round(filtered, 3),
            zone=self.zones.classify(sensor_id, filtered),
            measured_at_ms=now_ms(),
        )
        self.fusion.update(reading)
        return reading

    @property
    def zone(self) -> Zone:
        return self.fusion.zone(now_ms())

    @property
    def nearest_distance_m(self) -> float | None:
        nearest = self.fusion.nearest(now_ms())
        return nearest.distance_m if nearest else None

    def set_active(self, active: bool) -> None:
        """Botao start/stop. Pausar limpa o estado temporal para nao anunciar
        objetos vistos antes da pausa ao retomar."""
        self.active = active
        if not active:
            self.tracker.reset()
            self.composer.reset()
            self.last_tracks = []

    def reset(self) -> None:
        self.tracker.reset()
        self.composer.reset()
        self.fusion.reset()
        self.zones.reset()
        self.sonar_filter.reset()
        self.last_tracks = []

    # ------------------------------------------------------------------ saida

    def queue_action(self, action: ActionOut) -> None:
        self.pending_actions.append(action)

    def drain_actions(self) -> list[ActionOut]:
        actions = list(self.pending_actions)
        self.pending_actions.clear()
        return actions

    def snapshot(self, volume_step: int) -> SessionStateOut:
        return SessionStateOut(
            device_id=self.device_id,
            online=self.connected,
            active=self.active,
            volume_step=volume_step,
            zone=self.zone,
            nearest_distance_m=self.nearest_distance_m,
            frames_received=self.stats.frames_received,
            frames_processed=self.stats.frames_processed,
            frames_dropped=self.stats.frames_dropped,
            avg_inference_ms=round(self.stats.inference_ms.value, 1),
            avg_end_to_end_ms=round(self.stats.end_to_end_ms.value, 1),
            fps=round(self.stats.fps, 1),
            last_seen_ms=self.last_seen_ms,
            battery_pct=self.battery_pct,
            rssi_dbm=self.rssi_dbm,
        )
