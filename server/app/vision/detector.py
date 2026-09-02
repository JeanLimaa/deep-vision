"""Contrato do detector de objetos e fabrica de backends.

Manter a interface minima permite trocar YOLOv8 por qualquer outro detector
(ou pelo simulado) sem tocar no pipeline.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import numpy as np

from app.core.types import Detection
from app.settings import VisionSettings

log = logging.getLogger(__name__)


@runtime_checkable
class ObjectDetector(Protocol):
    """Recebe imagem BGR (HxWx3, uint8) e devolve deteccoes em pixels."""

    name: str

    def detect(self, image: np.ndarray) -> list[Detection]: ...

    def warmup(self) -> None: ...

    @property
    def ready(self) -> bool: ...


def build_detector(settings: VisionSettings) -> ObjectDetector:
    """Instancia o backend configurado.

    ``auto`` tenta YOLO e cai para o detector simulado se as dependencias
    pesadas (torch/ultralytics) ou o arquivo de pesos nao estiverem presentes.
    Isso e o que permite rodar o projeto inteiro sem hardware nem GPU.
    """
    backend = settings.backend
    if backend in ("yolo", "auto"):
        try:
            from app.vision.yolo_detector import YoloDetector

            detector = YoloDetector(settings)
            log.info("Detector YOLO carregado (%s)", settings.model_path)
            return detector
        except Exception as exc:  # noqa: BLE001 - fallback deliberado
            if backend == "yolo":
                raise
            log.warning("YOLO indisponivel (%s); usando detector simulado.", exc)

    from app.vision.fake_detector import FakeDetector

    return FakeDetector(settings)
