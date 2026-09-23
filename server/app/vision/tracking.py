"""Rastreador leve por IoU, com votacao de rotulo.

Objetivo nao e reidentificacao robusta, e dar identidade estavel entre quadros
para que a narracao nao repita o mesmo objeto -- e filtrar o que o detector
erra de um quadro para o outro, que e de onde vem os erros que o usuario
percebe:

- **Rotulo que oscila.** A mesma cadeira sai "chair" num quadro e "couch" no
  seguinte. Casar so dentro da mesma classe criava dois rastros e dois
  anuncios; aqui o rastro aceita outra classe quando a caixa e praticamente a
  mesma, e o rotulo e decidido por votacao ponderada pela confianca.
- **Deteccao espuria intermitente.** Um "cat" num monte de roupa aparece num
  quadro e some nos seguintes. Enquanto o rastro nao foi confirmado, uma falha
  o elimina: so vira objeto o que aparece em ``min_hits`` quadros SEGUIDOS.
- **Caixa fantasma.** O rastro confirmado sobrevive a ``max_misses`` quadros
  sem deteccao, para nao reanunciar um objeto que piscou, mas so e devolvido
  -- desenhado, narrado, citado nas respostas -- enquanto foi visto ha no
  maximo ``coast_frames`` quadros.
"""

from __future__ import annotations

from app.core.types import BoundingBox, Detection, Direction, Milliseconds, TrackedObject
from app.settings import TrackingSettings

# IoU minimo para um rastro aceitar deteccao de OUTRA classe. Alto de proposito:
# a oscilacao de rotulo do mesmo objeto produz caixas quase identicas, enquanto
# objetos distintos sobrepostos -- pessoa sentada na cadeira, ciclista na
# bicicleta -- raramente passam de 0,5 e precisam continuar separados.
RELABEL_IOU = 0.6
# Vantagem de uma deteccao da mesma classe na disputa por um rastro. Pequena o
# bastante para uma caixa quase identica de outra classe (o mesmo objeto,
# rotulado diferente) vencer um vizinho da mesma classe que mal encosta.
SAME_LABEL_BONUS = 0.2
# Peso dos votos antigos a cada nova deteccao: o rotulo acompanha uma mudanca
# persistente do detector em poucos quadros, mas nao um quadro isolado.
VOTE_DECAY = 0.85


class IouTracker:
    """Associacao gulosa global por IoU, preferindo a mesma classe."""

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
        """Casa deteccoes com rastros e devolve os confirmados e visiveis."""
        matched_tracks: set[int] = set()
        matched_detections: set[int] = set()
        for track_id, index in self._candidate_pairs(detections):
            if track_id in matched_tracks or index in matched_detections:
                continue
            matched_tracks.add(track_id)
            matched_detections.add(index)
            self._absorb(self._tracks[track_id], detections[index], at_ms)

        for index, detection in enumerate(detections):
            if index not in matched_detections:
                matched_tracks.add(self._spawn(detection, at_ms).track_id)

        self._age_out(matched_tracks)
        s = self._settings
        return [
            track
            for track in self._tracks.values()
            if track.hits >= s.min_hits and track.misses <= s.coast_frames
        ]

    def _candidate_pairs(self, detections: list[Detection]) -> list[tuple[int, int]]:
        """Pares (rastro, deteccao) admissiveis, do mais para o menos provavel."""
        scored: list[tuple[float, int, int]] = []
        for track_id, track in self._tracks.items():
            for index, detection in enumerate(detections):
                overlap = track.box.iou(detection.box)
                if detection.label == track.label:
                    if overlap >= self._settings.iou_match_threshold:
                        scored.append((overlap + SAME_LABEL_BONUS, track_id, index))
                elif overlap >= RELABEL_IOU:
                    scored.append((overlap, track_id, index))
        scored.sort(reverse=True)
        return [(track_id, index) for _, track_id, index in scored]

    def _spawn(self, detection: Detection, at_ms: Milliseconds) -> TrackedObject:
        track = TrackedObject(
            track_id=self._next_id,
            label=detection.label,
            confidence=detection.confidence,
            box=detection.box,
            direction=Direction.CENTER,
            distance_m=None,
            first_seen_ms=at_ms,
            last_seen_ms=at_ms,
            label_votes={detection.label: detection.confidence},
        )
        self._tracks[self._next_id] = track
        self._next_id += 1
        return track

    def _absorb(self, track: TrackedObject, detection: Detection, at_ms: Milliseconds) -> None:
        track.box = _smooth(track.box, detection.box)
        track.confidence = 0.5 * track.confidence + 0.5 * detection.confidence
        _vote(track, detection)
        track.last_seen_ms = at_ms
        track.hits += 1
        track.misses = 0

    def _age_out(self, matched_ids: set[int]) -> None:
        for track_id in list(self._tracks):
            if track_id in matched_ids:
                continue
            track = self._tracks[track_id]
            track.misses += 1
            # Rastro nao confirmado nao tem direito a falha: o que pisca nao vira objeto.
            tentative = track.hits < self._settings.min_hits
            if tentative or track.misses > self._settings.max_misses:
                del self._tracks[track_id]


def _vote(track: TrackedObject, detection: Detection) -> None:
    """Acumula o voto da deteccao e elege o rotulo do rastro."""
    votes = track.label_votes
    for label in votes:
        votes[label] *= VOTE_DECAY
    votes[detection.label] = votes.get(detection.label, 0.0) + detection.confidence
    track.label = max(votes, key=votes.__getitem__)


def _smooth(previous: BoundingBox, current: BoundingBox, alpha: float = 0.6) -> BoundingBox:
    """Suavizacao exponencial da caixa -- evita oscilacao na distancia estimada."""
    beta = 1.0 - alpha
    return BoundingBox(
        x1=alpha * current.x1 + beta * previous.x1,
        y1=alpha * current.y1 + beta * previous.y1,
        x2=alpha * current.x2 + beta * previous.x2,
        y2=alpha * current.y2 + beta * previous.y2,
    )
