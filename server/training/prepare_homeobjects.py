"""Dataset de interiores para um modelo extra: porta, janela, guarda-roupa, luminaria.

Parte do HomeObjects-3K (Ultralytics, AGPL-3.0; 2.285 imagens de treino e 404
de validacao, 12 classes) e mantem so as classes que o COCO nao tem. As demais
(cama, sofa, cadeira...) viram fundo de proposito: o modelo treinado aqui roda
AO LADO do modelo COCO, que ja cuida delas (ver training/train.py).

    python training/prepare_homeobjects.py
    python training/train.py --data ../datasets/interiores/data.yaml --weights ../models/yolo26s.pt
    # no servidor: AVS_VISION__EXTRA_MODEL_PATHS=["<pesos treinados>"]

Referencia medida: yolo26n, backbone congelado, so 6 epocas em CPU, chegou a
AP50 de 42% (porta), 49% (janela), 48% (guarda-roupa) e 47% (luminaria) na
validacao -- contra 47%, 5%, 5% e 19% do YOLOE sem treino, so com o nome da
classe em texto. E um treino curto de demonstracao; com GPU, use as 100 epocas
padrao do train.py.
"""

from __future__ import annotations

import argparse
import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
URL = "https://github.com/ultralytics/assets/releases/download/v0.0.0/homeobjects-3K.zip"
HOME_OBJECTS = [
    "bed", "sofa", "chair", "table", "lamp", "tv", "laptop",
    "wardrobe", "window", "door", "potted plant", "photo frame",
]
# O que o COCO nao tem e importa para quem anda num ambiente interno. "photo
# frame" tambem falta no COCO, mas nao ajuda ninguem a se orientar.
DEFAULT_CLASSES = ["door", "window", "wardrobe", "lamp"]


def fetch(cache: Path) -> Path:
    """Baixa e extrai o HomeObjects-3K uma vez; so imagens e rotulos."""
    source = cache / "homeobjects-3K"
    if (source / "images").exists():
        return source
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / "homeobjects-3K.zip"
    if not archive.exists():
        print(f"baixando {URL} (390 MB)...")
        urllib.request.urlretrieve(URL, archive)
    with zipfile.ZipFile(archive) as bundle:
        members = [m for m in bundle.namelist() if m.startswith(("images/", "labels/"))]
        bundle.extractall(source, members)
    # O zip fica fora do .gitignore (so images/ e labels/ estao la): apaga.
    archive.unlink()
    return source


def build(source: Path, target: Path, classes: list[str]) -> dict[str, tuple[int, int, int]]:
    """Copia as imagens e reescreve os rotulos so com as classes pedidas."""
    remap = {HOME_OBJECTS.index(name): index for index, name in enumerate(classes)}
    stats = {}
    for split in ("train", "val"):
        (target / "images" / split).mkdir(parents=True, exist_ok=True)
        (target / "labels" / split).mkdir(parents=True, exist_ok=True)
        images = objects = background = 0
        for image in sorted((source / "images" / split).glob("*.jpg")):
            shutil.copy2(image, target / "images" / split / image.name)
            label = source / "labels" / split / f"{image.stem}.txt"
            lines = []
            if label.exists():
                for line in label.read_text().splitlines():
                    parts = line.split()
                    if parts and int(parts[0]) in remap:
                        lines.append(" ".join([str(remap[int(parts[0])]), *parts[1:5]]))
            # Imagem sem nenhuma das classes fica como fundo: ensina o que NAO e porta.
            text = "\n".join(lines) + ("\n" if lines else "")
            (target / "labels" / split / f"{image.stem}.txt").write_text(text)
            images += 1
            objects += len(lines)
            background += not lines
        stats[split] = (images, objects, background)

    names = "".join(f"  {index}: {name}\n" for index, name in enumerate(classes))
    (target / "data.yaml").write_text(
        f"path: {target.resolve().as_posix()}\ntrain: images/train\nval: images/val\n\n"
        f"names:\n{names}",
        encoding="utf-8",
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--classes", nargs="+", default=DEFAULT_CLASSES, choices=HOME_OBJECTS)
    parser.add_argument("--target", type=Path, default=ROOT / "datasets" / "interiores")
    parser.add_argument("--cache", type=Path, default=ROOT / "datasets" / "cache")
    parser.add_argument("--source", type=Path, help="HomeObjects-3K ja extraido (pula o download)")
    args = parser.parse_args()

    source = args.source or fetch(args.cache)
    stats = build(source, args.target, args.classes)
    for split, (images, objects, background) in stats.items():
        print(f"  {split:5s} {images:5d} imagens  {objects:5d} objetos  {background:4d} so fundo")
    print(f"\n[ok] {args.target / 'data.yaml'}  classes: {args.classes}")


if __name__ == "__main__":
    main()
