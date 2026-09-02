"""Rastreamento, interpretacao espacial e detector simulado."""

from __future__ import annotations

import numpy as np

from app.core.types import BoundingBox, Detection, Direction
from app.settings import CameraSettings, TrackingSettings, VisionSettings
from app.vision.fake_detector import FakeDetector
from app.vision.spatial import SpatialEstimator, describe_distance
from app.vision.tracking import IouTracker


def box(x1, y1, x2, y2) -> BoundingBox:
    return BoundingBox(x1, y1, x2, y2)


def test_tracker_keeps_identity_across_frames():
    tracker = IouTracker(TrackingSettings(min_hits=1))
    first = tracker.update([Detection("person", 0.9, box(10, 10, 60, 120))], at_ms=0)
    second = tracker.update([Detection("person", 0.9, box(14, 12, 64, 122))], at_ms=100)
    assert first[0].track_id == second[0].track_id
    assert second[0].hits == 2


def test_tracker_separates_distinct_objects():
    tracker = IouTracker(TrackingSettings(min_hits=1))
    tracks = tracker.update(
        [
            Detection("person", 0.9, box(0, 0, 50, 100)),
            Detection("person", 0.8, box(300, 0, 350, 100)),
        ],
        at_ms=0,
    )
    assert len({track.track_id for track in tracks}) == 2


def test_tracker_drops_object_after_max_misses():
    tracker = IouTracker(TrackingSettings(min_hits=1, max_misses=2))
    tracker.update([Detection("chair", 0.9, box(0, 0, 40, 40))], at_ms=0)
    for tick in range(4):
        tracker.update([], at_ms=100 * (tick + 1))
    assert tracker.tracks == []


def test_direction_is_derived_from_horizontal_position():
    spatial = SpatialEstimator(CameraSettings(center_band=0.34))
    assert spatial.direction_of(box(0, 0, 100, 100), 640) is Direction.LEFT
    assert spatial.direction_of(box(280, 0, 360, 100), 640) is Direction.CENTER
    assert spatial.direction_of(box(540, 0, 640, 100), 640) is Direction.RIGHT


def test_distance_estimate_follows_the_pinhole_model():
    spatial = SpatialEstimator(CameraSettings(horizontal_fov_deg=66.0))
    focal = spatial.focal_length_px(640)
    # Uma pessoa (1,70 m) a 3 m ocupa 1.70 * f / 3 pixels de altura.
    height = 1.70 * focal / 3.0
    estimated = spatial.distance_of("person", box(0, 0, 50, height), 640)
    assert estimated is not None
    assert abs(estimated - 3.0) < 0.05


def test_distance_is_omitted_for_unknown_class():
    spatial = SpatialEstimator(CameraSettings())
    assert spatial.distance_of("classe-inexistente", box(0, 0, 50, 100), 640) is None


def test_distance_description_is_rounded_for_speech():
    assert describe_distance(None) == ""
    assert describe_distance(0.4) == "bem perto"
    assert "metros" in describe_distance(2.4)


def test_fake_detector_produces_coherent_stream():
    detector = FakeDetector(VisionSettings(backend="fake", confidence_threshold=0.3))
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    frames = [detector.detect(image) for _ in range(30)]
    assert any(frames), "o detector simulado deve produzir deteccoes"
    for detections in frames:
        for detection in detections:
            assert 0 <= detection.box.x1 <= 640
            assert detection.confidence >= 0.3
