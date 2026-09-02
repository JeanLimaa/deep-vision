"""Sintese de voz (Text-to-Speech) -- contrato e fabrica de motores.

O TCC (Secao 5.5) preve que o servidor converta a resposta textual em audio e
devolva ao dispositivo. A interface abaixo isola essa decisao: qualquer motor
serve desde que produza bytes de audio a partir de um texto.
"""

from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.settings import TTSSettings

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SynthesizedAudio:
    data: bytes
    mime: str
    path: Path | None = None

    @property
    def extension(self) -> str:
        return {"audio/mpeg": ".mp3", "audio/wav": ".wav"}.get(self.mime, ".bin")


@runtime_checkable
class TextToSpeech(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    def synthesize(self, text: str) -> SynthesizedAudio | None: ...


class NullTTS:
    """Motor inerte: nao gera audio, apenas mantem o fluxo textual.

    E o que permite validar toda a logica de narracao (fila, prioridades,
    cooldowns) sem nenhum hardware ou dependencia de audio instalada.
    """

    name = "null"

    @property
    def available(self) -> bool:
        return True

    def synthesize(self, text: str) -> SynthesizedAudio | None:  # noqa: ARG002
        return None


class Pyttsx3TTS:
    """Sintese offline via SAPI5 (Windows), NSSpeechSynthesizer (macOS) ou espeak.

    O pyttsx3 nao e reentrante: cada sintese cria e destroi o motor dentro de
    um lock. O custo e aceitavel porque o resultado vai para o cache em disco.
    """

    name = "pyttsx3"

    def __init__(self, settings: TTSSettings) -> None:
        import pyttsx3  # import tardio: dependencia opcional

        self._pyttsx3 = pyttsx3
        self._settings = settings
        self._voice_id = self._pick_voice(settings.voice, settings.language_hints)
        self._available = True

    @property
    def available(self) -> bool:
        return self._available

    def _pick_voice(self, requested: str, hints: tuple[str, ...]) -> str | None:
        """Escolhe uma voz pt-BR quando existir; caso contrario usa a padrao."""
        engine = self._pyttsx3.init()
        try:
            voices = engine.getProperty("voices") or []
            if requested:
                for voice in voices:
                    if requested.lower() in (voice.id or "").lower() or requested.lower() in (
                        voice.name or ""
                    ).lower():
                        return voice.id
            for voice in voices:
                haystack = f"{voice.id} {voice.name} {getattr(voice, 'languages', '')}".lower()
                if any(hint in haystack for hint in hints):
                    return voice.id
        finally:
            engine.stop()
        return None

    def synthesize(self, text: str) -> SynthesizedAudio | None:
        import tempfile

        engine = self._pyttsx3.init()
        try:
            if self._voice_id:
                engine.setProperty("voice", self._voice_id)
            engine.setProperty("rate", self._settings.rate_wpm)
            engine.setProperty("volume", self._settings.volume)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                temp_path = Path(handle.name)
            engine.save_to_file(text, str(temp_path))
            engine.runAndWait()
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha na sintese pyttsx3: %s", exc)
            self._available = False
            return None
        finally:
            engine.stop()

        try:
            data = temp_path.read_bytes()
        finally:
            temp_path.unlink(missing_ok=True)
        return SynthesizedAudio(data=data, mime="audio/wav") if data else None


class GTTSEngine:
    """Sintese online (Google Translate TTS). Melhor prosodia, exige internet."""

    name = "gtts"

    def __init__(self, settings: TTSSettings) -> None:
        from gtts import gTTS  # import tardio: dependencia opcional

        self._gtts = gTTS
        self._settings = settings
        self._available = True

    @property
    def available(self) -> bool:
        return self._available

    def synthesize(self, text: str) -> SynthesizedAudio | None:
        import io

        try:
            buffer = io.BytesIO()
            self._gtts(text=text, lang="pt", tld="com.br").write_to_fp(buffer)
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha na sintese gTTS: %s", exc)
            self._available = False
            return None
        return SynthesizedAudio(data=buffer.getvalue(), mime="audio/mpeg")


class CachingTTS:
    """Decorador que memoriza sinteses em disco.

    As frases do sistema se repetem muito ("pessoa a frente", "caminho livre"),
    entao o cache remove a sintese do caminho critico na maioria dos casos.
    """

    def __init__(self, engine: TextToSpeech, cache_dir: Path, cache_size: int = 128) -> None:
        self._engine = engine
        self._cache_dir = cache_dir
        self._cache_size = cache_size
        self._index: OrderedDict[str, SynthesizedAudio] = OrderedDict()
        cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return self._engine.name

    @property
    def available(self) -> bool:
        return self._engine.available

    @property
    def inner(self) -> TextToSpeech:
        return self._engine

    def synthesize(self, text: str) -> SynthesizedAudio | None:
        key = hashlib.sha1(f"{self._engine.name}:{text}".encode()).hexdigest()[:16]
        cached = self._index.get(key)
        if cached is not None:
            self._index.move_to_end(key)
            return cached

        audio = self._engine.synthesize(text)
        if audio is None:
            return None

        path = self._cache_dir / f"{key}{audio.extension}"
        try:
            path.write_bytes(audio.data)
        except OSError as exc:  # pragma: no cover - disco cheio/permissao
            log.warning("Nao foi possivel gravar o cache de TTS: %s", exc)
            path = None

        stored = SynthesizedAudio(data=audio.data, mime=audio.mime, path=path)
        self._index[key] = stored
        self._index.move_to_end(key)
        while len(self._index) > self._cache_size:
            _, evicted = self._index.popitem(last=False)
            if evicted.path is not None:
                evicted.path.unlink(missing_ok=True)
        return stored


def build_tts(settings: TTSSettings) -> TextToSpeech:
    """Instancia o motor configurado, com degradacao para ``null``."""
    # Em "auto" tenta offline, depois online, e por fim degrada para textual.
    order = ["pyttsx3", "gtts", "null"] if settings.engine == "auto" else [settings.engine]

    for choice in order:
        try:
            if choice == "pyttsx3":
                engine: TextToSpeech = Pyttsx3TTS(settings)
            elif choice == "gtts":
                engine = GTTSEngine(settings)
            else:
                engine = NullTTS()
        except Exception as exc:  # noqa: BLE001
            log.info("Motor de TTS '%s' indisponivel: %s", choice, exc)
            continue
        if isinstance(engine, NullTTS):
            if settings.engine == "auto":
                log.warning("Nenhum motor de TTS disponivel; saida ficara apenas textual.")
            return engine
        log.info("Motor de TTS: %s", engine.name)
        return CachingTTS(engine, settings.cache_dir, settings.cache_size)

    return NullTTS()
