"""Fixtures compartilhadas.

Todos os testes rodam com o detector simulado e sem audio real, para que a
suite execute em qualquer maquina -- sem GPU, sem modelo baixado e sem
alto-falante.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.settings import Settings


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        environment="dev",
        log_level="WARNING",
        vision={"backend": "fake", "warmup_on_startup": False, "max_inference_fps": 0},
        tts={"engine": "null"},
        speech={"stt_engine": "null"},
        audio={"sinks": ["null"]},
        storage={
            "var_dir": tmp_path,
            "snapshots_dir": tmp_path / "snapshots",
            "raw_dir": tmp_path / "raw",
            "event_log_path": tmp_path / "events.jsonl",
            "log_events": False,
        },
    )


@pytest.fixture()
def jpeg_frame() -> bytes:
    """Quadro JPEG valido, com gradiente para nao ser uma imagem degenerada."""
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[:, :, 0] = np.linspace(0, 255, 640, dtype=np.uint8)
    image[:, :, 1] = 90
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()
