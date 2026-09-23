"""Quanto cada etapa do caminho da imagem custa em acuracia.

Roda o mesmo modelo sobre um conjunto rotulado no formato YOLO, variando uma
coisa por vez: realce CLAHE, imagem esticada (webcam 16:9 forcada em 4:3) e
pouca luz simulada. Mede revocacao e precisao com IoU >= 0,5 e mesma classe --
foram esses numeros que fixaram os padroes em ``app/settings.py``.

    python training/pipeline_ablation.py --data caminho/coco128 --weights ../models/yolo11s.pt
    python training/pipeline_ablation.py --data caminho/coco128 --conf 0.35 --only original

O COCO128 (7 MB) serve bem para comparar variantes entre si:
https://github.com/ultralytics/assets/releases/download/v0.0.0/coco128.zip
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.settings import PreprocessSettings  # noqa: E402
from app.vision.preprocessing import FramePreprocessor  # noqa: E402

Box = list[float]


def iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def darken(image: np.ndarray) -> np.ndarray:
    """Pouca luz: 35% da luminancia linear e ruido gaussiano de sensor barato."""
    linear = (image.astype(np.float32) / 255.0) ** 2.2 * 0.35
    linear += np.random.default_rng(0).normal(0.0, 0.02, linear.shape)
    return (np.clip(linear, 0.0, 1.0) ** (1 / 2.2) * 255).astype(np.uint8)


def squash_to_4x3(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    return cv2.resize(image, (width, int(round(width * 3 / 4))))


def build_variants() -> dict[str, Callable[[np.ndarray], np.ndarray]]:
    clahe = FramePreprocessor(PreprocessSettings(clahe=True))
    return {
        "original": lambda image: image,
        "clahe": clahe.apply,
        "esticada_4x3": squash_to_4x3,
        "escura": darken,
        "escura_clahe": lambda image: clahe.apply(darken(image)),
    }


def load_labels(label_path: Path, width: int, height: int) -> list[tuple[int, Box]]:
    boxes: list[tuple[int, Box]] = []
    if not label_path.exists():
        return boxes
    for line in label_path.read_text().splitlines():
        cls, cx, cy, bw, bh = (float(v) for v in line.split()[:5])
        boxes.append(
            (
                int(cls),
                [(cx - bw / 2) * width, (cy - bh / 2) * height,
                 (cx + bw / 2) * width, (cy + bh / 2) * height],
            )
        )
    return boxes


def evaluate(model, images: list[Path], transform, conf: float, imgsz: int) -> tuple[float, float]:
    true_pos = false_pos = total = 0
    for image_path in images:
        image = transform(cv2.imread(str(image_path)))
        height, width = image.shape[:2]
        label_path = Path(str(image_path).replace("images", "labels")).with_suffix(".txt")
        truth = load_labels(label_path, width, height)
        total += len(truth)

        result = model.predict(image, conf=conf, imgsz=imgsz, verbose=False)[0]
        used: set[int] = set()
        for cls, box in zip(result.boxes.cls.tolist(), result.boxes.xyxy.tolist(), strict=False):
            best, best_index = 0.5, -1
            for index, (truth_cls, truth_box) in enumerate(truth):
                if index in used or truth_cls != int(cls):
                    continue
                overlap = iou(box, truth_box)
                if overlap >= best:
                    best, best_index = overlap, index
            if best_index >= 0:
                used.add(best_index)
                true_pos += 1
            else:
                false_pos += 1
    return true_pos / max(1, total), true_pos / max(1, true_pos + false_pos)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, required=True, help="pasta com images/ e labels/")
    parser.add_argument("--weights", default="../models/yolo11s.pt")
    parser.add_argument("--conf", type=float, default=0.5)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--only", nargs="*", help="subconjunto de variantes")
    args = parser.parse_args()

    from ultralytics import YOLO

    suffixes = {".jpg", ".jpeg", ".png"}
    images = sorted(p for p in (args.data / "images").rglob("*") if p.suffix.lower() in suffixes)
    if not images:
        raise SystemExit(f"Nenhuma imagem em {args.data / 'images'}")
    model = YOLO(args.weights)
    variants = build_variants()
    selected = args.only or list(variants)

    print(f"{Path(args.weights).name}  conf={args.conf}  {len(images)} imagens")
    print(f"{'variante':16s} {'revocacao':>10s} {'precisao':>10s}")
    for name in selected:
        recall, precision = evaluate(model, images, variants[name], args.conf, args.imgsz)
        print(f"{name:16s} {recall:10.1%} {precision:10.1%}")


if __name__ == "__main__":
    main()
