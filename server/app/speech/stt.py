"""Reconhecimento de fala (Speech-to-Text) -- contrato e fabrica.

A Secao 5.5 do TCC descreve o fluxo inverso ao da imagem: o audio captado no
dispositivo sobe para o servidor, que o transcreve e casa a intencao. Como o
microfone ainda nao esta montado, o canal textual (painel web e simulador)
alimenta exatamente o mesmo pipeline de intencoes -- trocar a fonte nao muda
uma linha de ``speech.intents`` nem de ``speech.commands``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.settings import SpeechSettings

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    confidence: float = 0.0
    language: str = "pt"


@runtime_checkable
class SpeechToText(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    def transcribe(self, audio: bytes, mime: str = "audio/wav") -> Transcript | None: ...


class NullSTT:
    """Sem transcricao. Comandos continuam disponiveis por texto."""

    name = "null"

    @property
    def available(self) -> bool:
        return False

    def transcribe(self, audio: bytes, mime: str = "audio/wav") -> Transcript | None:  # noqa: ARG002
        return None


class FasterWhisperSTT:
    """Transcricao offline com faster-whisper (CTranslate2).

    Escolhido por rodar em CPU com ``int8`` e nao depender de servico externo --
    o que mantem o sistema funcional em rede local, como preve a arquitetura.
    """

    name = "faster-whisper"

    def __init__(self, settings: SpeechSettings) -> None:
        from faster_whisper import WhisperModel  # import tardio: dependencia opcional

        device, compute_type = self._pick_device(settings.whisper_compute_type)
        self._model = WhisperModel(
            settings.whisper_model, device=device, compute_type=compute_type
        )
        self._settings = settings
        self._available = True
        log.info("STT faster-whisper '%s' em %s/%s", settings.whisper_model, device, compute_type)

    @staticmethod
    def _pick_device(preferred_compute: str) -> tuple[str, str]:
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda", "float16"
        except Exception:  # noqa: BLE001
            pass
        return "cpu", preferred_compute

    @property
    def available(self) -> bool:
        return self._available

    def transcribe(self, audio: bytes, mime: str = "audio/wav") -> Transcript | None:  # noqa: ARG002
        import io

        try:
            segments, info = self._model.transcribe(
                io.BytesIO(audio),
                language=self._settings.language,
                vad_filter=True,
                beam_size=1,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha na transcricao: %s", exc)
            return None
        if not text:
            return None
        return Transcript(
            text=text,
            confidence=float(getattr(info, "language_probability", 0.0) or 0.0),
            language=getattr(info, "language", self._settings.language),
        )


def build_stt(settings: SpeechSettings) -> SpeechToText:
    """Instancia o motor de STT, degradando para ``null`` quando indisponivel."""
    if settings.stt_engine == "null":
        return NullSTT()
    try:
        return FasterWhisperSTT(settings)
    except Exception as exc:  # noqa: BLE001
        if settings.stt_engine == "whisper":
            raise
        log.info("STT indisponivel (%s); comandos ficam apenas por texto.", exc)
        return NullSTT()
