"""Cria o esqueleto do dataset e divide as imagens em treino/validacao/teste.

As classes ja incluem os obstaculos urbanos apontados como trabalho futuro na
Secao 6 do TCC (degraus, buracos, postes, placas). Elas tambem estao mapeadas
em ``app/vision/labels.py``, entao assim que o modelo refinado reconhecer essas
classes a narracao em portugues funciona sem nenhuma alteracao de codigo.

    python training/prepare_dataset.py --init
    python training/prepare_dataset.py --split --source datasets/brutas
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "datasets" / "urbano"

# Classes proprias do projeto. As classes COCO uteis (pessoa, carro, cadeira...)
# continuam vindo do modelo COCO, que roda AO LADO do modelo treinado com este
# dataset (AVS_VISION__EXTRA_MODEL_PATHS) -- o modelo treinado aqui so conhece
# as classes abaixo e, sozinho, deixaria de ver pessoas (ver training/train.py).
CLASSES = [
    "step",            # degrau
    "stairs",          # escada
    "pothole",         # buraco
    "pole",            # poste
    "curb",            # meio-fio
    "tactile paving",  # piso tatil
    "door",            # porta
    "crosswalk",       # faixa de pedestres
]

DATA_YAML = """# Dataset do sistema assistivo -- formato YOLO.
#
# Rotulos: um .txt por imagem, com uma linha por objeto:
#   <indice_da_classe> <cx> <cy> <largura> <altura>
# todos normalizados entre 0 e 1, relativos ao tamanho da imagem.
#
# Ferramentas de anotacao compativeis: LabelImg, CVAT, Roboflow (exportar YOLO).

path: {path}
train: images/train
val: images/val
test: images/test

names:
{names}
"""


def init_structure(target: Path) -> None:
    for split in ("train", "val", "test"):
        (target / "images" / split).mkdir(parents=True, exist_ok=True)
        (target / "labels" / split).mkdir(parents=True, exist_ok=True)

    names = "\n".join(f"  {index}: {name}" for index, name in enumerate(CLASSES))
    (target / "data.yaml").write_text(
        DATA_YAML.format(path=target.as_posix(), names=names), encoding="utf-8"
    )
    (target / "README.md").write_text(
        "# Dataset\n\n"
        "Coloque as imagens em `images/train|val|test` e os rotulos YOLO\n"
        "correspondentes em `labels/...` com o mesmo nome de arquivo.\n\n"
        "Para dividir automaticamente um diretorio ja anotado:\n\n"
        "    python training/prepare_dataset.py --split --source <pasta>\n",
        encoding="utf-8",
    )
    print(f"[ok] estrutura criada em {target}")
    print(f"     {len(CLASSES)} classes: {', '.join(CLASSES)}")


def split_dataset(source: Path, target: Path, ratios: tuple[float, float, float], seed: int) -> int:
    """Divide um diretorio plano (imagens + .txt lado a lado) nos tres conjuntos."""
    images = sorted(
        path
        for path in source.iterdir()
        if path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    if not images:
        print(f"[erro] nenhuma imagem em {source}")
        return 1

    random.Random(seed).shuffle(images)
    total = len(images)
    train_end = int(total * ratios[0])
    val_end = train_end + int(total * ratios[1])
    buckets = {
        "train": images[:train_end],
        "val": images[train_end:val_end],
        "test": images[val_end:],
    }

    init_structure(target)
    missing_labels = 0
    for split, files in buckets.items():
        for image in files:
            shutil.copy2(image, target / "images" / split / image.name)
            label = image.with_suffix(".txt")
            if label.exists():
                shutil.copy2(label, target / "labels" / split / label.name)
            else:
                missing_labels += 1
        print(f"  {split:5s}: {len(files)} imagens")

    if missing_labels:
        print(f"[aviso] {missing_labels} imagens sem rotulo (serao tratadas como fundo)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--init", action="store_true", help="cria apenas a estrutura vazia")
    parser.add_argument("--split", action="store_true", help="divide um diretorio anotado")
    parser.add_argument("--source", type=Path, help="diretorio com imagens e rotulos")
    parser.add_argument("--target", type=Path, default=DATASET_DIR)
    parser.add_argument("--train-ratio", type=float, default=0.70)
    parser.add_argument("--val-ratio", type=float, default=0.20)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    if args.split:
        if args.source is None or not args.source.is_dir():
            parser.error("--split exige --source apontando para um diretorio existente")
        ratios = (args.train_ratio, args.val_ratio, 1.0 - args.train_ratio - args.val_ratio)
        return split_dataset(args.source, args.target, ratios, args.seed)

    init_structure(args.target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
