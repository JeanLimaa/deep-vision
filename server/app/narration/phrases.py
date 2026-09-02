"""Frases em pt-BR entregues ao usuario.

Centralizar os textos aqui mantem o tom consistente e permite revisa-los sem
mexer na logica -- o que importa em tecnologia assistiva, onde verbosidade
excessiva cansa e leva ao abandono do dispositivo (LANUTTI, 2019).
"""

from __future__ import annotations

from app.core.types import Direction, TrackedObject, Zone
from app.vision.labels import label_pt, label_pt_with_article
from app.vision.spatial import describe_direction, describe_distance

SYSTEM = {
    "started": "Assistente ativado.",
    "stopped": "Assistente pausado.",
    "connected": "Dispositivo conectado.",
    "disconnected": "Conexao com o dispositivo perdida.",
    "no_objects": "Nao identifiquei objetos a frente.",
    "path_clear": "Caminho livre.",
    "not_understood": "Nao entendi o comando.",
    "vision_offline": "O reconhecimento de imagens esta indisponivel.",
    "volume_changed": "Volume {step} de 10.",
}

ZONE_ALERTS = {
    Zone.CRITICAL: "Atencao, obstaculo {distance} {direction}.",
    Zone.WARNING: "Obstaculo {direction}, {distance}.",
}


def object_phrase(track: TrackedObject, with_distance: bool = True) -> str:
    """Ex.: "uma cadeira a frente, a cerca de dois metros"."""
    parts = [label_pt_with_article(track.label), describe_direction(track.direction)]
    if with_distance:
        distance = describe_distance(track.distance_m)
        if distance:
            parts.append(distance)
    return ", ".join(parts)


def scene_phrase(tracks: list[TrackedObject], with_distance: bool = True) -> str:
    """Resumo de varios objetos, respondendo a "o que tem a minha frente?"."""
    if not tracks:
        return SYSTEM["no_objects"]
    phrases = [object_phrase(t, with_distance) for t in tracks]
    if len(phrases) == 1:
        return _capitalize(phrases[0]) + "."
    return _capitalize("; ".join(phrases[:-1]) + f"; e {phrases[-1]}") + "."


def new_object_phrase(track: TrackedObject, with_distance: bool = True) -> str:
    return _capitalize(object_phrase(track, with_distance)) + "."


def obstacle_phrase(zone: Zone, distance_m: float | None, direction: Direction) -> str:
    template = ZONE_ALERTS.get(zone)
    if template is None:
        return SYSTEM["path_clear"]
    return template.format(
        distance=_distance_for_alert(distance_m),
        direction=describe_direction(direction),
    ).replace(" ,", ",")


def count_phrase(label: str, count: int) -> str:
    """Resposta a "quantas pessoas tem aqui?"."""
    name = label_pt(label)
    if count == 0:
        return f"Nao vejo nenhum {name}."
    if count == 1:
        return f"Vejo um {name}."
    plural = name + ("s" if not name.endswith("s") else "")
    return f"Vejo {count} {plural}."


def presence_phrase(track: TrackedObject | None, label: str) -> str:
    """Resposta a "tem uma cadeira aqui?"."""
    if track is None:
        return f"Nao encontrei {label_pt(label)} por perto."
    return _capitalize(f"sim, {object_phrase(track)}") + "."


def _distance_for_alert(distance_m: float | None) -> str:
    if distance_m is None:
        return "muito proximo"
    if distance_m < 1.0:
        return f"a {int(round(distance_m * 100))} centimetros"
    return f"a {distance_m:.1f} metros".replace(".", ",")


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text
