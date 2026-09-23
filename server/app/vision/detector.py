"""Contrato do detector de objetos e fabrica de backends.

Manter a interface minima permite trocar YOLOv8 por qualquer outro detector
(ou pelo simulado) sem tocar no pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path
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


def allowed_class_ids(names: dict[int, str], allowed: list[str]) -> list[int] | None:
    """Ids do modelo que pertencem a ``allowed_labels``, ou None para nao filtrar.

    Se nenhuma classe permitida existe no modelo -- um modelo proprio com nomes
    diferentes, por exemplo --, filtrar zeraria o detector sem erro nenhum.
    Nesse caso o filtro e desligado, com aviso no log.
    """
    if not allowed:
        return None
    wanted = set(allowed)
    ids = sorted(index for index, name in names.items() if name in wanted)
    if not ids:
        log.warning(
            "Nenhuma classe de vision.allowed_labels existe no modelo (%d classes); "
            "filtro de classes desligado.",
            len(names),
        )
        return None
    return ids


class CompositeDetector:
    """Modelo principal + modelos extras, que so acrescentam classes novas.

    E assim que o sistema ganha classes (degrau, porta, poste...) sem perder as
    do COCO: um modelo pequeno, treinado so com as classes novas
    (``training/``), roda ao lado do principal. Re-treinar o principal apenas
    com as classes novas trocaria a cabeca de 80 saidas por uma de 8 -- o modelo
    esqueceria pessoa, carro e cadeira. Deteccoes de um extra cujo rotulo o
    principal ja conhece sao descartadas, para o mesmo objeto nao sair duas vezes.
    """

    name = "yolo"

    def __init__(self, main, extras: list) -> None:  # noqa: ANN001 - YoloDetector/Onnx
        self._main = main
        self._extras = extras
        known = main.labels
        self._extra_labels = [extra.labels - known for extra in extras]
        for extra, labels in zip(extras, self._extra_labels, strict=True):
            if not labels:
                log.warning(
                    "Modelo extra %s nao traz nenhuma classe que o principal nao tenha; "
                    "sera ignorado.",
                    extra.model_path,
                )
        self.model_path = " + ".join(Path(d.model_path).name for d in (main, *extras))
        self.device = main.device

    @property
    def labels(self) -> frozenset[str]:
        return self._main.labels.union(*self._extra_labels)

    @property
    def ready(self) -> bool:
        return self._main.ready and all(extra.ready for extra in self._extras)

    def warmup(self) -> None:
        for detector in (self._main, *self._extras):
            detector.warmup()

    def detect(self, image: np.ndarray) -> list[Detection]:
        detections = self._main.detect(image)
        for extra, labels in zip(self._extras, self._extra_labels, strict=True):
            if labels:
                detections.extend(d for d in extra.detect(image) if d.label in labels)
        return detections


class DetectorUnavailableError(RuntimeError):
    """O YOLO foi pedido e nao carregou. A mensagem diz como resolver."""


def build_detector(settings: VisionSettings) -> ObjectDetector:
    """Instancia o backend configurado.

    ``yolo`` (padrao) falha na partida se o modelo nao carregar. ``auto`` tenta
    o YOLO e cai para o detector simulado -- util para demonstrar o sistema sem
    GPU nem pesos, mas o quadro anotado passa a sair com a tarja "DETECTOR
    SIMULADO", para que caixas sinteticas nunca sejam confundidas com deteccoes.
    """
    backend = settings.backend
    if backend in ("yolo", "auto"):
        try:
            from app.vision.yolo_detector import create_yolo_detector

            detector = create_yolo_detector(settings)
            log.info("Detector YOLO carregado (%s em %s)", detector.model_path, detector.device)
            return detector
        except Exception as exc:  # noqa: BLE001 - fallback deliberado
            reason = f"{type(exc).__name__}: {exc}"
            if backend == "yolo":
                raise DetectorUnavailableError(
                    f"YOLO indisponivel ({reason}). Instale as dependencias de visao "
                    '(cd server && uv pip install -e ".[vision]") e reinicie o servidor, '
                    "ou use AVS_VISION__BACKEND=fake para o detector simulado."
                ) from exc
            log.error(
                "YOLO indisponivel (%s). Usando o DETECTOR SIMULADO: as caixas NAO "
                "vem da camera. Reinicie o servidor depois de instalar o YOLO.",
                reason,
            )
            from app.vision.fake_detector import FakeDetector

            return FakeDetector(settings, fallback_reason=reason)

    from app.vision.fake_detector import FakeDetector

    return FakeDetector(settings)
