"""Rastreamento, interpretacao espacial e detector simulado."""

from __future__ import annotations

import numpy as np
import pytest

from app.core.types import BoundingBox, Detection, Direction
from app.settings import CameraSettings, TrackingSettings, VisionSettings
from app.vision.annotate import draw_tracks
from app.vision.detector import (
    CompositeDetector,
    DetectorUnavailableError,
    allowed_class_ids,
    build_detector,
)
from app.vision.fake_detector import FakeDetector
from app.vision.labels import COCO_PT, MOBILITY_LABELS, canonical_label, label_pt
from app.vision.spatial import SpatialEstimator, describe_distance
from app.vision.tracking import RELABEL_IOU, IouTracker


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


def test_flickering_label_stays_one_object_with_the_majority_label():
    # A mesma cadeira, rotulada "couch" em um quadro de cada tres.
    tracker = IouTracker(TrackingSettings(min_hits=1))
    labels = ["chair", "chair", "couch", "chair", "chair", "couch", "chair"]
    for tick, label in enumerate(labels):
        tracks = tracker.update([Detection(label, 0.6, box(100, 100, 300, 400))], at_ms=tick)
    assert len(tracker.tracks) == 1
    assert tracks[0].label == "chair"


def test_persistent_relabel_wins_the_vote():
    tracker = IouTracker(TrackingSettings(min_hits=1))
    for tick in range(3):
        tracker.update([Detection("couch", 0.5, box(100, 100, 300, 400))], at_ms=tick)
    for tick in range(3, 9):
        tracks = tracker.update([Detection("chair", 0.8, box(100, 100, 300, 400))], at_ms=tick)
    assert tracks[0].label == "chair"


def test_overlapping_distinct_objects_are_not_merged():
    # Pessoa sentada numa cadeira: caixas sobrepostas, IoU ~0,4.
    tracker = IouTracker(TrackingSettings(min_hits=1))
    person, chair = box(100, 50, 300, 450), box(120, 250, 320, 450)
    assert 0.3 < person.iou(chair) < RELABEL_IOU
    tracker.update([Detection("person", 0.9, person), Detection("chair", 0.7, chair)], at_ms=0)
    tracks = tracker.update([Detection("chair", 0.7, chair)], at_ms=1)
    assert {t.label for t in tracks} == {"person", "chair"}
    assert len(tracker.tracks) == 2


def test_intermittent_noise_is_never_confirmed():
    # Um "cat" que aparece um quadro sim, outro nao: nunca 3 seguidos.
    tracker = IouTracker(TrackingSettings(min_hits=3))
    for tick in range(12):
        detections = [Detection("cat", 0.4, box(10, 10, 60, 60))] if tick % 2 == 0 else []
        assert tracker.update(detections, at_ms=tick) == []


def test_track_is_hidden_soon_after_the_object_leaves():
    tracker = IouTracker(TrackingSettings(min_hits=3, coast_frames=2, max_misses=8))
    for tick in range(3):
        visible = tracker.update([Detection("person", 0.9, box(0, 0, 50, 100))], at_ms=tick)
    assert len(visible) == 1
    shown = [len(tracker.update([], at_ms=10 + tick)) for tick in range(4)]
    # Tolera duas falhas (a caixa nao pisca), depois some da tela...
    assert shown == [1, 1, 0, 0]
    # ...mas a identidade sobrevive: quando o objeto volta, nao e um novo anuncio.
    back = tracker.update([Detection("person", 0.9, box(0, 0, 50, 100))], at_ms=20)
    assert back and back[0].track_id == visible[0].track_id


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


def test_allowed_labels_map_to_model_class_ids():
    names = {0: "person", 1: "bicycle", 2: "giraffe"}
    assert allowed_class_ids(names, ["person", "bicycle", "door"]) == [0, 1]
    assert allowed_class_ids(names, []) is None


def test_allowlist_unknown_to_the_model_does_not_blank_the_detector():
    # Modelo proprio com outros nomes: filtrar zeraria todas as deteccoes.
    assert allowed_class_ids({0: "degrau", 1: "poste"}, ["person"]) is None


def test_mobility_profile_drops_classes_that_never_help_a_pedestrian():
    allowed = set(VisionSettings().allowed_labels)
    assert {"person", "car", "chair", "bicycle", "cell phone", "door"} <= allowed
    assert not {"giraffe", "airplane", "pizza", "skis", "tie"} & allowed


class _StubDetector:
    def __init__(self, labels, detections, model_path):
        self.labels = frozenset(labels)
        self._detections = detections
        self.model_path = model_path
        self.device = "cpu"
        self.ready = True

    def warmup(self):
        pass

    def detect(self, image):
        return list(self._detections)


def test_extra_model_only_adds_classes_the_main_model_lacks():
    person = Detection("person", 0.9, box(0, 0, 10, 10))
    main = _StubDetector({"person", "chair"}, [person], "yolo26s.pt")
    # O extra tambem conhece "chair": essa deteccao duplicaria a do principal.
    extra = _StubDetector(
        {"chair", "stairs"},
        [Detection("chair", 0.8, box(5, 5, 20, 20)), Detection("stairs", 0.7, box(30, 30, 90, 90))],
        "urbano.pt",
    )
    composite = CompositeDetector(main, [extra])
    labels = [d.label for d in composite.detect(np.zeros((100, 100, 3), np.uint8))]
    assert labels == ["person", "stairs"]
    assert composite.labels == {"person", "chair", "stairs"}
    assert composite.model_path == "yolo26s.pt + urbano.pt"


def test_every_mobility_label_is_spoken_in_portuguese():
    # Sem traducao, a narracao falaria "a street lights, a frente".
    missing = [label for label in MOBILITY_LABELS if label not in COCO_PT]
    assert missing == []
    # "ladder" e escada de mao; "escada" sozinha faria o usuario esperar degraus.
    assert label_pt("ladder") == "escada de mao"


def test_other_datasets_names_map_to_the_project_vocabulary():
    # O Objects365 chama poste de "street lights": modelo extra, dataset de
    # treino e avaliacao precisam falar do mesmo objeto com o mesmo nome.
    assert canonical_label("street lights") == "pole"
    assert canonical_label("trash bin/can") == "trash can"
    assert canonical_label("person") == "person"
    assert {"pole", "trash can"} <= MOBILITY_LABELS


def test_yolo_backend_fails_loudly_instead_of_faking(monkeypatch):
    import app.vision.yolo_detector as yolo_module

    def missing(_settings):
        raise ModuleNotFoundError("No module named 'ultralytics'")

    monkeypatch.setattr(yolo_module, "create_yolo_detector", missing)
    with pytest.raises(DetectorUnavailableError, match="ultralytics"):
        build_detector(VisionSettings(backend="yolo"))


def test_auto_backend_fallback_is_marked_as_simulated(monkeypatch):
    import app.vision.yolo_detector as yolo_module

    def missing(_settings):
        raise ModuleNotFoundError("No module named 'ultralytics'")

    monkeypatch.setattr(yolo_module, "create_yolo_detector", missing)
    detector = build_detector(VisionSettings(backend="auto"))
    assert detector.name == "fake"
    assert "ultralytics" in detector.fallback_reason


def test_simulated_frames_carry_a_red_banner():
    image = np.full((480, 640, 3), 128, dtype=np.uint8)
    plain = draw_tracks(image, [])
    marked = draw_tracks(image, [], simulated=True)
    assert tuple(plain[5, 5]) == (128, 128, 128)
    blue, green, red = (int(v) for v in marked[5, 5])
    assert red > 150 and blue < 80 and green < 80


def test_fake_detector_produces_coherent_stream():
    detector = FakeDetector(VisionSettings(backend="fake", confidence_threshold=0.3))
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    frames = [detector.detect(image) for _ in range(30)]
    assert any(frames), "o detector simulado deve produzir deteccoes"
    for detections in frames:
        for detection in detections:
            assert 0 <= detection.box.x1 <= 640
            assert detection.confidence >= 0.3
