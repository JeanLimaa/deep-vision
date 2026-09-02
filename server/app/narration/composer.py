"""Traduz o estado percebido (rastros + sonar) em locucoes.

Regra central: falar pouco e no momento certo. O sistema so anuncia um objeto
quando ele e novo, quando muda de lado ou quando se aproxima o bastante para
mudar de zona. Fora isso, silencio.
"""

from __future__ import annotations

from app.core.types import (
    Direction,
    Priority,
    ProximityReading,
    TrackedObject,
    Utterance,
    Zone,
    now_ms,
)
from app.narration import phrases
from app.proximity.fusion import SENSOR_DIRECTIONS
from app.proximity.zones import ZONE_TONES
from app.settings import NarrationSettings, ProximitySettings
from app.vision.labels import is_hazard


class NarrationComposer:
    """Decide o que deve ser dito a partir do quadro corrente."""

    def __init__(self, narration: NarrationSettings, proximity: ProximitySettings) -> None:
        self._settings = narration
        self._proximity = proximity
        self._announced_zone: Zone = Zone.SAFE

    # ---------------------------------------------------------------- objetos

    def compose_objects(self, tracks: list[TrackedObject]) -> list[Utterance]:
        """Locucoes para objetos novos ou que mudaram de posicao relevante."""
        if not self._settings.autonomous_narration:
            return []

        utterances: list[Utterance] = []
        for track in self._sorted_by_relevance(tracks):
            if not self._should_announce(track):
                continue
            utterances.append(
                Utterance(
                    text=phrases.new_object_phrase(track, self._settings.announce_distance),
                    priority=Priority.ALERT if is_hazard(track.label) else Priority.INFO,
                    dedupe_key=f"obj:{track.label}:{track.direction.value}",
                    metadata={
                        "track_id": track.track_id,
                        "label": track.label,
                        "direction": track.direction.value,
                        "distance_m": track.distance_m,
                    },
                )
            )
            track.announced = True
            track.last_announced_direction = track.direction
            track.last_announced_zone = self._track_zone(track)
            if len(utterances) >= self._settings.max_objects_per_utterance:
                break
        return utterances

    def _should_announce(self, track: TrackedObject) -> bool:
        if not track.announced:
            return True
        if track.last_announced_direction is not track.direction:
            return True
        # Reanuncia se o objeto entrou numa zona mais grave desde o ultimo aviso.
        current = self._track_zone(track)
        previous = track.last_announced_zone or Zone.SAFE
        return current.severity > previous.severity

    def _track_zone(self, track: TrackedObject) -> Zone:
        if track.distance_m is None:
            return Zone.SAFE
        if track.distance_m < self._proximity.critical_distance_m:
            return Zone.CRITICAL
        if track.distance_m < self._proximity.warning_distance_m:
            return Zone.WARNING
        return Zone.SAFE

    def _sorted_by_relevance(self, tracks: list[TrackedObject]) -> list[TrackedObject]:
        """Mais perto e mais perigoso primeiro; sem distancia, maior caixa primeiro."""

        def key(track: TrackedObject) -> tuple[int, float]:
            hazard_rank = 0 if is_hazard(track.label) else 1
            distance = track.distance_m if track.distance_m is not None else _pseudo_distance(track)
            return hazard_rank, distance

        return sorted(tracks, key=key)

    # ------------------------------------------------------------ obstaculos

    def compose_obstacle(self, reading: ProximityReading | None, zone: Zone) -> Utterance | None:
        """Alerta do sensor ultrassonico, independente da rede de visao."""
        if zone is Zone.SAFE:
            self._announced_zone = Zone.SAFE
            return None

        direction = (
            SENSOR_DIRECTIONS.get(reading.sensor_id, Direction.CENTER)
            if reading
            else Direction.CENTER
        )
        distance = reading.distance_m if reading else None
        critical = zone is Zone.CRITICAL
        self._announced_zone = zone

        return Utterance(
            text=phrases.obstacle_phrase(zone, distance, direction),
            priority=Priority.CRITICAL if critical else Priority.ALERT,
            dedupe_key=f"obstacle:{zone.value}:{direction.value}",
            interrupt=critical,
            tone=ZONE_TONES[zone],
            metadata={"zone": zone.value, "distance_m": distance, "at_ms": now_ms()},
        )

    # -------------------------------------------------------------- respostas

    def compose_scene_answer(self, tracks: list[TrackedObject]) -> Utterance:
        """Resposta sob demanda -- ignora cooldowns porque foi pedida pelo usuario."""
        selected = self._sorted_by_relevance(tracks)[: self._settings.max_objects_per_utterance]
        return Utterance(
            text=phrases.scene_phrase(selected, self._settings.announce_distance),
            priority=Priority.ANSWER,
            interrupt=True,
        )

    def reset(self) -> None:
        self._announced_zone = Zone.SAFE


def _pseudo_distance(track: TrackedObject) -> float:
    """Ordena por tamanho aparente quando nao ha distancia: maior = mais perto."""
    return 1.0 / max(track.box.area, 1.0)
