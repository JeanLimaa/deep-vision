"""Execucao dos comandos de voz/texto.

Recebe uma ``IntentMatch`` e produz a resposta falada. O roteador nao sabe se o
comando veio de um microfone, do painel web ou do simulador -- e essa
indiferenca que permite testar toda a interacao por voz antes de o microfone
existir.
"""

from __future__ import annotations

import logging

from app.audio.output import AudioOutputService
from app.core.events import Event, EventBus, EventType
from app.core.schemas import CommandResultOut
from app.core.types import Priority, TrackedObject, Utterance
from app.devices.session import DeviceSession
from app.narration import phrases
from app.pipeline.orchestrator import AssistivePipeline
from app.speech.intents import Intent, IntentMatch, IntentMatcher

log = logging.getLogger(__name__)

HELP_TEXT = (
    "Voce pode pedir: descrever o ambiente, procurar um objeto, "
    "contar quantos objetos ha, saber se o caminho esta livre, "
    "iniciar ou pausar o assistente, e ajustar o volume."
)


class CommandRouter:
    """Mapeia intencoes em acoes sobre a sessao e em respostas faladas."""

    def __init__(
        self,
        matcher: IntentMatcher,
        pipeline: AssistivePipeline,
        audio: AudioOutputService,
        bus: EventBus,
    ) -> None:
        self._matcher = matcher
        self._pipeline = pipeline
        self._audio = audio
        self._bus = bus

    async def handle_text(self, session: DeviceSession, text: str, source: str) -> CommandResultOut:
        match = self._matcher.match(text)
        await self._bus.publish(
            Event(
                EventType.COMMAND_RECEIVED,
                {"text": text, "source": source, "intent": match.intent.value},
                device_id=session.device_id,
            )
        )
        reply = await self._dispatch(session, match)
        if reply:
            self._audio.submit(
                Utterance(text=reply, priority=Priority.ANSWER, interrupt=True)
            )
        await self._bus.publish(
            Event(
                EventType.COMMAND_HANDLED,
                {"intent": match.intent.value, "reply": reply, "slots": match.slots},
                device_id=session.device_id,
            )
        )
        return CommandResultOut(
            understood=match.understood,
            intent=match.intent.value,
            reply=reply,
            slots=match.slots,
        )

    # ------------------------------------------------------------- despacho

    async def _dispatch(self, session: DeviceSession, match: IntentMatch) -> str:
        handler = _HANDLERS.get(match.intent, CommandRouter._handle_unknown)
        return await handler(self, session, match)

    async def _handle_describe(self, session: DeviceSession, match: IntentMatch) -> str:  # noqa: ARG002
        if not session.active:
            return phrases.SYSTEM["stopped"]
        utterance = session.composer.compose_scene_answer(session.last_tracks)
        return utterance.text

    async def _handle_find(self, session: DeviceSession, match: IntentMatch) -> str:
        label = match.slots.get("label")
        if not label:
            return await self._handle_describe(session, match)
        found = _nearest_with_label(session.last_tracks, label)
        return phrases.presence_phrase(found, label)

    async def _handle_count(self, session: DeviceSession, match: IntentMatch) -> str:
        label = match.slots.get("label")
        if not label:
            return await self._handle_describe(session, match)
        count = sum(1 for track in session.last_tracks if track.label == label)
        return phrases.count_phrase(label, count)

    async def _handle_obstacle(self, session: DeviceSession, match: IntentMatch) -> str:  # noqa: ARG002
        distance = session.nearest_distance_m
        if distance is None:
            return phrases.SYSTEM["path_clear"]
        return phrases.obstacle_phrase(session.zone, distance, _front_direction(session))

    async def _handle_start(self, session: DeviceSession, match: IntentMatch) -> str:  # noqa: ARG002
        session.set_active(True)
        return phrases.SYSTEM["started"]

    async def _handle_stop(self, session: DeviceSession, match: IntentMatch) -> str:  # noqa: ARG002
        session.set_active(False)
        self._audio.clear()
        return phrases.SYSTEM["stopped"]

    async def _handle_volume_up(self, session: DeviceSession, match: IntentMatch) -> str:  # noqa: ARG002
        return phrases.SYSTEM["volume_changed"].format(step=self._audio.adjust_volume(+1))

    async def _handle_volume_down(self, session: DeviceSession, match: IntentMatch) -> str:  # noqa: ARG002
        return phrases.SYSTEM["volume_changed"].format(step=self._audio.adjust_volume(-1))

    async def _handle_repeat(self, session: DeviceSession, match: IntentMatch) -> str:  # noqa: ARG002
        history = self._audio.history
        return history[-1] if history else phrases.SYSTEM["no_objects"]

    async def _handle_status(self, session: DeviceSession, match: IntentMatch) -> str:  # noqa: ARG002
        state = "ativo" if session.active else "pausado"
        parts = [f"Assistente {state}."]
        if session.battery_pct is not None:
            parts.append(f"Bateria em {int(session.battery_pct)} por cento.")
        objects = len(session.last_tracks)
        parts.append(
            "Nenhum objeto identificado." if objects == 0 else f"{objects} objetos por perto."
        )
        return " ".join(parts)

    async def _handle_help(self, session: DeviceSession, match: IntentMatch) -> str:  # noqa: ARG002
        return HELP_TEXT

    async def _handle_unknown(self, session: DeviceSession, match: IntentMatch) -> str:  # noqa: ARG002
        return phrases.SYSTEM["not_understood"]


_HANDLERS = {
    Intent.DESCRIBE_SCENE: CommandRouter._handle_describe,
    Intent.FIND_OBJECT: CommandRouter._handle_find,
    Intent.COUNT_OBJECT: CommandRouter._handle_count,
    Intent.OBSTACLE_STATUS: CommandRouter._handle_obstacle,
    Intent.START: CommandRouter._handle_start,
    Intent.STOP: CommandRouter._handle_stop,
    Intent.VOLUME_UP: CommandRouter._handle_volume_up,
    Intent.VOLUME_DOWN: CommandRouter._handle_volume_down,
    Intent.REPEAT: CommandRouter._handle_repeat,
    Intent.STATUS: CommandRouter._handle_status,
    Intent.HELP: CommandRouter._handle_help,
    Intent.UNKNOWN: CommandRouter._handle_unknown,
}


def _nearest_with_label(tracks: list[TrackedObject], label: str) -> TrackedObject | None:
    candidates = [track for track in tracks if track.label == label]
    if not candidates:
        return None
    with_distance = [t for t in candidates if t.distance_m is not None]
    if with_distance:
        return min(with_distance, key=lambda t: t.distance_m)
    return max(candidates, key=lambda t: t.box.area)


def _front_direction(session: DeviceSession):
    from app.core.types import Direction, now_ms
    from app.proximity.fusion import SENSOR_DIRECTIONS

    nearest = session.fusion.nearest(now_ms())
    if nearest is None:
        return Direction.CENTER
    return SENSOR_DIRECTIONS.get(nearest.sensor_id, Direction.CENTER)

