"""Conversao de coordenadas do backend ONNX (nao exige onnxruntime)."""

import numpy as np
import pytest

from app.vision.onnx_detector import letterbox, unletterbox


@pytest.mark.parametrize("shape", [(480, 640), (720, 1280), (640, 480), (640, 640)])
def test_letterbox_keeps_aspect_and_fills_input(shape):
    image = np.zeros((*shape, 3), dtype=np.uint8)
    blob, ratio, pad_x, pad_y = letterbox(image, 640, 640)
    assert blob.shape == (1, 3, 640, 640)
    assert blob.dtype == np.float32
    # Um dos lados ocupa a entrada inteira; o outro e centralizado.
    assert min(pad_x, pad_y) == 0
    assert ratio == pytest.approx(min(640 / shape[1], 640 / shape[0]))


def test_box_round_trip_returns_to_image_coordinates():
    height, width = 480, 640
    _, ratio, pad_x, pad_y = letterbox(np.zeros((height, width, 3), np.uint8), 640, 640)
    original = (100.0, 50.0, 300.0, 400.0)
    in_input = (
        original[0] * ratio + pad_x,
        original[1] * ratio + pad_y,
        original[2] * ratio + pad_x,
        original[3] * ratio + pad_y,
    )
    box = unletterbox(in_input, ratio, pad_x, pad_y, width, height)
    assert (box.x1, box.y1, box.x2, box.y2) == pytest.approx(original)


def test_boxes_are_clipped_to_the_image():
    box = unletterbox((-20.0, -20.0, 900.0, 900.0), 1.0, 0.0, 80.0, 640, 480)
    assert (box.x1, box.y1, box.x2, box.y2) == (0.0, 0.0, 640.0, 480.0)
