"""Classificacao das zonas de proximidade (Secao 5.4 do TCC).

O artigo define apenas duas fronteiras -- acima de 1,5 m o sistema apenas
monitora, abaixo de 0,8 m dispara alerta prioritario. A faixa entre 0,8 m e
1,5 m ficava indefinida, o que na pratica produziria um salto abrupto de
"silencio" para "alerta continuo". Aqui ela e tratada como ``WARNING``, com
alerta pulsante mais lento, e as transicoes usam histerese para nao oscilar
quando a leitura fica exatamente sobre a fronteira.
"""

from __future__ import annotations

from app.core.types import ProximityReading, Zone, now_ms
from app.settings import ProximitySettings

# Padrao de tom associado a cada zona; o firmware traduz em bipes.
ZONE_TONES: dict[Zone, str | None] = {
    Zone.SAFE: None,
    Zone.WARNING: "pulse_slow",
    Zone.CRITICAL: "pulse_fast",
}


class ZoneClassifier:
    """Converte distancias em zonas, com histerese e estado por sensor."""

    def __init__(self, settings: ProximitySettings) -> None:
        self._settings = settings
        self._current: dict[str, Zone] = {}

    def classify(self, sensor_id: str, distance_m: float | None) -> Zone:
        s = self._settings
        previous = self._current.get(sensor_id, Zone.SAFE)

        in_range = distance_m is not None and (
            s.min_valid_distance_m <= distance_m <= s.max_valid_distance_m
        )
        if not in_range:
            # Sem eco valido: assume caminho livre, mas nunca "sobe" de zona.
            self._current[sensor_id] = Zone.SAFE
            return Zone.SAFE

        # Sair de uma zona mais grave exige folga extra (histerese).
        critical_limit = s.critical_distance_m
        warning_limit = s.warning_distance_m
        if previous is Zone.CRITICAL:
            critical_limit += s.hysteresis_m
        if previous in (Zone.CRITICAL, Zone.WARNING):
            warning_limit += s.hysteresis_m

        if distance_m < critical_limit:
            zone = Zone.CRITICAL
        elif distance_m < warning_limit:
            zone = Zone.WARNING
        else:
            zone = Zone.SAFE

        self._current[sensor_id] = zone
        return zone

    def reading(self, sensor_id: str, distance_cm: float | None) -> ProximityReading:
        """Cria a leitura ja classificada a partir do valor cru em centimetros."""
        distance_m = None if distance_cm is None else round(distance_cm / 100.0, 3)
        if distance_m is not None and distance_m > self._settings.max_valid_distance_m:
            distance_m = None
        return ProximityReading(
            sensor_id=sensor_id,
            distance_m=distance_m,
            zone=self.classify(sensor_id, distance_m),
            measured_at_ms=now_ms(),
        )

    def reset(self, sensor_id: str | None = None) -> None:
        if sensor_id is None:
            self._current.clear()
        else:
            self._current.pop(sensor_id, None)


class MedianFilter:
    """Filtro de mediana por sensor.

    O HC-SR04 produz outliers isolados (eco espurio, superficie obliqua). A
    mediana remove o pico sem introduzir o atraso de uma media longa.
    O filtro tambem existe no firmware; aqui ele protege contra leituras
    corrompidas na rede.
    """

    def __init__(self, window: int = 5) -> None:
        self.window = max(1, window | 1)  # sempre impar
        self._buffers: dict[str, list[float]] = {}

    def push(self, sensor_id: str, distance_m: float | None) -> float | None:
        buffer = self._buffers.setdefault(sensor_id, [])
        if distance_m is None:
            buffer.clear()
            return None
        buffer.append(distance_m)
        if len(buffer) > self.window:
            del buffer[0]
        return sorted(buffer)[len(buffer) // 2]

    def reset(self) -> None:
        self._buffers.clear()
