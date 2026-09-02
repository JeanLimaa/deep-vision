"""Utilitarios de limitacao temporal usados por narracao e alertas."""

from __future__ import annotations

from app.core.types import Milliseconds, now_ms


class Cooldown:
    """Permite uma acao por chave a cada ``interval_ms``.

    Usado para nao repetir "cadeira a frente" a cada quadro processado.
    """

    def __init__(self, interval_ms: Milliseconds) -> None:
        self.interval_ms = interval_ms
        self._last: dict[str, Milliseconds] = {}

    def ready(self, key: str, at_ms: Milliseconds | None = None) -> bool:
        at_ms = now_ms() if at_ms is None else at_ms
        last = self._last.get(key)
        return last is None or at_ms - last >= self.interval_ms

    def mark(self, key: str, at_ms: Milliseconds | None = None) -> None:
        self._last[key] = now_ms() if at_ms is None else at_ms

    def try_acquire(self, key: str, at_ms: Milliseconds | None = None) -> bool:
        at_ms = now_ms() if at_ms is None else at_ms
        if not self.ready(key, at_ms):
            return False
        self.mark(key, at_ms)
        return True

    def forget(self, key: str) -> None:
        self._last.pop(key, None)

    def clear(self) -> None:
        self._last.clear()


class RateLimiter:
    """Limitador simples de taxa (quadros por segundo) por chave."""

    def __init__(self, max_per_second: float) -> None:
        self.min_interval_ms = 0 if max_per_second <= 0 else int(1000 / max_per_second)
        self._last: dict[str, Milliseconds] = {}

    def allow(self, key: str = "default", at_ms: Milliseconds | None = None) -> bool:
        if self.min_interval_ms == 0:
            return True
        at_ms = now_ms() if at_ms is None else at_ms
        last = self._last.get(key)
        if last is not None and at_ms - last < self.min_interval_ms:
            return False
        self._last[key] = at_ms
        return True


class MovingAverage:
    """Media movel de janela fixa -- usada nas metricas de latencia/FPS."""

    def __init__(self, window: int = 30) -> None:
        self.window = window
        self._values: list[float] = []

    def add(self, value: float) -> None:
        self._values.append(value)
        if len(self._values) > self.window:
            del self._values[0 : len(self._values) - self.window]

    @property
    def value(self) -> float:
        return sum(self._values) / len(self._values) if self._values else 0.0

    def __len__(self) -> int:
        return len(self._values)
