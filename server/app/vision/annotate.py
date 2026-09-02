"""Desenho das deteccoes sobre o quadro, para o painel e para as figuras do TCC."""

from __future__ import annotations

import cv2
import numpy as np

from app.core.types import TrackedObject, Zone
from app.vision.labels import label_pt

_ZONE_COLORS: dict[Zone, tuple[int, int, int]] = {
    Zone.SAFE: (90, 200, 90),
    Zone.WARNING: (40, 180, 240),
    Zone.CRITICAL: (60, 60, 235),
}

_FONT = cv2.FONT_HERSHEY_SIMPLEX


def draw_tracks(
    image: np.ndarray,
    tracks: list[TrackedObject],
    zone: Zone = Zone.SAFE,
    nearest_distance_m: float | None = None,
) -> np.ndarray:
    """Devolve uma copia anotada com caixas, rotulos e faixa de status."""
    canvas = image.copy()
    for track in tracks:
        color = _ZONE_COLORS[Zone.CRITICAL] if track.distance_m and track.distance_m < 1.0 else (
            _ZONE_COLORS[Zone.SAFE]
        )
        x1, y1 = int(track.box.x1), int(track.box.y1)
        x2, y2 = int(track.box.x2), int(track.box.y2)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)

        caption = f"{label_pt(track.label)} {track.confidence:.0%}"
        if track.distance_m is not None:
            caption += f" ~{track.distance_m:.1f}m"
        _draw_caption(canvas, caption, x1, y1, color)

    _draw_status_bar(canvas, zone, nearest_distance_m, len(tracks))
    return canvas


def _draw_caption(canvas: np.ndarray, text: str, x: int, y: int, color) -> None:
    (tw, th), baseline = cv2.getTextSize(text, _FONT, 0.5, 1)
    top = max(0, y - th - baseline - 4)
    cv2.rectangle(canvas, (x, top), (x + tw + 6, top + th + baseline + 4), color, -1)
    cv2.putText(canvas, text, (x + 3, top + th + 2), _FONT, 0.5, (20, 20, 20), 1, cv2.LINE_AA)


def _draw_status_bar(
    canvas: np.ndarray, zone: Zone, nearest_distance_m: float | None, count: int
) -> None:
    height, width = canvas.shape[:2]
    bar_height = 28
    overlay = canvas.copy()
    cv2.rectangle(overlay, (0, height - bar_height), (width, height), (30, 30, 30), -1)
    cv2.addWeighted(overlay, 0.65, canvas, 0.35, 0, canvas)

    distance = f"{nearest_distance_m:.2f} m" if nearest_distance_m is not None else "--"
    text = f"objetos: {count}   sonar: {distance}   zona: {zone.value}"
    cv2.putText(
        canvas,
        text,
        (8, height - 9),
        _FONT,
        0.5,
        _ZONE_COLORS[zone],
        1,
        cv2.LINE_AA,
    )


def encode_jpeg(image: np.ndarray, quality: int = 80) -> bytes:
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return buffer.tobytes() if ok else b""
