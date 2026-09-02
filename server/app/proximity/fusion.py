"""Fusao entre sensor ultrassonico e visao computacional.

O sonar mede bem, mas nao sabe *o que* mediu. A camera sabe o que ve, mas
estima distancia mal. Combinando os dois, um obstaculo centralizado ganha nome
e distancia confiavel: "cadeira a frente, a oitenta centimetros".
"""

from __future__ import annotations

from app.core.types import Direction, ProximityReading, TrackedObject, Zone
from app.settings import ProximitySettings

# Sensor -> direcao coberta. Um unico sensor frontal e o padrao do prototipo;
# se forem instalados sensores laterais, basta acrescentar entradas aqui.
SENSOR_DIRECTIONS: dict[str, Direction] = {
    "front": Direction.CENTER,
    "center": Direction.CENTER,
    "left": Direction.LEFT,
    "right": Direction.RIGHT,
}


class ProximityFusion:
    """Mantem o estado de proximidade do dispositivo e o cruza com os rastros."""

    def __init__(self, settings: ProximitySettings) -> None:
        self._settings = settings
        self._readings: dict[str, ProximityReading] = {}

    def update(self, reading: ProximityReading) -> None:
        self._readings[reading.sensor_id] = reading

    def readings(self, at_ms: int) -> list[ProximityReading]:
        """Leituras ainda validas -- descarta telemetria obsoleta."""
        ttl = self._settings.reading_ttl_ms
        return [r for r in self._readings.values() if at_ms - r.measured_at_ms <= ttl]

    def zone(self, at_ms: int) -> Zone:
        """Pior zona entre os sensores validos."""
        valid = self.readings(at_ms)
        if not valid:
            return Zone.SAFE
        return max((r.zone for r in valid), key=lambda z: z.severity)

    def nearest(self, at_ms: int) -> ProximityReading | None:
        candidates = [r for r in self.readings(at_ms) if r.distance_m is not None]
        return min(candidates, key=lambda r: r.distance_m) if candidates else None

    def reading_for(self, direction: Direction, at_ms: int) -> ProximityReading | None:
        for reading in self.readings(at_ms):
            if SENSOR_DIRECTIONS.get(reading.sensor_id) is direction:
                return reading
        return None

    def refine_tracks(self, tracks: list[TrackedObject], at_ms: int) -> list[TrackedObject]:
        """Substitui a distancia estimada pela medida do sonar quando plausivel.

        So aceita a troca se as duas fontes ja concordarem dentro de uma margem
        ampla; caso contrario provavelmente o sonar mediu outra coisa (uma
        parede atras do objeto, por exemplo) e a substituicao seria enganosa.
        """
        for track in tracks:
            reading = self.reading_for(track.direction, at_ms)
            if reading is None or reading.distance_m is None:
                continue
            estimated = track.distance_m
            if estimated is None or abs(estimated - reading.distance_m) <= _tolerance(estimated):
                track.distance_m = reading.distance_m
        return tracks

    def reset(self) -> None:
        self._readings.clear()


def _tolerance(estimated_m: float) -> float:
    """Margem de concordancia: 60% da distancia estimada, no minimo 0,5 m."""
    return max(0.5, estimated_m * 0.6)
