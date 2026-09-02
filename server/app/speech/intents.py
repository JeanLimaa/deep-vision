"""Casamento de intencoes por regras (pt-BR).

O TCC menciona processamento de linguagem natural para as intencoes. Optou-se
por um casador deterministico baseado em expressoes regulares em vez de um
modelo de linguagem: o vocabulario do dispositivo e fechado (uma duzia de
comandos), a resposta e imediata, o resultado e reproduzivel -- e nada disso
depende de rede ou de GPU. Trocar por um modelo maior no futuro exige apenas
outra implementacao de ``IntentMatcher.match``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.vision.labels import COCO_PT, resolve_label_from_pt


class Intent(str, Enum):
    START = "start"
    STOP = "stop"
    DESCRIBE_SCENE = "describe_scene"
    FIND_OBJECT = "find_object"
    COUNT_OBJECT = "count_object"
    OBSTACLE_STATUS = "obstacle_status"
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"
    REPEAT = "repeat"
    STATUS = "status"
    HELP = "help"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class IntentMatch:
    intent: Intent
    confidence: float = 1.0
    slots: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""

    @property
    def understood(self) -> bool:
        return self.intent is not Intent.UNKNOWN


def normalize(text: str) -> str:
    """Minusculas, sem acentos e sem pontuacao -- tolera erros do STT."""
    lowered = text.strip().lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", stripped)).strip()


# Ordem importa: padroes mais especificos vem antes dos genericos. As alternativas
# cobrem as variacoes de conjugacao que aparecem na fala natural ("pare", "pausar",
# "para de falar") e os erros mais comuns de transcricao.
_RULES: list[tuple[Intent, str]] = [
    (
        Intent.STOP,
        r"\b(parar|pare|pausar|pausa|desligar|desliga|silencio|para de falar)\b",
    ),
    (
        Intent.START,
        r"\b(iniciar|inicia|comecar|comeca|ligar|liga|retomar|voltar|ativar)\b",
    ),
    (
        Intent.VOLUME_UP,
        r"\b(aumentar|aumenta|subir|sobe|mais alto)\b.*\bvolume\b"
        r"|\bvolume\b.*\b(aumentar|aumenta|mais alto|subir)\b",
    ),
    (
        Intent.VOLUME_DOWN,
        r"\b(diminuir|diminui|abaixar|abaixa|baixar|mais baixo)\b.*\bvolume\b"
        r"|\bvolume\b.*\b(diminuir|diminui|abaixar|mais baixo|baixar)\b",
    ),
    (
        Intent.REPEAT,
        r"\b(repetir|repete|de novo|novamente|nao entendi)\b",
    ),
    (
        Intent.COUNT_OBJECT,
        r"\bquant[oa]s?\b",
    ),
    (
        Intent.OBSTACLE_STATUS,
        r"\b(obstaculo|obstaculos|barreira|posso (andar|seguir|passar)"
        r"|caminho (esta )?livre|tem algo na frente)\b",
    ),
    (
        Intent.FIND_OBJECT,
        r"\b(onde|procur[ae]|ache|achar|encontr[ae]|localiz[ae]"
        r"|tem (um|uma|algum|alguma))\b",
    ),
    (
        Intent.DESCRIBE_SCENE,
        r"\b(o que (tem|voce ve|esta vendo|ha)|descrev[ae]|descricao"
        r"|me diga o que|olhar|o que e isso)\b",
    ),
    (
        Intent.STATUS,
        r"\b(status|bateria|conexao|como esta o sistema|esta funcionando)\b",
    ),
    (
        Intent.HELP,
        r"\b(ajuda|socorro|o que voce (pode|sabe) fazer|comandos)\b",
    ),
]

_COMPILED: list[tuple[Intent, re.Pattern[str]]] = [
    (intent, re.compile(pattern)) for intent, pattern in _RULES
]

# Palavras a ignorar ao procurar o objeto citado no comando.
_STOPWORDS = frozenset(
    """
    a o as os um uma uns umas de do da dos das em no na nos nas por para com sem
    que qual quais quantos quantas onde esta estao tem ha aqui perto minha meu
    frente lado direita esquerda me diga ver vejo voce eu ate e ou
    """.split()
)


class IntentMatcher:
    """Converte texto livre em uma intencao com slots preenchidos."""

    def match(self, text: str) -> IntentMatch:
        normalized = normalize(text)
        if not normalized:
            return IntentMatch(Intent.UNKNOWN, 0.0, raw_text=text)

        for intent, pattern in _COMPILED:
            if not pattern.search(normalized):
                continue
            slots = self._extract_slots(intent, normalized)
            # Intencoes que exigem um objeto e nao o encontraram viram descricao
            # geral da cena, que e a resposta util mais proxima.
            if intent in (Intent.FIND_OBJECT, Intent.COUNT_OBJECT) and "label" not in slots:
                return IntentMatch(Intent.DESCRIBE_SCENE, 0.6, slots, text)
            return IntentMatch(intent, 0.9, slots, text)

        # Ultimo recurso: se citou um objeto conhecido, trata como busca.
        label = self._find_label(normalized)
        if label:
            return IntentMatch(Intent.FIND_OBJECT, 0.5, {"label": label}, text)
        return IntentMatch(Intent.UNKNOWN, 0.0, raw_text=text)

    def _extract_slots(self, intent: Intent, normalized: str) -> dict[str, Any]:
        if intent not in (Intent.FIND_OBJECT, Intent.COUNT_OBJECT):
            return {}
        label = self._find_label(normalized)
        return {"label": label} if label else {}

    @staticmethod
    def _find_label(normalized: str) -> str | None:
        """Procura um nome de classe conhecido no texto, do mais longo ao mais curto."""
        for label, (name_pt, _) in _SORTED_LABELS:
            if re.search(rf"\b{re.escape(normalize(name_pt))}s?\b", normalized):
                return label
        for token in normalized.split():
            if token in _STOPWORDS or len(token) < 3:
                continue
            label = resolve_label_from_pt(token)
            if label:
                return label
        return None


# Nomes mais longos primeiro para "vaso sanitario" nao casar antes com "vaso".
_SORTED_LABELS = sorted(COCO_PT.items(), key=lambda item: -len(item[1][0]))
