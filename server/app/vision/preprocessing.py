"""Pre-processamento de imagem (Secao 5.1 do TCC).

Redimensionamento, reducao de ruido e realce de contraste antes da inferencia.
A normalizacao de escala de pixels e feita internamente pelo YOLO, por isso nao
e duplicada aqui -- repeti-la degradaria a acuracia.
"""

from __future__ import annotations

import cv2
import numpy as np

from app.settings import PreprocessSettings


class FramePreprocessor:
    """Aplica a cadeia de pre-processamento configurada.

    O objeto CLAHE e criado uma unica vez porque sua alocacao e cara em
    relacao ao custo por quadro.
    """

    def __init__(self, settings: PreprocessSettings) -> None:
        self._settings = settings
        self._clahe = (
            cv2.createCLAHE(
                clipLimit=settings.clahe_clip_limit,
                tileGridSize=(settings.clahe_tile_grid, settings.clahe_tile_grid),
            )
            if settings.clahe
            else None
        )

    _ROTATIONS = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }

    def decode(self, jpeg: bytes) -> np.ndarray | None:
        """JPEG -> matriz BGR, ja na orientacao certa.

        A rotacao vem antes de qualquer outra coisa: inferencia, caixas e painel
        passam a trabalhar no mesmo referencial do mundo real.
        """
        buffer = np.frombuffer(jpeg, dtype=np.uint8)
        image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if image is None or not image.size:
            return None
        rotation = self._ROTATIONS.get(self._settings.rotate_deg)
        return image if rotation is None else cv2.rotate(image, rotation)

    def apply(self, image: np.ndarray) -> np.ndarray:
        if not self._settings.enabled:
            return image
        result = image
        if self._settings.denoise:
            # Bilateral preserva bordas (importante para objetos pequenos)
            # e ainda assim remove o ruido tipico do sensor OV2640.
            result = cv2.bilateralFilter(result, d=5, sigmaColor=50, sigmaSpace=50)
        if self._clahe is not None:
            result = self._equalize_luminance(result)
        return result

    def _equalize_luminance(self, image: np.ndarray) -> np.ndarray:
        """Equaliza apenas o canal L (LAB), preservando as cores.

        Mitiga a perda de desempenho em baixa luminosidade relatada na Secao 6.
        """
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        lightness, a_channel, b_channel = cv2.split(lab)
        lightness = self._clahe.apply(lightness)
        return cv2.cvtColor(cv2.merge((lightness, a_channel, b_channel)), cv2.COLOR_LAB2BGR)


def resize_keep_aspect(image: np.ndarray, max_side: int) -> tuple[np.ndarray, float]:
    """Reduz a imagem mantendo a proporcao. Devolve a imagem e o fator aplicado.

    O fator permite reprojetar as caixas para as coordenadas originais.
    """
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= max_side:
        return image, 1.0
    scale = max_side / longest
    resized = cv2.resize(
        image,
        (int(round(width * scale)), int(round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )
    return resized, scale
