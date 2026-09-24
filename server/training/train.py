"""Refinamento do modelo por Aprendizado por Transferencia (Secao 5.2 do TCC).

Parte de pesos pre-treinados no COCO e ajusta a rede para as classes do
projeto. As metricas monitoradas sao as descritas no trabalho -- funcao de
perda e mAP -- e ficam registradas em ``runs/`` para compor os resultados.

Atencao ao que o modelo resultante sabe: SO as classes do dataset. Treinar com
as 8 classes urbanas troca a cabeca de 80 saidas do COCO por uma de 8 -- o
modelo passa a achar degraus e esquece pessoas e carros (esquecimento
catastrofico). Por isso ele roda AO LADO do modelo COCO
(``AVS_VISION__EXTRA_MODEL_PATHS``), e nao no lugar dele. Um modelo unico so e
possivel se as imagens novas tambem forem rotuladas com as classes COCO que
aparecem nelas -- do contrario a rede aprende que pessoa e fundo.

Uso tipico:

    # 1. confira se o dataset esta no formato esperado
    python training/train.py --check-only

    # 2. treine (o tamanho do lote se ajusta a memoria da GPU com batch=-1)
    python training/train.py --epochs 100 --batch -1

    # 3. avalie no conjunto de teste
    python training/evaluate.py --weights runs/detect/train/weights/best.pt

Estrutura esperada do dataset (formato YOLO):

    datasets/urbano/
      data.yaml
      images/{train,val,test}/*.jpg
      labels/{train,val,test}/*.txt   # classe cx cy w h  (normalizados 0..1)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = ROOT / "datasets" / "urbano" / "data.yaml"
DEFAULT_WEIGHTS = ROOT / "models" / "yolo26s.pt"


def check_dataset(data_yaml: Path) -> bool:
    """Valida a estrutura antes de gastar horas de GPU com um caminho errado."""
    import yaml

    if not data_yaml.exists():
        print(f"[erro] data.yaml nao encontrado em {data_yaml}")
        print("       use training/prepare_dataset.py para criar o esqueleto.")
        return False

    spec = yaml.safe_load(data_yaml.read_text(encoding="utf-8"))
    base = Path(spec.get("path", data_yaml.parent))
    if not base.is_absolute():
        base = (data_yaml.parent / base).resolve()

    ok = True
    for split in ("train", "val"):
        relative = spec.get(split)
        if relative is None:
            print(f"[erro] a chave '{split}' esta ausente no data.yaml")
            ok = False
            continue
        images = (base / relative).resolve()
        labels = Path(str(images).replace("images", "labels"))
        count = len(list(images.glob("*.jpg"))) + len(list(images.glob("*.png")))
        label_count = len(list(labels.glob("*.txt"))) if labels.exists() else 0
        print(f"  {split:5s}  {count:5d} imagens  {label_count:5d} rotulos  ({images})")
        if count == 0:
            print(f"[erro] nenhuma imagem em {images}")
            ok = False
        elif label_count < count:
            # Imagens sem rotulo sao tratadas como plano de fundo pelo YOLO.
            # Um punhado ajuda a reduzir falsos positivos; muitos indicam
            # anotacao incompleta.
            print(f"[aviso] {count - label_count} imagens sem arquivo de rotulo")

    names = spec.get("names") or []
    print(f"  classes: {len(names)} -> {names}")
    return ok


def train(args: argparse.Namespace) -> int:
    from ultralytics import YOLO

    if not check_dataset(args.data):
        return 1
    if args.check_only:
        print("\n[ok] dataset valido.")
        return 0

    print(f"\n[treino] pesos iniciais: {args.weights}")
    model = YOLO(str(args.weights))

    model.train(
        data=str(args.data),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        workers=args.workers,
        patience=args.patience,
        # Congelar as primeiras camadas preserva os descritores visuais gerais
        # aprendidos no COCO e acelera a convergencia -- o ganho central do
        # Aprendizado por Transferencia quando o dataset proprio e pequeno.
        freeze=args.freeze,
        optimizer="auto",
        seed=args.seed,
        project=str(args.project),
        name=args.name,
        exist_ok=True,
        plots=True,
        # Aumento de dados voltado ao cenario de uso: variacao de iluminacao
        # (a limitacao relatada na Secao 6) e espelhamento horizontal.
        hsv_h=0.015,
        hsv_s=0.7,
        hsv_v=0.5,
        fliplr=0.5,
        flipud=0.0,
        degrees=5.0,
        translate=0.1,
        scale=0.4,
        mosaic=1.0,
    )

    metrics = model.val(data=str(args.data), imgsz=args.imgsz, device=args.device)
    print("\n=== metricas de validacao ===")
    print(f"  mAP@0.5      : {metrics.box.map50:.4f}")
    print(f"  mAP@0.5:0.95 : {metrics.box.map:.4f}")
    print(f"  precisao     : {metrics.box.mp:.4f}")
    print(f"  revocacao    : {metrics.box.mr:.4f}")
    weights = args.project / args.name / "weights" / "best.pt"
    print(f"\nPesos: {weights}")
    names = set(model.names.values())
    if "person" in names:
        print("O dataset inclui as classes do COCO: o modelo pode substituir o principal")
        print(f"  AVS_VISION__MODEL_PATH={weights.as_posix()}")
    else:
        # So as classes novas: substituir o principal apagaria pessoa, carro,
        # cadeira... O modelo entra AO LADO do COCO, acrescentando classes.
        print("O modelo conhece SO as classes do dataset -- nao substitua o principal.")
        print("Use-o ao lado do modelo COCO, que continua detectando pessoa, carro etc.:")
        print(f'  AVS_VISION__EXTRA_MODEL_PATHS=["{weights.as_posix()}"]')
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument(
        "--batch", type=int, default=-1, help="-1 ajusta o lote a memoria disponivel"
    )
    parser.add_argument("--device", default="0", help="'0' para a primeira GPU, 'cpu' para CPU")
    # No Windows cada processo auxiliar carrega as DLLs do CUDA inteiras; com 8
    # deles (e o servidor rodando na GPU) a memoria virtual acabou -- "arquivo de
    # paginacao muito pequeno" (WinError 1455). 2 mantem a GPU ocupada no yolo26n.
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--patience", type=int, default=25, help="parada antecipada")
    parser.add_argument("--freeze", type=int, default=10, help="camadas congeladas do backbone")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--project", type=Path, default=ROOT / "runs")
    parser.add_argument("--name", default="assistivo")
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(train(parse_args()))
