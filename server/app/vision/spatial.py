"""Interpretacao espacial das deteccoes.

Converte caixas em pixels em informacao util para o usuario: de que lado o
objeto esta e a que distancia aproximada. Essa camada e o que transforma um
rotulo cru ("chair") em uma instrucao acionavel ("cadeira a frente, a cerca de
dois metros"), conforme a Secao 5.5 do TCC.
"""

from __future__ import annotations

import math

from app.core.types import BoundingBox, Direction, TrackedObject
from app.settings import CameraSettings
from app.vision.labels import real_height_m


class SpatialEstimator:
    """Modelo pinhole simples para estimativa monocular de distancia.

    Com a distancia focal em pixels ``f`` e a altura real ``H`` de um objeto de
    classe conhecida, a distancia ``d`` para uma caixa de altura ``h`` pixels e:

        f = (largura_da_imagem / 2) / tan(fov_horizontal / 2)
        d = (H * f) / h

    A estimativa e grosseira -- depende de o objeto estar inteiro no quadro e de
    a altura media da classe ser representativa. Por isso ela e sempre
    substituida pela leitura do sensor ultrassonico quando ambos concordam sobre
    um objeto centralizado (ver ``proximity.fusion``).
    """

    def __init__(self, settings: CameraSettings) -> None:
        self._settings = settings
        self._half_fov_tan = math.tan(math.radians(settings.horizontal_fov_deg) / 2.0)

    def focal_length_px(self, image_width: int) -> float:
        return (image_width / 2.0) / self._half_fov_tan if self._half_fov_tan else 0.0

    def direction_of(self, box: BoundingBox, image_width: int) -> Direction:
        if image_width <= 0:
            return Direction.CENTER
        center_x = box.center[0] / image_width
        half_band = self._settings.center_band / 2.0
        if center_x < 0.5 - half_band:
            return Direction.LEFT
        if center_x > 0.5 + half_band:
            return Direction.RIGHT
        return Direction.CENTER

    def distance_of(self, label: str, box: BoundingBox, image_width: int) -> float | None:
        """Distancia estimada em metros, ou ``None`` quando nao e confiavel."""
        height_m = real_height_m(label)
        if height_m is None or box.height <= 1:
            return None
        focal = self.focal_length_px(image_width)
        if focal <= 0:
            return None
        distance = (height_m * focal) / box.height
        if not math.isfinite(distance):
            return None
        s = self._settings
        if distance < s.min_estimated_distance_m or distance > s.max_estimated_distance_m:
            return None
        return round(distance, 2)

    def annotate(self, track: TrackedObject, image_width: int) -> TrackedObject:
        """Preenche direcao e distancia do rastro, no lugar."""
        track.direction = self.direction_of(track.box, image_width)
        track.distance_m = self.distance_of(track.label, track.box, image_width)
        return track


def describe_distance(distance_m: float | None) -> str:
    """Arredonda a distancia para uma expressao natural em pt-BR.

    Precisao excessiva ("a 2,37 metros") atrapalha mais do que ajuda: a
    estimativa monocular nao sustenta esse nivel de detalhe.
    """
    if distance_m is None:
        return ""
    if distance_m < 0.6:
        return "bem perto"
    if distance_m < 1.2:
        return "a cerca de um metro"
    if distance_m < 1.8:
        return "a cerca de um metro e meio"
    if distance_m < 3.0:
        return f"a cerca de {round(distance_m)} metros"
    if distance_m < 6.0:
        return "a alguns metros"
    return "ao longe"


DIRECTION_PT: dict[Direction, str] = {
    Direction.LEFT: "a esquerda",
    Direction.CENTER: "a frente",
    Direction.RIGHT: "a direita",
}


def describe_direction(direction: Direction) -> str:
    return DIRECTION_PT[direction]
