"""Analise do log de eventos gerado durante os ensaios.

Le ``server/var/events.jsonl`` e resume o que aconteceu na sessao: latencias,
taxa de quadros, deteccoes por classe, alertas de proximidade e comandos de voz
reconhecidos. E dessa saida que saem os numeros da secao de resultados.

    python training/analyze_events.py
    python training/analyze_events.py --csv relatorio.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG = ROOT / "server" / "var" / "events.jsonl"


def load_events(path: Path) -> list[dict]:
    if not path.exists():
        print(f"[erro] log nao encontrado: {path}")
        print("       rode o servidor com AVS_STORAGE__LOG_EVENTS=true e refaca o ensaio.")
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # linha truncada por encerramento abrupto
    return events


def summarize(events: list[dict]) -> dict:
    inference, end_to_end, fps = [], [], []
    labels = Counter()
    zones = Counter()
    intents = Counter()
    speeches = 0
    detection_frames = 0

    for event in events:
        payload = event.get("payload", {})
        kind = event.get("type")

        if kind == "vision.detections":
            detection_frames += 1
            if payload.get("inference_ms"):
                inference.append(payload["inference_ms"])
            if payload.get("end_to_end_ms"):
                end_to_end.append(payload["end_to_end_ms"])
            if payload.get("fps"):
                fps.append(payload["fps"])
            for detection in payload.get("detections", []):
                labels[detection.get("label_pt") or detection.get("label")] += 1
        elif kind == "proximity.zone_changed":
            zones[payload.get("to", "?")] += 1
        elif kind == "speech.command_received":
            intents[payload.get("intent", "?")] += 1
        elif kind == "audio.speech_played":
            speeches += 1

    return {
        "eventos": len(events),
        "quadros_processados": detection_frames,
        "deteccoes": sum(labels.values()),
        "locucoes": speeches,
        "inferencia": _stats(inference),
        "fim_a_fim": _stats(end_to_end),
        "fps": _stats(fps),
        "classes": labels,
        "zonas": zones,
        "intencoes": intents,
    }


def _stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    return {
        "media": statistics.fmean(ordered),
        "mediana": statistics.median(ordered),
        "p95": ordered[max(0, int(len(ordered) * 0.95) - 1)],
        "min": ordered[0],
        "max": ordered[-1],
    }


def print_report(summary: dict) -> None:
    print("=== resumo da sessao ===")
    print(f"  eventos registrados : {summary['eventos']}")
    print(f"  quadros processados : {summary['quadros_processados']}")
    print(f"  deteccoes totais    : {summary['deteccoes']}")
    print(f"  locucoes emitidas   : {summary['locucoes']}")

    for title, key in (
        ("tempo de inferencia (ms)", "inferencia"),
        ("latencia fim a fim (ms)", "fim_a_fim"),
        ("quadros por segundo", "fps"),
    ):
        stats = summary[key]
        if not stats:
            continue
        print(f"\n=== {title} ===")
        for name, value in stats.items():
            print(f"  {name:<8}: {value:8.2f}")

    if summary["classes"]:
        print("\n=== deteccoes por classe ===")
        for label, count in summary["classes"].most_common(15):
            print(f"  {label:<22} {count:6d}")

    if summary["zonas"]:
        print("\n=== transicoes de zona ===")
        for zone, count in summary["zonas"].most_common():
            print(f"  {zone:<12} {count:6d}")

    if summary["intencoes"]:
        print("\n=== comandos reconhecidos ===")
        for intent, count in summary["intencoes"].most_common():
            print(f"  {intent:<20} {count:6d}")


def write_csv(summary: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metrica", "valor"])
        for key in ("eventos", "quadros_processados", "deteccoes", "locucoes"):
            writer.writerow([key, summary[key]])
        for group in ("inferencia", "fim_a_fim", "fps"):
            for name, value in summary[group].items():
                writer.writerow([f"{group}_{name}", round(value, 3)])
        writer.writerow([])
        writer.writerow(["classe", "deteccoes"])
        for label, count in summary["classes"].most_common():
            writer.writerow([label, count])
    print(f"\n[csv] {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--csv", type=Path, help="grava o resumo tambem em CSV")
    args = parser.parse_args(argv)

    events = load_events(args.log)
    if not events:
        return 1

    summary = summarize(events)
    print_report(summary)
    if args.csv:
        write_csv(summary, args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
