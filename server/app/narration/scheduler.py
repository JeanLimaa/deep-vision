"""Fila de fala com prioridade, deduplicacao, cooldown e validade.

Sem isso o usuario receberia dezenas de frases por segundo. A fila garante que
um alerta critico interrompe qualquer narracao em andamento, que informacao
ambiente nunca atropele uma resposta pedida pelo usuario, e que nada seja dito
depois de ter deixado de ser verdade.
"""

from __future__ import annotations

import heapq
import itertools

from app.core.throttle import Cooldown
from app.core.types import Priority, Utterance, now_ms
from app.settings import NarrationSettings


class SpeechScheduler:
    """Heap de prioridade estavel (prioridade desc, ordem de chegada asc)."""

    def __init__(self, settings: NarrationSettings, max_queue: int = 16) -> None:
        self._max_queue = max_queue
        # (-prioridade, ordem de chegada, instante de entrada, locucao)
        self._heap: list[tuple[int, int, int, Utterance]] = []
        self._counter = itertools.count()
        self._object_cooldown = Cooldown(settings.object_cooldown_ms)
        self._obstacle_cooldown = Cooldown(settings.obstacle_cooldown_ms)
        self._critical_cooldown = Cooldown(settings.critical_cooldown_ms)
        self._alert_max_age_ms = settings.alert_max_age_ms
        self._info_max_age_ms = settings.info_max_age_ms

    def submit(self, utterance: Utterance, at_ms: int | None = None) -> bool:
        """Enfileira a locucao. Devolve ``False`` se foi suprimida por cooldown."""
        at_ms = now_ms() if at_ms is None else at_ms
        if not self._passes_cooldown(utterance, at_ms):
            return False
        if utterance.interrupt:
            self._drop_below(utterance.priority)
        if len(self._heap) >= self._max_queue:
            self._drop_lowest()
        heapq.heappush(
            self._heap, (-int(utterance.priority), next(self._counter), at_ms, utterance)
        )
        return True

    def submit_all(self, utterances: list[Utterance], at_ms: int | None = None) -> list[Utterance]:
        return [u for u in utterances if self.submit(u, at_ms)]

    def pop(self, at_ms: int | None = None) -> Utterance | None:
        """Proxima locucao ainda valida; as vencidas sao descartadas no caminho."""
        at_ms = now_ms() if at_ms is None else at_ms
        while self._heap:
            _, _, queued_ms, utterance = heapq.heappop(self._heap)
            if not self._expired(utterance, queued_ms, at_ms):
                return utterance
        return None

    def clear(self) -> None:
        self._heap.clear()

    def reset(self) -> None:
        self.clear()
        self._object_cooldown.clear()
        self._obstacle_cooldown.clear()
        self._critical_cooldown.clear()

    def __len__(self) -> int:
        return len(self._heap)

    @property
    def pending(self) -> list[Utterance]:
        return [item[3] for item in sorted(self._heap, key=lambda item: (item[0], item[1]))]

    # -------------------------------------------------------------- internos

    def _expired(self, utterance: Utterance, queued_ms: int, at_ms: int) -> bool:
        if utterance.priority == Priority.ANSWER:
            return False
        max_age = (
            self._alert_max_age_ms
            if utterance.priority >= Priority.ALERT
            else self._info_max_age_ms
        )
        return at_ms - queued_ms > max_age

    def _passes_cooldown(self, utterance: Utterance, at_ms: int) -> bool:
        if utterance.dedupe_key is None:
            return True
        cooldown = self._cooldown_for(utterance.priority)
        return cooldown.try_acquire(utterance.dedupe_key, at_ms)

    def _cooldown_for(self, priority: Priority) -> Cooldown:
        if priority >= Priority.CRITICAL:
            return self._critical_cooldown
        if priority >= Priority.ALERT:
            return self._obstacle_cooldown
        return self._object_cooldown

    def _drop_below(self, priority: Priority) -> None:
        """Descarta o que estava na fila com prioridade menor que a locucao atual."""
        self._heap = [item for item in self._heap if -item[0] >= int(priority)]
        heapq.heapify(self._heap)

    def _drop_lowest(self) -> None:
        """Fila cheia: sacrifica a locucao menos prioritaria e mais recente."""
        if not self._heap:
            return
        worst = max(self._heap, key=lambda item: (item[0], item[1]))
        self._heap.remove(worst)
        heapq.heapify(self._heap)
