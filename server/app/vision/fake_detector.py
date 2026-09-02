"""Detector simulado -- permite executar e validar todo o sistema sem YOLO.

Nao e um mock trivial: ele encena objetos que entram, atravessam o quadro,
aproximam-se e saem. Assim o rastreamento, a estimativa de distancia, os
cooldowns de narracao e a fila de fala sao exercitados de verdade, tanto nos
testes automatizados quanto em demonstracoes sem GPU ou sem os pesos do modelo.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np

from app.core.types import BoundingBox, Detection
from app.settings import VisionSettings


@dataclass(slots=True)
class _VirtualObject:
    label: str
    # Posicao horizontal normalizada (0 = borda esquerda, 1 = direita).
    x: float
    dx: float
    # Altura normalizada da caixa; cresce conforme o objeto "se aproxima".
    height: float
    dh: float
    confidence: float

    def step(self) -> None:
        self.x += self.dx
        self.height = max(0.05, min(0.95, self.height + self.dh))
        if self.x < -0.15 or self.x > 1.15:
            self.dx = -self.dx
            self.x = min(max(self.x, -0.15), 1.15)
        if self.height >= 0.95 or self.height <= 0.05:
            self.dh = -self.dh


_SCENE = (
    ("person", 0.10, 0.012, 0.55, 0.004),
    ("chair", 0.72, -0.004, 0.30, -0.002),
    ("backpack", 0.45, 0.006, 0.18, 0.003),
    ("cell phone", 0.88, -0.010, 0.12, 0.001),
    ("car", 0.30, 0.008, 0.40, 0.005),
)


class FakeDetector:
    """Gera deteccoes coerentes no tempo, com ruido leve e aparicoes ciclicas."""

    name = "fake"

    def __init__(self, settings: VisionSettings, seed: int = 7) -> None:
        self._settings = settings
        self._rng = random.Random(seed)
        self._tick = 0
        self._objects = [
            _VirtualObject(label, x, dx, h, dh, confidence=0.65)
            for label, x, dx, h, dh in _SCENE
        ]

    @property
    def ready(self) -> bool:
        return True

    def warmup(self) -> None:
        return None

    def detect(self, image: np.ndarray) -> list[Detection]:
        height, width = image.shape[:2]
        self._tick += 1
        detections: list[Detection] = []

        for index, obj in enumerate(self._objects):
            obj.step()
            # Cada objeto fica visivel em janelas distintas, simulando
            # entrada e saida do campo de visao da camera.
            phase = math.sin((self._tick / 45.0) + index * 1.3)
            if phase < -0.35:
                continue

            box_h = obj.height * height
            box_w = box_h * _ASPECT.get(obj.label, 0.6)
            cx = obj.x * width
            cy = height * (0.85 - obj.height * 0.35)
            jitter = self._rng.uniform(-2.0, 2.0)

            box = BoundingBox(
                x1=max(0.0, cx - box_w / 2 + jitter),
                y1=max(0.0, cy - box_h / 2 + jitter),
                x2=min(float(width), cx + box_w / 2 + jitter),
                y2=min(float(height), cy + box_h / 2 + jitter),
            )
            if box.width < 4 or box.height < 4:
                continue

            confidence = min(0.97, obj.confidence + 0.25 * phase + self._rng.uniform(-0.03, 0.03))
            if confidence < self._settings.confidence_threshold:
                continue
            detections.append(Detection(label=obj.label, confidence=confidence, box=box))

        detections.sort(key=lambda d: d.confidence, reverse=True)
        return detections[: self._settings.max_detections]


# Razao largura/altura tipica por classe, so para as caixas parecerem plausiveis.
_ASPECT = {
    "person": 0.42,
    "chair": 0.85,
    "backpack": 0.75,
    "cell phone": 0.50,
    "car": 2.20,
}
