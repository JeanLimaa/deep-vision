"""Backend de deteccao baseado em Ultralytics YOLO (Secao 5.1 do TCC)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

from app.core.types import BoundingBox, Detection
from app.settings import ROOT_DIR, VisionSettings

log = logging.getLogger(__name__)


def _resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


# Pesos escolhidos quando ``model_path`` e "auto". Tempo por quadro medido com
# entrada 640x480 e imgsz=640 (CPU de 6 threads / GTX 1650, media de 12-15):
#
#     pesos      CPU        GPU     mAP50-95   VRAM (pico)
#     yolo11n     46 ms     13 ms     39,5
#     yolo11s     93 ms     17 ms     47,0
#     yolo11m    226 ms     34 ms     51,5      0,22 GB
#     yolo11l    278 ms     42 ms     53,4      0,33 GB
#     yolo11x      --       79 ms     54,7      0,64 GB
#
# A familia 11 custa o mesmo da 8 em cada porte e acerta mais (47,0 contra 44,9
# no porte "s"), entao nao ha motivo para ficar na 8.
#
# O criterio e folga sobre o teto de max_inference_fps (8 quadros/s, a taxa que
# a placa envia). Em CPU o "s" da 11 /s -- o "m", 4,4 /s, ja acumularia fila.
# Em GPU o "l" da 24 /s (folga de 3x) por 0,33 GB de VRAM; o "x" custa o dobro
# do tempo para 1,3 ponto de mAP e derrubaria a folga para 1,6x, que e pouco
# para uma demonstracao ao vivo.
AUTO_MODEL_BY_DEVICE = {
    "cpu": "yolo11s.pt",
    "cuda": "yolo11l.pt",
    # Nao medido aqui: escolha conservadora, um porte abaixo do de CUDA.
    "mps": "yolo11m.pt",
}


def _resolve_model_path(model_path: str, device: str) -> str:
    """Procura o arquivo de pesos em ``models/`` antes de deixar o ultralytics baixar."""
    if model_path == "auto":
        model_path = AUTO_MODEL_BY_DEVICE.get(device, AUTO_MODEL_BY_DEVICE["cpu"])
        log.info("Pesos escolhidos automaticamente para '%s': %s", device, model_path)
    candidate = Path(model_path)
    if candidate.is_absolute() and candidate.exists():
        return str(candidate)
    local = ROOT_DIR / "models" / candidate.name
    if local.exists():
        return str(local)
    return model_path


class YoloDetector:
    """Envolve o modelo YOLO com um lock, pois a inferencia roda em threadpool."""

    name = "yolo"

    def __init__(self, settings: VisionSettings) -> None:
        from ultralytics import YOLO  # import tardio: dependencia opcional

        self._settings = settings
        self._device = _resolve_device(settings.device)
        self.model_path = _resolve_model_path(settings.model_path, self._device)
        self._model = YOLO(self.model_path)
        self._lock = threading.Lock()
        self._ready = False
        self._names: dict[int, str] = dict(self._model.names or {})

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def device(self) -> str:
        return self._device

    def warmup(self) -> None:
        """Primeira inferencia e sempre a mais lenta; tira isso do caminho critico."""
        size = self._settings.inference_size
        blank = np.zeros((size, size, 3), dtype=np.uint8)
        self.detect(blank)
        self._ready = True
        log.info("YOLO pronto em %s (%d classes)", self._device, len(self._names))

    def detect(self, image: np.ndarray) -> list[Detection]:
        s = self._settings
        with self._lock:
            results = self._model.predict(
                source=image,
                imgsz=s.inference_size,
                conf=s.confidence_threshold,
                iou=s.iou_threshold,
                max_det=s.max_detections,
                device=self._device,
                verbose=False,
            )
        self._ready = True
        if not results:
            return []
        return self._to_detections(results[0])

    def _to_detections(self, result) -> list[Detection]:  # noqa: ANN001 - tipo do ultralytics
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []
        names = getattr(result, "names", None) or self._names
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        classes = boxes.cls.cpu().numpy().astype(int)
        detections: list[Detection] = []
        for (x1, y1, x2, y2), conf, cls in zip(xyxy, confs, classes, strict=False):
            detections.append(
                Detection(
                    label=str(names.get(int(cls), str(cls))),
                    confidence=float(conf),
                    box=BoundingBox(float(x1), float(y1), float(x2), float(y2)),
                    class_id=int(cls),
                )
            )
        return detections
