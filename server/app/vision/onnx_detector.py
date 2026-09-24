"""Backend YOLO em ONNX Runtime -- a GPU que nao e NVIDIA.

O PyTorch so acelera em placas NVIDIA (CUDA) no Windows. Para AMD e Intel, o
caminho e o ONNX Runtime com DirectML, que roda em qualquer GPU com DirectX 12.
Medido numa RX 6600 com entrada 640x640 (media de 30):

    pesos      DirectML   CPU (ONNX)
    yolo11s     8 ms       90 ms
    yolo11l    23 ms      326 ms

O modelo e exportado uma unica vez a partir do ``.pt`` (com o NMS embutido no
grafo) e a inferencia NAO passa pelo ultralytics: ao carregar um ``.onnx`` ele
reinstala por conta propria o pacote ``onnxruntime`` de CPU, que sobrescreve o
``onnxruntime-directml`` e desliga a GPU sem aviso nenhum.
"""

from __future__ import annotations

import ast
import logging
import os
import threading
from pathlib import Path

import cv2
import numpy as np

from app.core.types import BoundingBox, Detection
from app.settings import VisionSettings
from app.vision.detector import allowed_class_ids
from app.vision.labels import canonical_label

log = logging.getLogger(__name__)

# Piso de confianca gravado no NMS exportado. O limiar de verdade e aplicado
# aqui, a cada quadro, para que mudar a configuracao nao exija reexportar.
_EXPORT_CONF_FLOOR = 0.10
# Cor de preenchimento do letterbox -- a mesma usada no treino do ultralytics.
_PAD_VALUE = 114


def directml_available() -> bool:
    try:
        import onnxruntime as ort
    except ImportError:
        return False
    return "DmlExecutionProvider" in ort.get_available_providers()


def ensure_onnx(weights: str, imgsz: int, iou: float) -> Path:
    """Devolve o ``.onnx`` equivalente aos pesos, exportando na primeira vez.

    O tamanho de entrada fica fixo no grafo, por isso entra no nome do arquivo:
    mudar ``inference_size`` gera outro arquivo em vez de usar um incompativel.
    """
    source = Path(weights)
    if source.suffix == ".onnx":
        return source
    target = source.with_name(f"{source.stem}-{imgsz}.onnx")
    if target.exists():
        return target

    log.info("Exportando %s para ONNX (%dx%d); so acontece uma vez.", source.name, imgsz, imgsz)
    _disable_ultralytics_autoinstall()
    from ultralytics import YOLO

    exported = YOLO(str(source)).export(
        format="onnx",
        imgsz=imgsz,
        nms=True,
        conf=_EXPORT_CONF_FLOOR,
        iou=iou,
        simplify=True,
        opset=17,
    )
    Path(exported).replace(target)
    return target


def _disable_ultralytics_autoinstall() -> None:
    """Impede o ultralytics de instalar o ``onnxruntime`` de CPU na exportacao.

    A variavel de ambiente so vale se o modulo ainda nao foi importado; se ja
    foi, a constante precisa ser trocada onde ``check_requirements`` a le.
    """
    os.environ["YOLO_AUTOINSTALL"] = "False"
    import sys

    checks = sys.modules.get("ultralytics.utils.checks")
    if checks is not None:
        checks.AUTOINSTALL = False


class OnnxYoloDetector:
    """Mesmo contrato do ``YoloDetector``, executado pelo ONNX Runtime."""

    name = "yolo"

    def __init__(
        self,
        settings: VisionSettings,
        weights: str,
        use_gpu: bool = True,
    ) -> None:
        import onnxruntime as ort

        self._settings = settings
        self.model_path = str(ensure_onnx(weights, settings.inference_size, settings.iou_threshold))
        providers = ["DmlExecutionProvider", "CPUExecutionProvider"] if use_gpu else [
            "CPUExecutionProvider"
        ]
        options = ort.SessionOptions()
        # DirectML nao suporta execucao paralela nem reuso de memoria entre nos.
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self._session = ort.InferenceSession(self.model_path, options, providers=providers)
        self._device = "directml" if "Dml" in self._session.get_providers()[0] else "cpu"

        model_input = self._session.get_inputs()[0]
        self._input_name = model_input.name
        self._input_h, self._input_w = (int(v) for v in model_input.shape[2:4])
        metadata = self._session.get_modelmeta().custom_metadata_map
        self._names: dict[int, str] = {
            int(k): canonical_label(str(v))
            for k, v in ast.literal_eval(metadata.get("names", "{}")).items()
        }
        ids = allowed_class_ids(self._names, settings.allowed_labels)
        self._class_ids = None if ids is None else np.array(ids)
        self._lock = threading.Lock()
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def labels(self) -> frozenset[str]:
        """Rotulos que o detector pode devolver -- ja descontado o filtro de classes."""
        if self._class_ids is None:
            return frozenset(self._names.values())
        return frozenset(self._names[int(i)] for i in self._class_ids)

    @property
    def device(self) -> str:
        return self._device

    def warmup(self) -> None:
        blank = np.zeros((self._input_h, self._input_w, 3), dtype=np.uint8)
        self.detect(blank)
        self._ready = True
        log.info("YOLO (ONNX) pronto em %s (%d classes)", self._device, len(self._names))

    def detect(self, image: np.ndarray) -> list[Detection]:
        s = self._settings
        blob, ratio, pad_x, pad_y = letterbox(image, self._input_w, self._input_h)
        with self._lock:
            output = self._session.run(None, {self._input_name: blob})[0][0]
        self._ready = True

        # Saida do NMS embutido: (max_det, 6) = x1, y1, x2, y2, score, classe,
        # ja ordenada por score. Linhas vazias vem com score 0.
        keep = output[:, 4] >= s.confidence_threshold
        if self._class_ids is not None:
            keep &= np.isin(output[:, 5].astype(int), self._class_ids)
        rows = output[keep][: s.max_detections]
        height, width = image.shape[:2]
        detections: list[Detection] = []
        for x1, y1, x2, y2, score, cls in rows:
            box = unletterbox((x1, y1, x2, y2), ratio, pad_x, pad_y, width, height)
            if box.width <= 1 or box.height <= 1:
                continue
            class_id = int(cls)
            detections.append(
                Detection(
                    label=self._names.get(class_id, str(class_id)),
                    confidence=float(score),
                    box=box,
                    class_id=class_id,
                )
            )
        return detections


def letterbox(
    image: np.ndarray, target_w: int, target_h: int
) -> tuple[np.ndarray, float, float, float]:
    """Redimensiona mantendo a proporcao e centraliza numa tela cinza.

    Reproduz o pre-processamento do treino do ultralytics. Esticar a imagem para
    caber no quadrado deformaria os objetos e derrubaria a acuracia.
    Devolve o tensor NCHW RGB normalizado e o necessario para desfazer a conta.
    """
    height, width = image.shape[:2]
    ratio = min(target_w / width, target_h / height)
    new_w, new_h = round(width * ratio), round(height * ratio)
    pad_x, pad_y = (target_w - new_w) / 2, (target_h - new_h) / 2

    canvas = np.full((target_h, target_w, 3), _PAD_VALUE, dtype=np.uint8)
    left, top = round(pad_x - 0.1), round(pad_y - 0.1)
    if (new_w, new_h) != (width, height):
        image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    canvas[top : top + new_h, left : left + new_w] = image

    blob = np.ascontiguousarray(canvas[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32)
    blob /= 255.0
    return blob, ratio, float(left), float(top)


def unletterbox(
    box: tuple[float, float, float, float],
    ratio: float,
    pad_x: float,
    pad_y: float,
    width: int,
    height: int,
) -> BoundingBox:
    """Leva uma caixa das coordenadas da tela de entrada para as da imagem."""
    x1, y1, x2, y2 = box
    return BoundingBox(
        x1=float(np.clip((x1 - pad_x) / ratio, 0, width)),
        y1=float(np.clip((y1 - pad_y) / ratio, 0, height)),
        x2=float(np.clip((x2 - pad_x) / ratio, 0, width)),
        y2=float(np.clip((y2 - pad_y) / ratio, 0, height)),
    )
