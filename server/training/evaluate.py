"""Avaliacao do modelo treinado.

Produz as metricas quantitativas que a metodologia do TCC exige: mAP, precisao,
revocacao por classe e tempo de inferencia. O resultado sai em tabela pronta
para transcricao e tambem em CSV.

    python training/evaluate.py --weights runs/assistivo/weights/best.pt
    python training/evaluate.py --weights models/yolo26s.pt --benchmark-only
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def benchmark(weights: Path, imgsz: int, device: str, runs: int) -> dict[str, float]:
    """Mede o tempo de inferencia -- o dado que sustenta a alegacao de tempo real."""
    import numpy as np
    from ultralytics import YOLO

    model = YOLO(str(weights))
    image = np.random.randint(0, 255, (imgsz, imgsz, 3), dtype=np.uint8)

    # Descarta as primeiras execucoes: incluem alocacao de memoria e compilacao
    # de kernels, que nao representam o regime permanente.
    for _ in range(5):
        model.predict(image, imgsz=imgsz, device=device, verbose=False)

    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        model.predict(image, imgsz=imgsz, device=device, verbose=False)
        samples.append((time.perf_counter() - started) * 1000.0)

    samples.sort()
    return {
        "media_ms": statistics.fmean(samples),
        "mediana_ms": statistics.median(samples),
        "p95_ms": samples[int(len(samples) * 0.95) - 1],
        "min_ms": samples[0],
        "max_ms": samples[-1],
        "fps_estimado": 1000.0 / statistics.fmean(samples),
    }


def evaluate(args: argparse.Namespace) -> int:
    if not args.weights.exists():
        print(f"[erro] pesos nao encontrados: {args.weights}")
        return 1

    print(f"[avaliacao] pesos: {args.weights}  dispositivo: {args.device}\n")

    if not args.benchmark_only:
        from ultralytics import YOLO

        if not args.data.exists():
            print(f"[erro] data.yaml nao encontrado: {args.data}")
            print("       use --benchmark-only para medir apenas o tempo de inferencia.")
            return 1

        model = YOLO(str(args.weights))
        metrics = model.val(
            data=str(args.data), imgsz=args.imgsz, device=args.device, split=args.split
        )

        print("=== metricas globais ===")
        print(f"  mAP@0.5      : {metrics.box.map50:.4f}")
        print(f"  mAP@0.5:0.95 : {metrics.box.map:.4f}")
        print(f"  precisao     : {metrics.box.mp:.4f}")
        print(f"  revocacao    : {metrics.box.mr:.4f}\n")

        print("=== por classe ===")
        print(f"  {'classe':<20} {'precisao':>9} {'revocacao':>10} {'mAP@0.5':>9}")
        rows = []
        for index, name in enumerate(metrics.names.values()):
            try:
                precision, recall, ap50, _ = metrics.box.class_result(index)
            except (IndexError, ValueError):
                continue
            print(f"  {name:<20} {precision:>9.3f} {recall:>10.3f} {ap50:>9.3f}")
            rows.append({"classe": name, "precisao": precision, "revocacao": recall, "map50": ap50})

        if rows:
            output = args.output or ROOT / "runs" / "metricas_por_classe.csv"
            output.parent.mkdir(parents=True, exist_ok=True)
            with open(output, "w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            print(f"\n[csv] {output}")

    print("\n=== tempo de inferencia ===")
    results = benchmark(args.weights, args.imgsz, args.device, args.runs)
    for key, value in results.items():
        print(f"  {key:<14}: {value:8.2f}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weights", type=Path, default=ROOT / "models" / "yolo26s.pt")
    parser.add_argument("--data", type=Path, default=ROOT / "datasets" / "urbano" / "data.yaml")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--split", default="test", choices=("val", "test"))
    parser.add_argument("--runs", type=int, default=50)
    parser.add_argument("--benchmark-only", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(evaluate(parse_args()))
