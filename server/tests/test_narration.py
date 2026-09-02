"""Composicao e agendamento da fala.

Estes testes protegem o requisito de usabilidade mais importante do projeto: o
usuario nao pode ser soterrado de informacao. Falar demais e o caminho mais
curto para o abandono do dispositivo.
"""

from __future__ import annotations

from app.core.types import (
    BoundingBox,
    Direction,
    Priority,
    ProximityReading,
    TrackedObject,
    Utterance,
    Zone,
)
from app.narration.composer import NarrationComposer
from app.narration.scheduler import SpeechScheduler
from app.settings import NarrationSettings, ProximitySettings


def make_track(label="chair", direction=Direction.CENTER, distance=2.0, track_id=1):
    return TrackedObject(
        track_id=track_id,
        label=label,
        confidence=0.9,
        box=BoundingBox(0, 0, 100, 200),
        direction=direction,
        distance_m=distance,
        first_seen_ms=0,
        last_seen_ms=0,
    )


def make_composer(**overrides) -> NarrationComposer:
    return NarrationComposer(NarrationSettings(**overrides), ProximitySettings())


# ------------------------------------------------------------------ composer


def test_new_object_is_announced_once():
    composer = make_composer()
    track = make_track()
    assert composer.compose_objects([track])
    assert composer.compose_objects([track]) == []


def test_object_is_reannounced_when_it_changes_side():
    composer = make_composer()
    track = make_track()
    composer.compose_objects([track])
    track.direction = Direction.LEFT
    assert composer.compose_objects([track])


def test_object_is_reannounced_when_it_gets_closer():
    composer = make_composer()
    track = make_track(distance=2.5)
    composer.compose_objects([track])
    track.distance_m = 0.5  # entrou na zona critica
    assert composer.compose_objects([track])


def test_hazard_classes_get_higher_priority():
    composer = make_composer()
    [utterance] = composer.compose_objects([make_track(label="car")])
    assert utterance.priority is Priority.ALERT


def test_closest_object_is_announced_first():
    composer = make_composer(max_objects_per_utterance=1)
    far = make_track(label="chair", distance=4.0, track_id=1)
    near = make_track(label="backpack", distance=0.9, track_id=2)
    [utterance] = composer.compose_objects([far, near])
    assert "mochila" in utterance.text


def test_autonomous_narration_can_be_disabled():
    composer = make_composer(autonomous_narration=False)
    assert composer.compose_objects([make_track()]) == []


def test_critical_obstacle_interrupts():
    composer = make_composer()
    reading = ProximityReading("front", 0.5, Zone.CRITICAL, 0)
    utterance = composer.compose_obstacle(reading, Zone.CRITICAL)
    assert utterance is not None
    assert utterance.interrupt
    assert utterance.priority is Priority.CRITICAL
    assert "centimetros" in utterance.text


def test_safe_zone_produces_no_alert():
    composer = make_composer()
    assert composer.compose_obstacle(None, Zone.SAFE) is None


def test_scene_answer_lists_multiple_objects():
    composer = make_composer(max_objects_per_utterance=3)
    utterance = composer.compose_scene_answer(
        [make_track("chair", track_id=1), make_track("person", track_id=2, distance=1.0)]
    )
    assert "pessoa" in utterance.text and "cadeira" in utterance.text


# ----------------------------------------------------------------- scheduler


def test_higher_priority_is_spoken_first():
    scheduler = SpeechScheduler(NarrationSettings())
    scheduler.submit(Utterance("informacao", Priority.INFO))
    scheduler.submit(Utterance("alerta", Priority.CRITICAL))
    assert scheduler.pop().text == "alerta"


def test_same_priority_keeps_arrival_order():
    scheduler = SpeechScheduler(NarrationSettings())
    scheduler.submit(Utterance("primeira", Priority.INFO))
    scheduler.submit(Utterance("segunda", Priority.INFO))
    assert scheduler.pop().text == "primeira"


def test_cooldown_suppresses_repeated_key():
    scheduler = SpeechScheduler(NarrationSettings(object_cooldown_ms=5000))
    assert scheduler.submit(Utterance("cadeira", Priority.INFO, dedupe_key="obj:chair"), at_ms=0)
    assert not scheduler.submit(Utterance("cadeira", Priority.INFO, dedupe_key="obj:chair"), at_ms=1000)
    assert scheduler.submit(Utterance("cadeira", Priority.INFO, dedupe_key="obj:chair"), at_ms=6000)


def test_interrupt_drops_lower_priority_backlog():
    scheduler = SpeechScheduler(NarrationSettings())
    scheduler.submit(Utterance("informacao", Priority.INFO))
    scheduler.submit(Utterance("perigo", Priority.CRITICAL, interrupt=True))
    assert len(scheduler) == 1
    assert scheduler.pop().text == "perigo"


def test_queue_never_grows_past_the_limit():
    scheduler = SpeechScheduler(NarrationSettings(), max_queue=3)
    for index in range(10):
        scheduler.submit(Utterance(f"frase {index}", Priority.INFO))
    assert len(scheduler) <= 3
