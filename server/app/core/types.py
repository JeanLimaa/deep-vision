"""Tipos de dominio compartilhados por todo o pipeline.

Estes objetos sao deliberadamente independentes de framework (sem FastAPI,
sem OpenCV, sem YOLO) para que qualquer camada possa ser trocada sem afetar
as regras de negocio.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

Milliseconds = int


def now_ms() -> Milliseconds:
    """Relogio monotonico em milissegundos.

    Usado para tudo que e interno ao servidor -- cooldowns, TTL de leituras,
    limitacao de taxa -- porque nao sofre ajuste de horario nem salto de NTP.
    """
    return int(time.monotonic() * 1000)


def epoch_ms() -> Milliseconds:
    """Relogio de parede em milissegundos (Unix epoch).

    Usado apenas no que atravessa a rede: o dispositivo carimba o instante da
    captura com este relogio, e so assim os dois lados falam a mesma base.
    """
    return int(time.time() * 1000)


class Zone(str, Enum):
    """Zonas de proximidade definidas na Secao 5.4 do TCC.

    O artigo define explicitamente apenas SAFE (> 1,5 m) e CRITICAL (< 0,8 m).
    A faixa intermediaria ficava sem tratamento, entao ela foi nomeada WARNING
    e recebe um alerta pulsante mais lento -- ver ``proximity.zones``.
    """

    SAFE = "safe"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def severity(self) -> int:
        return _ZONE_SEVERITY[self]


_ZONE_SEVERITY = {Zone.SAFE: 0, Zone.WARNING: 1, Zone.CRITICAL: 2}


class Direction(str, Enum):
    """Posicao horizontal relativa do objeto no campo de visao."""

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class Priority(int, Enum):
    """Prioridade de uma locucao na fila de fala.

    Valores maiores interrompem/precedem valores menores.
    """

    AMBIENT = 0
    INFO = 10
    ANSWER = 20
    ALERT = 30
    CRITICAL = 40


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Caixa delimitadora em pixels, canto superior esquerdo -> inferior direito."""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0

    def iou(self, other: BoundingBox) -> float:
        """Intersection over Union -- base do rastreador em ``vision.tracking``."""
        ix1, iy1 = max(self.x1, other.x1), max(self.y1, other.y1)
        ix2, iy2 = min(self.x2, other.x2), min(self.y2, other.y2)
        inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def scaled(self, sx: float, sy: float) -> BoundingBox:
        return BoundingBox(self.x1 * sx, self.y1 * sy, self.x2 * sx, self.y2 * sy)


@dataclass(frozen=True, slots=True)
class Detection:
    """Uma deteccao bruta produzida por um ``ObjectDetector``."""

    label: str
    confidence: float
    box: BoundingBox
    class_id: int = -1


@dataclass(slots=True)
class TrackedObject:
    """Deteccao associada a uma identidade estavel entre quadros.

    Sem isso o sistema repetiria "pessoa a frente" a cada quadro (~10 Hz).
    """

    track_id: int
    label: str
    confidence: float
    box: BoundingBox
    direction: Direction
    distance_m: float | None
    first_seen_ms: Milliseconds
    last_seen_ms: Milliseconds
    hits: int = 1
    misses: int = 0
    # Votos por rotulo, ponderados pela confianca: o rotulo do rastro e o mais
    # votado, e nao o do ultimo quadro (ver ``vision.tracking``).
    label_votes: dict[str, float] = field(default_factory=dict)
    announced: bool = False
    last_announced_direction: Direction | None = None
    last_announced_zone: Zone | None = None

    @property
    def age_ms(self) -> Milliseconds:
        return self.last_seen_ms - self.first_seen_ms


@dataclass(frozen=True, slots=True)
class ProximityReading:
    """Leitura de um sensor ultrassonico (HC-SR04) enviada pela borda."""

    sensor_id: str
    distance_m: float | None
    zone: Zone
    measured_at_ms: Milliseconds


@dataclass(frozen=True, slots=True)
class Frame:
    """Quadro JPEG recebido da borda, ainda nao decodificado.

    ``captured_at_ms`` vem do relogio de parede do dispositivo e
    ``received_at_ms`` do relogio monotonico do servidor: os dois nao sao
    comparaveis diretamente. Ver ``latency_ms``.
    """

    device_id: str
    sequence: int
    jpeg: bytes
    captured_at_ms: Milliseconds
    received_at_ms: Milliseconds = field(default_factory=now_ms)
    arrived_at_epoch_ms: Milliseconds = field(default_factory=epoch_ms)

    @property
    def size_bytes(self) -> int:
        return len(self.jpeg)

    def latency_ms(self, max_plausible_ms: int = 60_000) -> float | None:
        """Latencia captura -> chegada, ou ``None`` se os relogios divergem.

        O ESP32 nao sincroniza com NTP por padrao, entao o carimbo de captura
        pode estar em qualquer base. Uma diferenca negativa ou absurdamente
        grande denuncia essa divergencia -- e nesse caso e melhor nao reportar
        numero nenhum do que reportar um numero errado numa medicao do TCC.
        """
        if not self.captured_at_ms:
            return None
        delta = self.arrived_at_epoch_ms - self.captured_at_ms
        return float(delta) if 0 <= delta <= max_plausible_ms else None


@dataclass(frozen=True, slots=True)
class Utterance:
    """Unidade de saida sonora entregue ao usuario."""

    text: str
    priority: Priority = Priority.INFO
    # Chave usada para cooldown/deduplicacao (ex.: "obj:chair:center").
    dedupe_key: str | None = None
    # Se verdadeiro, esvazia a fila antes de falar (usado em risco imediato).
    interrupt: bool = False
    tone: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
