"""Acuracia do sistema como implantado, e quanto cada condicao da imagem custa.

Cada imagem rotulada percorre o MESMO caminho de um quadro real:

    borda:    reduz para caber em 640x480 + JPEG (o que o firmware/simulador envia)
    servidor: decode -> resize_keep_aspect -> pre-processamento -> detector

O detector e o do servidor (``create_yolo_detector``), entao valem o filtro de
classes (``allowed_labels``), o limiar de confianca e o modelo escolhido.
Cada deteccao no ponto de operacao e classificada:

    acerto     IoU >= 0,5 com objeto real da mesma classe
    fantasma   nenhum objeto real embaixo (IoU < 0,1)  -- "detectou o que nao existe"
    rotulo     sobre objeto real de OUTRA classe         -- "achou, mas com o nome errado"
    duplicata  sobre objeto real ja contado
    mal_loc    caixa deslocada demais (0,1 <= IoU < 0,5)

Variantes simulam as condicoes do prototipo: pouca luz com ruido de sensor,
borrao de quem anda com a camera no peito, QVGA (a placa sem PSRAM) e o realce
CLAHE. Use um conjunto que o modelo NAO viu no treino -- o COCO128 e parte do
treino do COCO e infla os numeros. Como montar 500 imagens do COCO val2017 esta
em docs/testes.md.

    python training/pipeline_ablation.py --data caminho/val --weights yolo26s.pt
    python training/pipeline_ablation.py --data caminho/val --conf 0.35 --only original escuro
    python training/pipeline_ablation.py --data datasets/ensaio --all-classes --json r.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.settings import PreprocessSettings, VisionSettings  # noqa: E402
from app.vision.preprocessing import FramePreprocessor, resize_keep_aspect  # noqa: E402

KINDS = ("acerto", "fantasma", "rotulo", "duplicata", "mal_loc")


# ------------------------------------------------------------------ condicoes


def darken(image: np.ndarray) -> np.ndarray:
    """Pouca luz: 30% da luminancia linear e ruido de sensor barato."""
    linear = (image.astype(np.float32) / 255.0) ** 2.2 * 0.30
    linear += np.random.default_rng(0).normal(0.0, 0.025, linear.shape)
    return (np.clip(linear, 0.0, 1.0) ** (1 / 2.2) * 255).astype(np.uint8)


def motion_blur(image: np.ndarray, length: int = 11) -> np.ndarray:
    """Borrao horizontal de ~11 px: passada com a camera presa ao peito."""
    kernel = np.zeros((length, length), np.float32)
    kernel[length // 2, :] = 1.0 / length
    return cv2.filter2D(image, -1, kernel)


def qvga(image: np.ndarray) -> np.ndarray:
    """320x240: o que a ESP32-CAM envia quando nao encontra a PSRAM."""
    height, width = image.shape[:2]
    scale = min(320 / width, 240 / height, 1.0)
    size = (round(width * scale), round(height * scale))
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)


VARIANTS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "original": lambda image: image,
    "escuro": darken,
    "borrao": motion_blur,
    "qvga": qvga,
    # CLAHE e aplicado no servidor (pre-processamento), nao na imagem de origem.
    "clahe": lambda image: image,
}


def device_jpeg(image: np.ndarray, quality: int) -> bytes:
    """O quadro como a borda o envia: cabe em 640x480, proporcao mantida."""
    height, width = image.shape[:2]
    scale = min(640 / width, 480 / height, 1.0)
    if scale != 1.0:
        size = (round(width * scale), round(height * scale))
        image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("falha ao codificar JPEG")
    return buffer.tobytes()


# ------------------------------------------------------------------ avaliacao


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    ix = np.minimum(a[:, None, 2], b[None, :, 2]) - np.maximum(a[:, None, 0], b[None, :, 0])
    iy = np.minimum(a[:, None, 3], b[None, :, 3]) - np.maximum(a[:, None, 1], b[None, :, 1])
    inter = np.clip(ix, 0, None) * np.clip(iy, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / (area_a[:, None] + area_b[None, :] - inter + 1e-9)


def load_labels(path: Path, names: dict[int, str], width: int, height: int):
    labels, boxes = [], []
    if path.exists():
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            cls, cx, cy, bw, bh = int(parts[0]), *map(float, parts[1:5])
            labels.append(names.get(cls, str(cls)))
            boxes.append([(cx - bw / 2) * width, (cy - bh / 2) * height,
                          (cx + bw / 2) * width, (cy + bh / 2) * height])
    return labels, np.array(boxes, dtype=float).reshape(-1, 4)


def classify(detections, truth_labels, truth_boxes):
    """Casa por confianca decrescente e rotula cada deteccao com o tipo de erro."""
    boxes = np.array([[d.box.x1, d.box.y1, d.box.x2, d.box.y2] for d in detections]).reshape(-1, 4)
    ious = iou_matrix(boxes, truth_boxes)
    matched = np.zeros(len(truth_labels), bool)
    kinds, confusions = [""] * len(detections), []
    for i in sorted(range(len(detections)), key=lambda k: -detections[k].confidence):
        label = detections[i].label
        same = [j for j, t in enumerate(truth_labels) if t == label and not matched[j]]
        if same and ious[i, same].max() >= 0.5:
            matched[same[int(ious[i, same].argmax())]] = True
            kinds[i] = "acerto"
        elif len(truth_labels) and ious[i].max() >= 0.5:
            best = int(ious[i].argmax())
            if truth_labels[best] != label and not matched[best]:
                kinds[i] = "rotulo"
                confusions.append(f"{truth_labels[best]} -> {label}")
            else:
                kinds[i] = "duplicata"
        elif not len(truth_labels) or ious[i].max() < 0.1:
            kinds[i] = "fantasma"
        else:
            kinds[i] = "mal_loc"
    return kinds, matched, confusions


def run_variant(name, images, detector, settings, names, args, counted_labels):
    preprocess = PreprocessSettings(clahe=name == "clahe")
    preprocessor = FramePreprocessor(preprocess)
    transform = VARIANTS[name]
    counts, per_class, confusions = Counter(), defaultdict(Counter), Counter()
    truth_total, found_total, times = 0, 0, []

    for path in images:
        original = cv2.imread(str(path))
        if args.as_is:
            # Quadro que ja veio da placa: recomprimir seria degradar duas vezes.
            image = transform(original)
        else:
            image = preprocessor.decode(device_jpeg(transform(original), args.quality))
        height, width = image.shape[:2]
        label_path = Path(str(path).replace("images", "labels")).with_suffix(".txt")
        truth_labels, truth_boxes = load_labels(label_path, names, width, height)

        scaled, scale = resize_keep_aspect(image, settings.inference_size)
        started = time.perf_counter()
        detections = detector.detect(preprocessor.apply(scaled))
        times.append((time.perf_counter() - started) * 1000.0)
        if scale != 1.0:
            from dataclasses import replace

            detections = [replace(d, box=d.box.scaled(1 / scale, 1 / scale)) for d in detections]

        kinds, matched, pairs = classify(detections, truth_labels, truth_boxes)
        counts.update(kinds)
        confusions.update(pairs)
        for detection, kind in zip(detections, kinds, strict=True):
            per_class[detection.label][kind] += 1
        # A revocacao so conta objetos que o sistema pode reportar: com o
        # perfil de mobilidade, uma girafa nao detectada nao e erro.
        for label, hit in zip(truth_labels, matched, strict=True):
            if counted_labels is None or label in counted_labels:
                truth_total += 1
                found_total += int(hit)
                per_class[label]["reais"] += 1
                per_class[label]["encontrados"] += int(hit)

    total = sum(counts[k] for k in KINDS)
    precision = counts["acerto"] / max(1, total)
    recall = found_total / max(1, truth_total)
    return {
        "precisao": round(precision, 4),
        "revocacao": round(recall, 4),
        "f1": round(2 * precision * recall / max(1e-9, precision + recall), 4),
        "deteccoes": total,
        "objetos_reais": truth_total,
        "erros": {k: counts[k] for k in KINDS[1:]},
        "ms_mediana": round(float(np.median(times)), 1),
        "confusoes": confusions.most_common(15),
        "por_classe": {k: dict(v) for k, v in sorted(per_class.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, required=True, help="pasta com images/ e labels/")
    parser.add_argument("--weights", default="yolo26s.pt", help="pesos (procurados em models/)")
    parser.add_argument("--conf", type=float, help="limiar; padrao = o do servidor")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--quality", type=int, default=88, help="qualidade JPEG da borda")
    parser.add_argument("--only", nargs="*", choices=list(VARIANTS), help="variantes a rodar")
    parser.add_argument("--all-classes", action="store_true", help="desliga o perfil de mobilidade")
    parser.add_argument("--names", type=Path, help="data.yaml com os nomes; padrao = do modelo")
    parser.add_argument("--extra", nargs="*", default=[],
                        help="modelos extras, como no servidor (ex.: yolo26l-objv1-150.pt)")
    parser.add_argument("--split", help="so images/<split> (ex.: test, o teste da ESP)")
    parser.add_argument("--as-is", action="store_true",
                        help="imagens ja sao quadros da placa: nao recomprime o JPEG")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--json", type=Path, help="grava o resultado completo")
    args = parser.parse_args()

    from app.vision.yolo_detector import create_yolo_detector

    overrides = {"backend": "yolo", "model_path": args.weights, "device": args.device,
                 "extra_model_paths": args.extra}
    if args.conf is not None:
        overrides["confidence_threshold"] = args.conf
    if args.all_classes:
        overrides["allowed_labels"] = []
    settings = VisionSettings(**overrides)
    detector = create_yolo_detector(settings)
    if not args.names and not hasattr(detector, "class_names"):
        raise SystemExit("Com --extra, informe --names (o data.yaml do conjunto de teste).")

    if args.names:
        import yaml

        spec = yaml.safe_load(args.names.read_text(encoding="utf-8"))["names"]
        if isinstance(spec, list):
            names = dict(enumerate(spec))
        else:
            names = {int(k): v for k, v in spec.items()}
    else:
        names = detector.class_names
    counted = None if args.all_classes else set(settings.allowed_labels)

    suffixes = {".jpg", ".jpeg", ".png"}
    folder = args.data / "images" / (args.split or "")
    images = sorted(p for p in folder.rglob("*") if p.suffix.lower() in suffixes)
    if args.limit:
        images = images[: args.limit]
    if not images:
        raise SystemExit(f"Nenhuma imagem em {folder}")

    profile = "todas" if args.all_classes else "perfil de mobilidade"
    print(f"{Path(detector.model_path).name}  conf={settings.confidence_threshold}  "
          f"{len(images)} imagens  classes={profile}")
    print(f"{'variante':10s} {'precisao':>9s} {'revocacao':>9s} {'F1':>6s} | "
          f"{'fantasma':>8s} {'rotulo':>6s} {'duplic':>6s} {'mal_loc':>7s} | {'ms':>5s}")
    results = {}
    for name in args.only or list(VARIANTS):
        r = run_variant(name, images, detector, settings, names, args, counted)
        results[name] = r
        e = r["erros"]
        print(f"{name:10s} {r['precisao']:9.1%} {r['revocacao']:9.1%} {r['f1']:6.3f} | "
              f"{e['fantasma']:8d} {e['rotulo']:6d} {e['duplicata']:6d} {e['mal_loc']:7d} | "
              f"{r['ms_mediana']:5.0f}", flush=True)

    first = results[next(iter(results))]
    if first["confusoes"]:
        print("\nconfusoes mais comuns (real -> detectado):")
        for pair, count in first["confusoes"][:10]:
            print(f"  {pair}: {count}")
    if args.json:
        args.json.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
