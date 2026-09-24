"""Backend de deteccao baseado em Ultralytics YOLO (Secao 5.1 do TCC)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

from app.core.types import BoundingBox, Detection
from app.settings import ROOT_DIR, VisionSettings
from app.vision.detector import allowed_class_ids
from app.vision.labels import canonical_label

log = logging.getLogger(__name__)


def _resolve_device(requested: str) -> str:
    """Ordem de preferencia: CUDA (NVIDIA, ou AMD com ROCm no Linux), Apple,
    DirectML (qualquer GPU DirectX 12 no Windows, AMD inclusive) e, por fim, CPU."""
    if requested != "auto":
        return requested
    try:
        import torch

        if _cuda_available(torch):
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001
        pass
    from app.vision.onnx_detector import directml_available

    if directml_available():
        return "directml"
    return "cpu"


def _cuda_available(torch) -> bool:  # noqa: ANN001 - modulo importado tarde
    """Como ``torch.cuda.is_available()``, mas diz no log POR QUE nao ha CUDA.

    Com a placa NVIDIA presente e o driver antigo demais para o build do torch
    (ex.: driver 512 com torch cu126), o torch so emite um UserWarning e o
    servidor segue na CPU -- com o modelo menor e 4x mais lento -- sem que
    ninguem perceba. Aqui o aviso vira uma linha de log com a solucao.
    """
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        available = torch.cuda.is_available()
    if not available:
        for warning in caught:
            text = str(warning.message)
            if "driver" in text.lower():
                log.warning(
                    "GPU NVIDIA encontrada, mas o CUDA nao iniciou -- rodando na CPU. "
                    "Atualize o driver da placa (o torch %s exige driver para CUDA %s). "
                    "Detalhe: %s",
                    torch.__version__,
                    torch.version.cuda,
                    text.split(". ")[0],
                )
                break
    return available


# Pesos escolhidos quando ``model_path`` e "auto". mAP50-95 medido em 500
# imagens do COCO val2017 (nunca vistas no treino); tempo por quadro na CPU
# Ryzen 5 5600H, imgsz=640, lote 1. GPU: GTX 1650 (medicao anterior, familia 11).
#
#     pesos      mAP50-95    CPU        GPU
#     yolo11n     39,3      38 ms      13 ms
#     yolo26n     41,0      37 ms
#     yolo11s     46,5      83 ms      17 ms
#     yolo26s     49,0      85 ms
#     yolo11m     52,1     274 ms      34 ms
#     yolo26m     53,2     269 ms
#     yolo11l     54,1     346 ms      42 ms
#     yolo26l     56,7     332 ms
#
# A familia 26 acerta mais que a 11 em todo porte, pelo mesmo tempo (+2,5 pontos
# no "s", +2,6 no "l"), e dispensa o NMS -- nao ha motivo para ficar na 11.
#
# O criterio e folga sobre o teto de max_inference_fps (8 quadros/s, a taxa que
# a placa envia). Em CPU o "s" da ~12 /s -- o "m", ~3,7 /s, ja acumularia fila.
# Em GPU o "l" cabe com folga de ~3x.
AUTO_MODEL_BY_DEVICE = {
    "cpu": "yolo26s.pt",
    "cuda": "yolo26l.pt",
    # RX 6600 via DirectML: o yolo11l em 23 ms, folga de 5x (ver onnx_detector.py).
    # Fica na 11: a exportacao ONNX da 26 nao foi validada com DirectML.
    "directml": "yolo11l.pt",
    # Nao medido aqui: escolha conservadora, um porte abaixo do de CUDA.
    "mps": "yolo26m.pt",
}


def create_yolo_detector(settings: VisionSettings):  # noqa: ANN201 - dois backends
    """Modelo principal e, se configurados, os modelos extras ao lado dele."""
    device = _resolve_device(settings.device)
    main = _create_one(settings, device, _resolve_model_path(settings.model_path, device))
    if not settings.extra_model_paths:
        return main
    from app.vision.detector import CompositeDetector

    extras = [
        _create_one(settings, device, _resolve_model_path(path, device))
        for path in settings.extra_model_paths
    ]
    return CompositeDetector(main, extras)


def _create_one(settings: VisionSettings, device: str, weights: str):  # noqa: ANN202
    """Escolhe o backend pelo dispositivo: ONNX Runtime para DirectML, PyTorch
    para o resto. Extras tambem passam pelo perfil de classes: um modelo do
    Objects365 traz poste e lixeira, mas tambem tenis, chapeu e oculos."""
    if device == "directml" or weights.endswith(".onnx"):
        from app.vision.onnx_detector import OnnxYoloDetector

        return OnnxYoloDetector(settings, weights, use_gpu=device == "directml")
    return YoloDetector(settings, device, weights)


def _resolve_model_path(model_path: str, device: str) -> str:
    """Procura o arquivo de pesos em ``models/``; se faltar, baixa para la.

    Um nome solto (``yolo26s.pt``) vira ``models/yolo26s.pt``: o ultralytics
    baixa os pesos oficiais para o caminho pedido, em vez de para a pasta de
    onde o servidor foi iniciado.
    """
    if model_path == "auto":
        model_path = AUTO_MODEL_BY_DEVICE.get(device, AUTO_MODEL_BY_DEVICE["cpu"])
        log.info("Pesos escolhidos automaticamente para '%s': %s", device, model_path)
    candidate = Path(model_path)
    if candidate.is_absolute() and candidate.exists():
        return str(candidate)
    local = ROOT_DIR / "models" / candidate.name
    if local.exists():
        return str(local)
    if candidate.parent == Path("."):
        local.parent.mkdir(parents=True, exist_ok=True)
        return str(local)
    return model_path


class YoloDetector:
    """Envolve o modelo YOLO com um lock, pois a inferencia roda em threadpool."""

    name = "yolo"

    def __init__(
        self,
        settings: VisionSettings,
        device: str | None = None,
        weights: str | None = None,
    ) -> None:
        from ultralytics import YOLO  # import tardio: dependencia opcional

        self._settings = settings
        self._device = device or _resolve_device(settings.device)
        self.model_path = weights or _resolve_model_path(settings.model_path, self._device)
        self._model = YOLO(self.model_path)
        self._lock = threading.Lock()
        self._ready = False
        # Nomes ja no vocabulario do projeto (Objects365 "street lights" -> "pole").
        self._names: dict[int, str] = {
            int(i): canonical_label(str(n)) for i, n in (self._model.names or {}).items()
        }
        # Filtrar dentro do predict (e nao depois) poupa as vagas de max_det
        # para as classes que interessam.
        self._class_ids = allowed_class_ids(self._names, settings.allowed_labels)

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def labels(self) -> frozenset[str]:
        """Rotulos que o detector pode devolver -- ja descontado o filtro de classes."""
        if self._class_ids is None:
            return frozenset(self._names.values())
        return frozenset(self._names[i] for i in self._class_ids)

    @property
    def class_names(self) -> dict[int, str]:
        """Id -> nome, na numeracao dos arquivos de rotulo do dataset do modelo."""
        return dict(self._names)

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
                classes=self._class_ids,
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
        names = self._names
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
