"""Rastreador leve por IoU + proximidade de centro.

Objetivo nao e reidentificacao robusta, e apenas dar identidade estavel entre
quadros para que a narracao nao repita o mesmo objeto continuamente.
"""

from __future__ import annotations

from app.core.types import BoundingBox, Detection, Direction, Milliseconds, TrackedObject
from app.settings import TrackingSettings


class IouTracker:
    """Associacao gulosa por maior IoU dentro da mesma classe."""

    def __init__(self, settings: TrackingSettings) -> None:
        self._settings = settings
        self._tracks: dict[int, TrackedObject] = {}
        self._next_id = 1

    @property
    def tracks(self) -> list[TrackedObject]:
        return list(self._tracks.values())

    def reset(self) -> None:
        self._tracks.clear()

    def update(self, detections: list[Detection], at_ms: Milliseconds) -> list[TrackedObject]:
        """Casa deteccoes com rastros existentes e devolve os rastros confirmados."""
        unmatched = list(detections)
        matched_ids: set[int] = set()

        for track_id, track in sorted(self._tracks.items()):
            best_index, best_score = -1, self._settings.iou_match_threshold
            for index, detection in enumerate(unmatched):
                if detection.label != track.label:
                    continue
                score = track.box.iou(detection.box)
                if score >= best_score:
                    best_index, best_score = index, score
            if best_index >= 0:
                self._absorb(track, unmatched.pop(best_index), at_ms)
                matched_ids.add(track_id)

        for detection in unmatched:
            track = TrackedObject(
                track_id=self._next_id,
                label=detection.label,
                confidence=detection.confidence,
                box=detection.box,
                direction=Direction.CENTER,
                distance_m=None,
                first_seen_ms=at_ms,
                last_seen_ms=at_ms,
            )
            self._tracks[self._next_id] = track
            matched_ids.add(self._next_id)
            self._next_id += 1

        self._age_out(matched_ids)
        return [t for t in self._tracks.values() if t.hits >= self._settings.min_hits]

    def _absorb(self, track: TrackedObject, detection: Detection, at_ms: Milliseconds) -> None:
        track.box = _smooth(track.box, detection.box)
        track.confidence = detection.confidence
        track.last_seen_ms = at_ms
        track.hits += 1
        track.misses = 0

    def _age_out(self, matched_ids: set[int]) -> None:
        for track_id in list(self._tracks):
            if track_id in matched_ids:
                continue
            track = self._tracks[track_id]
            track.misses += 1
            if track.misses > self._settings.max_misses:
                del self._tracks[track_id]


def _smooth(previous: BoundingBox, current: BoundingBox, alpha: float = 0.6) -> BoundingBox:
    """Suavizacao exponencial da caixa -- evita oscilacao na distancia estimada."""
    beta = 1.0 - alpha
    return BoundingBox(
        x1=alpha * current.x1 + beta * previous.x1,
        y1=alpha * current.y1 + beta * previous.y1,
        x2=alpha * current.x2 + beta * previous.x2,
        y2=alpha * current.y2 + beta * previous.y2,
    )
