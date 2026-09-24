"""Montagem do dataset de treino (training/build_dataset.py), sem rede nem GPU."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

_PATH = Path(__file__).resolve().parents[1] / "training" / "build_dataset.py"
_spec = importlib.util.spec_from_file_location("build_dataset", _PATH)
build_dataset = importlib.util.module_from_spec(_spec)
sys.modules["build_dataset"] = build_dataset  # dataclasses procuram o modulo aqui
_spec.loader.exec_module(build_dataset)


def _frame(folder: Path, ms: int, seq: int, device: str = "sim-01") -> Path:
    path = folder / f"{device}-{ms}-{seq}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.full((48, 64, 3), 128, np.uint8))
    return path


def test_a_pause_of_more_than_the_gap_opens_a_new_session(tmp_path):
    start = 1_790_190_000_000
    first = [_frame(tmp_path, start + i * 1000, i) for i in range(3)]
    second = [_frame(tmp_path, start + 30 * 60_000 + i * 1000, 10 + i) for i in range(3)]
    sessions = build_dataset.esp_sessions(first + second, gap_min=10)
    assert len({sessions[p] for p in first}) == 1
    assert len({sessions[p] for p in second}) == 1
    assert sessions[first[0]] != sessions[second[0]]


def test_esp_test_split_never_mixes_frames_of_one_session(tmp_path):
    start = 1_790_190_000_000
    for session in range(4):
        for i in range(5):
            _frame(tmp_path / "images", start + session * 3_600_000 + i * 1000, session * 10 + i)
    (tmp_path / "classes.txt").write_text("person\n")
    samples = build_dataset.source_esp(tmp_path, test_fraction=0.25, gap_min=10)
    split_by_session: dict[str, set[str]] = {}
    for sample in samples:
        split_by_session.setdefault(sample.session, set()).add(sample.split)
    # Cada sessao inteira de um lado so; uma das quatro no teste.
    assert all(len(splits) == 1 for splits in split_by_session.values())
    assert sum(splits == {"test"} for splits in split_by_session.values()) == 1


def test_explicit_test_folder_wins_over_automatic_split(tmp_path):
    _frame(tmp_path / "images" / "train", 1_790_190_000_000, 1)
    _frame(tmp_path / "images" / "test", 1_790_190_001_000, 2)
    (tmp_path / "classes.txt").write_text("person\n")
    splits = {s.key: s.split for s in build_dataset.source_esp(tmp_path, 0.25, 10)}
    assert splits == {"sim-01-1790190000000-1": "train", "sim-01-1790190001000-2": "test"}


@pytest.mark.parametrize("layout", ["beside", "mirrored"])
def test_labels_are_found_in_cvat_and_ultralytics_exports(tmp_path, layout):
    if layout == "beside":  # CVAT YOLO 1.1: obj_train_data/x.jpg + x.txt
        image = _frame(tmp_path / "obj_train_data", 1_790_190_000_000, 1)
        label = image.with_suffix(".txt")
    else:  # Ultralytics: images/train/x.jpg + labels/train/x.txt
        image = _frame(tmp_path / "images" / "train", 1_790_190_000_000, 1)
        label = tmp_path / "labels" / "train" / f"{image.stem}.txt"
    label.parent.mkdir(parents=True, exist_ok=True)
    label.write_text("0 0.5 0.5 0.2 0.2\n")
    assert build_dataset.find_label(tmp_path, image) == label


def test_esp_labels_are_read_by_class_name(tmp_path):
    # A equipe pode reordenar as classes no CVAT: o que vale e o nome.
    image = _frame(tmp_path / "obj_train_data", 1_790_190_000_000, 1)
    image.with_suffix(".txt").write_text("1 0.5 0.5 0.2 0.2\n")
    (tmp_path / "obj.names").write_text("chair\nstreet lights\n")
    sample = build_dataset.source_esp(tmp_path, 0.25, 10)[0]
    assert [row[0] for row in sample.labels] == ["pole"]  # nome canonico do projeto


def test_degraded_image_keeps_its_size():
    import random

    image = np.random.default_rng(0).integers(0, 255, (120, 160, 3), dtype=np.uint8)
    rng = random.Random(1)
    for _ in range(20):
        degraded, kind = build_dataset.degrade(image, rng)
        assert degraded.shape == image.shape and degraded.dtype == np.uint8
        assert kind in {"escuro", "borrao", "jpeg", "baixa_res", "cor"}


def test_vocabulary_is_spoken_in_portuguese():
    from app.vision.labels import COCO_PT, MOBILITY_LABELS

    assert set(build_dataset.VOCAB) <= set(COCO_PT)
    assert set(build_dataset.VOCAB) <= MOBILITY_LABELS
