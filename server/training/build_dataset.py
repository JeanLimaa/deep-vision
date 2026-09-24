"""Dataset do modelo final: COCO + classes novas + imagens da propria ESP32-CAM.

Dois comandos:

    # depois de um ensaio: pre-rotula os quadros crus para a equipe so corrigir
    python training/build_dataset.py prelabel --images var/raw --out ../datasets/esp_pre

    # no Colab (training/colab_treino.ipynb): junta todas as fontes
    python training/build_dataset.py build --stage professor --out /content/professor
    python training/build_dataset.py build --esp /content/esp --out /content/assistivo \\
        --teachers yolo26l.pt yolo26l-objv1-150.pt /content/professor.pt

Por que um modelo so, treinado num dataset juntado, e nao varios modelos lado a
lado: medido no mesmo teste, o modelo COCO e o melhor nas classes essenciais
(70% de AP50, contra 61% do Objects365), mas nunca viu a camera da ESP -- pouca
luz e borrao de movimento tiram ~20 pontos de revocacao. O ajuste fino a partir
dos pesos do COCO, com imagens da propria camera, melhora justamente as classes
essenciais nessa camera, e ensina as classes que faltam.

O problema de juntar datasets: cada fonte rotulou so as proprias classes. Uma
porta que aparece sem rotulo numa foto do dataset de degraus ensina a rede que
porta e fundo. Por isso cada imagem recebe rotulos automaticos ("pseudo-
rotulos") dos professores para as classes que a fonte nao rotulou por completo:

    1o professor (yolo26l.pt, COCO)       -> classes do COCO
    demais (Objects365, professor novo)   -> classes novas (poste, lixeira, porta...)

Rotulo humano sempre fica; o professor so acrescenta objetos de classes que a
fonte nao cobre por completo, e nunca em cima de um rotulo humano da mesma
classe (IoU >= 0,5). Tudo fica registrado em manifest.csv e SOURCES.md.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import shutil
import sys
import urllib.request
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))

from app.vision.labels import canonical_label  # noqa: E402

# --------------------------------------------------------------- vocabulario

# Classes do modelo final. Nomes do COCO onde o COCO ja tem a classe: o modelo
# refinado substitui o principal sem mudar a narracao (ver app/vision/labels.py).
COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "bus", "truck", "dog",
    "chair", "couch", "dining table", "bench", "potted plant", "fire hydrant",
    "traffic light", "stop sign",
    "backpack", "suitcase", "cell phone", "bottle", "cup",
]
# O que nenhum modelo pronto faz bem e que importa para quem caminha.
NEW_CLASSES = [
    "stairs", "step", "curb", "door", "crosswalk",
    "pole", "trash can", "traffic cone", "traffic sign",
]
VOCAB = COCO_CLASSES + NEW_CLASSES

COCO80 = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich",
    "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
    "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book",
    "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
]

ASSETS = "https://github.com/ultralytics/assets/releases/download/v0.0.0"
URLS = {
    "coco_labels": f"{ASSETS}/coco2017labels.zip",
    "coco_image": "http://images.cocodataset.org/train2017/{stem}.jpg",
    "barriers": "https://zenodo.org/api/records/6382090/files/wm_barriers_data.zip/content",
    "homeobjects": f"{ASSETS}/homeobjects-3K.zip",
    "doordetect": "https://codeload.github.com/MiguelARD/DoorDetect-Dataset/zip/refs/heads/master",
    "rod": "https://huggingface.co/datasets/ty-li/Obstacle-Detection-Dataset-YOLO/resolve/main/{path}",
}

LICENSES = [
    ("COCO train2017 (cocodataset.org)", "classes do COCO",
     "CC BY 4.0 (anotacoes); imagens do Flickr, termos do COCO"),
    ("Image Dataset of Accessibility Barriers (Zenodo 6382090)", "degrau, meio-fio, escada",
     "CC BY 4.0"),
    ("HomeObjects-3K (Ultralytics)", "porta, cadeira, sofa, mesa, vaso de planta", "AGPL-3.0"),
    ("DoorDetect (github.com/MiguelARD/DoorDetect-Dataset)", "porta",
     "**sem licenca declarada**: uso academico com citacao; confirme com o orientador"),
    ("ROD (huggingface.co/datasets/ty-li/Obstacle-Detection-Dataset-YOLO)",
     "poste, lixeira, cone, placa, faixa, escada",
     "MIT; as fontes do Roboflow mantem as proprias licencas"),
    ("Imagens da ESP32-CAM do projeto", "todas as classes, rotuladas pela equipe",
     "da equipe; rostos de terceiros exigem cuidado com a LGPD"),
]

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def default_device() -> str:
    try:
        import torch

        return "0" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def vocab_index(names: list[str]) -> dict[str, int]:
    return {name: index for index, name in enumerate(names)}


def list_images(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)


# ----------------------------------------------------------------- amostras

Label = tuple[str, float, float, float, float]  # (classe, cx, cy, w, h) normalizados


@dataclass
class Sample:
    """Uma imagem de uma fonte, com os rotulos humanos ja no vocabulario."""

    source: str
    key: str  # nome unico dentro da fonte
    load: Callable[[], np.ndarray | None]
    labels: list[Label] = field(default_factory=list)
    # Classes que a fonte rotulou por completo nesta imagem: sem pseudo-rotulo nelas.
    complete: frozenset[str] = frozenset()
    split: str | None = None  # None = decide pelo hash (treino/validacao)
    session: str = ""


def from_file(path: Path) -> Callable[[], np.ndarray | None]:
    return lambda: cv2.imread(str(path), cv2.IMREAD_COLOR)


def download(url: str, target: Path, desc: str = "") -> Path:
    if target.exists() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"  baixando {desc or target.name} ...", flush=True)
    partial = target.with_suffix(target.suffix + ".part")
    urllib.request.urlretrieve(url, partial)
    partial.replace(target)
    return target


def download_many(jobs: list[tuple[str, Path]], threads: int = 16) -> int:
    def one(job: tuple[str, Path]) -> bool:
        url, target = job
        if target.exists() and target.stat().st_size > 0:
            return True
        target.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            try:
                urllib.request.urlretrieve(url, target)
                return True
            except Exception:  # noqa: BLE001 - rede instavel: tenta de novo
                continue
        return False

    with ThreadPoolExecutor(threads) as pool:
        return sum(pool.map(one, jobs))


def yolo_lines(text: str) -> Iterator[tuple[int, float, float, float, float]]:
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5:
            yield int(float(parts[0])), *(float(v) for v in parts[1:5])


# ------------------------------------------------------------------- fontes


def source_coco(cache: Path, limit: int, seed: int, per_class: int = 250) -> list[Sample]:
    """COCO train2017, balanceado: cada classe do vocabulario com >= per_class imagens."""
    archive = download(URLS["coco_labels"], cache / "coco2017labels.zip", "rotulos do COCO")
    wanted = {COCO80.index(name): name for name in COCO_CLASSES}
    per_image: dict[str, list[Label]] = {}
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.namelist():
            if "labels/train2017/" not in member or not member.endswith(".txt"):
                continue
            rows = [
                (wanted[c], cx, cy, w, h)
                for c, cx, cy, w, h in yolo_lines(bundle.read(member).decode())
                if c in wanted
            ]
            if rows:
                per_image[Path(member).stem] = rows
    stems = sorted(per_image)
    random.Random(seed).shuffle(stems)
    by_class: dict[str, list[str]] = defaultdict(list)
    for stem in stems:
        for name in {row[0] for row in per_image[stem]}:
            by_class[name].append(stem)
    chosen: list[str] = []
    seen: set[str] = set()
    covered: Counter = Counter()
    # Classes raras primeiro, para nao sumirem no meio de 60 mil pessoas.
    for name in sorted(by_class, key=lambda n: len(by_class[n])):
        for stem in by_class[name]:
            if covered[name] >= per_class or len(chosen) >= limit:
                break
            if stem not in seen:
                chosen.append(stem)
                seen.add(stem)
                covered.update({row[0] for row in per_image[stem]})
    for stem in stems:
        if len(chosen) >= limit:
            break
        if stem not in seen:
            chosen.append(stem)
            seen.add(stem)

    folder = cache / "coco_images"
    jobs = [(URLS["coco_image"].format(stem=s), folder / f"{s}.jpg") for s in chosen]
    print(f"  coco: {download_many(jobs)}/{len(chosen)} imagens")
    complete = frozenset(COCO_CLASSES)
    return [
        Sample("coco", stem, from_file(folder / f"{stem}.jpg"), per_image[stem], complete)
        for stem in chosen
        if (folder / f"{stem}.jpg").exists()
    ]


def source_barriers(
    cache: Path, limit: int, seed: int, archive: Path | None = None
) -> list[Sample]:
    """Degraus (soleira/outros), meios-fios e escadas, a partir do XML do CVAT.

    Degraus com menos de 3 cm ficam de fora de proposito: viram fundo, porque
    anunciar uma soleira de 2 cm e ruido para quem usa bengala.
    """
    if archive is None:
        archive = download(URLS["barriers"], cache / "wm_barriers_data.zip", "barreiras (8 GB)")
    bundle = zipfile.ZipFile(archive)
    xml_name = next(n for n in bundle.namelist() if n.endswith(".xml"))
    root = ElementTree.fromstring(bundle.read(xml_name))
    members = {Path(n).name: n for n in bundle.namelist() if n.lower().endswith(".jpg")}
    samples: list[Sample] = []
    for image in root.iter("image"):
        name = Path(image.get("name", "")).name  # o XML guarda "images/000083035.jpg"
        if name not in members:
            continue
        width, height = float(image.get("width", 0)), float(image.get("height", 0))
        labels: list[Label] = []
        for box in image.iter("box"):
            attrs = {a.get("name"): a.text for a in box.iter("attribute")}
            if box.get("label") == "stair":
                cls = "stairs"
            elif box.get("label") == "step":
                if attrs.get("height") == "less than 3cm":
                    continue
                cls = "curb" if attrs.get("type") == "curb" else "step"
            else:
                continue  # rampa e corrimao: fora do vocabulario
            x1, y1 = float(box.get("xtl")), float(box.get("ytl"))
            x2, y2 = float(box.get("xbr")), float(box.get("ybr"))
            labels.append((cls, (x1 + x2) / 2 / width, (y1 + y2) / 2 / height,
                           (x2 - x1) / width, (y2 - y1) / height))

        def load(member: str = members[name], size: tuple[float, float] = (width, height)):
            data = np.frombuffer(bundle.read(member), np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
            # O CVAT anota a imagem como o navegador a mostra; se a orientacao do
            # EXIF nao bate com o tamanho anotado, usa os pixels crus.
            if img is not None and (img.shape[1], img.shape[0]) != size:
                img = cv2.imdecode(data, cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
                if img is None or (img.shape[1], img.shape[0]) != size:
                    return None
            return img

        samples.append(Sample("barriers", Path(name).stem, load, labels,
                              frozenset({"step", "curb", "stairs"})))
    random.Random(seed).shuffle(samples)
    return samples[:limit]


def source_homeobjects(
    cache: Path, limit: int, seed: int, folder: Path | None = None
) -> list[Sample]:
    """Portas e mobiliario de interiores (as outras classes do dataset ficam de fora)."""
    if folder is None:
        archive = download(URLS["homeobjects"], cache / "homeobjects-3K.zip", "HomeObjects")
        folder = cache / "homeobjects"
        if not (folder / "images").exists():
            zipfile.ZipFile(archive).extractall(folder)
    names = ["bed", "sofa", "chair", "table", "lamp", "tv", "laptop", "wardrobe", "window",
             "door", "potted plant", "photo frame"]
    mapping = {"sofa": "couch", "chair": "chair", "table": "dining table", "door": "door",
               "potted plant": "potted plant"}
    complete = frozenset(mapping.values())
    samples = []
    for image in list_images(folder / "images"):
        label_file = folder / "labels" / image.parent.name / f"{image.stem}.txt"
        rows: list[Label] = []
        if label_file.exists():
            for c, cx, cy, w, h in yolo_lines(label_file.read_text()):
                if names[c] in mapping:
                    rows.append((mapping[names[c]], cx, cy, w, h))
        key = f"{image.parent.name}_{image.stem}"  # nomes podem repetir entre treino e validacao
        samples.append(Sample("homeobjects", key, from_file(image), rows, complete))
    random.Random(seed).shuffle(samples)
    return samples[:limit]


def source_doordetect(cache: Path, limit: int, seed: int) -> list[Sample]:
    """Portas. Lido direto do zip: ha nomes de arquivo com mais de 200
    caracteres, que estouram o limite de caminho do Windows ao extrair."""
    archive = download(URLS["doordetect"], cache / "doordetect.zip", "DoorDetect")
    bundle = zipfile.ZipFile(archive)
    labels_by_stem = {Path(n).stem: n for n in bundle.namelist()
                      if "/labels/" in n and n.endswith(".txt")}
    samples = []
    for member in bundle.namelist():
        if "/images/" not in member or Path(member).suffix.lower() not in IMAGE_SUFFIXES:
            continue
        stem = Path(member).stem
        rows: list[Label] = []
        if stem in labels_by_stem:
            text = bundle.read(labels_by_stem[stem]).decode(errors="ignore")
            # 0 porta, 1 macaneta, 2 porta de armario, 3 porta de geladeira
            rows = [("door", cx, cy, w, h) for c, cx, cy, w, h in yolo_lines(text) if c == 0]

        def load(name: str = member) -> np.ndarray | None:
            return cv2.imdecode(np.frombuffer(bundle.read(name), np.uint8), cv2.IMREAD_COLOR)

        key = f"{stem[:40]}_{hashlib.md5(stem.encode()).hexdigest()[:8]}"
        samples.append(Sample("doordetect", key, load, rows, frozenset({"door"})))
    random.Random(seed).shuffle(samples)
    return samples[:limit]


ROD_NAMES = [
    "Bike", "Building", "Car", "Person", "Stairs", "Traffic sign", "Electrical Pole", "Road",
    "Motorcycle", "Dustbin", "Dog", "Manhole", "Tree", "Guard rail", "Pedestrian crosswalk",
    "Truck", "Bus", "Bench", "Traffic Cone", "Fire hydrant", "Teraffic Barrel", "Plant Pot",
    "Electrical Box", "Chair", "Bicycle Rack",
]
ROD_MAP = {
    "Bike": "bicycle", "Car": "car", "Person": "person", "Stairs": "stairs",
    "Traffic sign": "traffic sign", "Electrical Pole": "pole", "Motorcycle": "motorcycle",
    "Dustbin": "trash can", "Dog": "dog", "Pedestrian crosswalk": "crosswalk", "Truck": "truck",
    "Bus": "bus", "Bench": "bench", "Traffic Cone": "traffic cone",
    "Fire hydrant": "fire hydrant", "Plant Pot": "potted plant", "Chair": "chair",
}
ROD_WANTED = {"Stairs", "Electrical Pole", "Dustbin", "Pedestrian crosswalk", "Traffic Cone",
              "Traffic sign"}


def source_rod(cache: Path, limit: int, seed: int) -> list[Sample]:
    """Calcadas: so imagens com poste, lixeira, cone, placa, faixa ou escada.

    O ROD junta 26 datasets do Roboflow, cada um rotulado com as proprias
    classes -- nenhuma classe e tratada como completa, os professores
    preenchem o resto.
    """
    rows = []
    for split in ("train", "valid", "test"):
        meta = download(URLS["rod"].format(path=f"{split}/metadata.csv"),
                        cache / "rod" / f"{split}_metadata.csv", f"ROD {split}/metadata.csv")
        with meta.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if ROD_WANTED & {c.strip() for c in row["class_names"].split(",")}:
                    rows.append((split, row))
    random.Random(seed).shuffle(rows)
    rows = rows[:limit]
    folder = cache / "rod"
    jobs = [(URLS["rod"].format(path=f"{split}/{row['file_name']}"),
             folder / split / row["file_name"]) for split, row in rows]
    print(f"  rod: {download_many(jobs)}/{len(jobs)} imagens")
    samples = []
    for split, row in rows:
        image = folder / split / row["file_name"]
        if not image.exists():
            continue
        labels: list[Label] = []
        for box in json.loads(row["bboxes_yolo"] or "[]"):
            name = ROD_NAMES[int(box["class_id"])]
            if name in ROD_MAP:
                labels.append((ROD_MAP[name], box["x_center"], box["y_center"],
                               box["width"], box["height"]))
        samples.append(Sample("rod", f"{split}_{image.stem}", from_file(image), labels))
    return samples


ESP_NAME = re.compile(r"^(?P<device>.+)-(?P<ms>\d{13})-\d+$")


def read_names(folder: Path) -> list[str]:
    """Nomes das classes de uma exportacao YOLO (data.yaml, obj.names ou classes.txt)."""
    for yaml_file in folder.rglob("data.yaml"):
        import yaml

        names = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))["names"]
        return list(names.values()) if isinstance(names, dict) else list(names)
    for names_file in ("obj.names", "classes.txt"):
        for path in folder.rglob(names_file):
            lines = path.read_text(encoding="utf-8").splitlines()
            return [line.strip() for line in lines if line.strip()]
    raise SystemExit(f"Nenhum data.yaml, obj.names ou classes.txt em {folder}")


def find_label(folder: Path, image: Path) -> Path | None:
    """Rotulo YOLO da imagem: ao lado dela (CVAT YOLO 1.1) ou em labels/
    espelhando images/ (exportacao Ultralytics)."""
    beside = image.with_suffix(".txt")
    if beside.exists():
        return beside
    parts = list(image.relative_to(folder).parts)
    if "images" in parts:
        position = len(parts) - 1 - parts[::-1].index("images")
        parts[position] = "labels"
        mirrored = folder.joinpath(*parts).with_suffix(".txt")
        if mirrored.exists():
            return mirrored
    return None


def esp_sessions(images: list[Path], gap_min: float) -> dict[Path, str]:
    """Sessao de cada quadro: mais de gap_min minutos sem quadro abre outra.

    Quadros vizinhos sao quase identicos; separar treino e teste por quadro
    colocaria o mesmo corredor dos dois lados e inflaria o resultado.
    """
    stamped, sessions = [], {}
    for image in images:
        match = ESP_NAME.match(image.stem)
        if match:
            stamped.append((match["device"], int(match["ms"]), image))
        else:
            sessions[image] = image.parent.name  # sem horario no nome: uma pasta, uma sessao
    stamped.sort()
    last: dict[str, int] = {}
    counter: dict[str, int] = defaultdict(int)
    for device, ms, image in stamped:
        if device in last and ms - last[device] > gap_min * 60_000:
            counter[device] += 1
        last[device] = ms
        sessions[image] = f"{device}-s{counter[device]}"
    return sessions


def source_esp(folder: Path, test_fraction: float, gap_min: float) -> list[Sample]:
    """Imagens da ESP revisadas pela equipe; o teste separado por sessao.

    Se a exportacao tiver uma pasta "test", ela e o teste. Senao, as ultimas
    sessoes (por horario) viram teste -- nunca quadros sorteados.
    """
    names = [canonical_label(n) for n in read_names(folder)]
    unknown = sorted(set(names) - set(VOCAB))
    if unknown:
        print(f"  [aviso] classes da ESP fora do vocabulario, ignoradas: {unknown}")
    images = list_images(folder)
    sessions = esp_sessions(images, gap_min)
    explicit_test = any("test" in p.relative_to(folder).parts for p in images)
    ordered = sorted(set(sessions.values()))
    n_test = max(1, round(len(ordered) * test_fraction)) if len(ordered) > 1 else 0
    test_sessions = set(ordered[-n_test:]) if n_test else set()
    samples = []
    for image in images:
        label_file = find_label(folder, image)
        rows: list[Label] = []
        if label_file:
            for c, cx, cy, w, h in yolo_lines(label_file.read_text()):
                if c < len(names) and names[c] in VOCAB:
                    rows.append((names[c], cx, cy, w, h))
        if explicit_test:
            split = "test" if "test" in image.relative_to(folder).parts else "train"
        else:
            split = "test" if sessions[image] in test_sessions else "train"
        samples.append(Sample("esp", image.stem, from_file(image), rows, frozenset(VOCAB),
                              split=split, session=sessions[image]))
    if len(ordered) == 1 and not explicit_test:
        # Uma sessao so: o ultimo trecho continuo vira teste.
        cut = int(len(samples) * (1 - test_fraction))
        for sample in samples[cut:]:
            sample.split = "test"
    return samples


# ---------------------------------------------------------------- professores


@dataclass
class Teacher:
    path: str
    model: object
    names: dict[int, str]  # ja no vocabulario do projeto
    accept: frozenset[str]  # classes que este professor rotula


def resolve_weights(name: str) -> str:
    path = Path(name)
    if path.exists():
        return str(path)
    local = ROOT / "models" / path.name
    local.parent.mkdir(parents=True, exist_ok=True)
    return str(local)  # o ultralytics baixa os pesos oficiais para o caminho pedido


def load_teachers(paths: list[str], vocab: list[str]) -> list[Teacher]:
    """O 1o professor rotula as classes do COCO; os demais, so as classes novas."""
    from ultralytics import YOLO

    teachers = []
    for position, name in enumerate(paths):
        model = YOLO(resolve_weights(name))
        names = {int(i): canonical_label(str(n)) for i, n in model.names.items()}
        scope = set(COCO_CLASSES) if position == 0 else set(NEW_CLASSES)
        accept = frozenset(set(names.values()) & scope & set(vocab))
        print(f"  professor {Path(name).name}: {sorted(accept)}")
        teachers.append(Teacher(name, model, names, accept))
    return teachers


def iou_xywh(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    ax1, ay1, ax2, ay2 = a[0] - a[2] / 2, a[1] - a[3] / 2, a[0] + a[2] / 2, a[1] + a[3] / 2
    bx1, by1, bx2, by2 = b[0] - b[2] / 2, b[1] - b[3] / 2, b[0] + b[2] / 2, b[1] + b[3] / 2
    inter = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def pseudo_labels(
    teachers: list[Teacher], image: np.ndarray, sample: Sample, conf: float, device: str
) -> list[Label]:
    """Objetos que os professores veem nas classes que a fonte nao cobriu."""
    height, width = image.shape[:2]
    added: list[Label] = []
    for teacher in teachers:
        wanted = teacher.accept - sample.complete
        if not wanted:
            continue
        ids = [i for i, n in teacher.names.items() if n in wanted]
        result = teacher.model.predict(image, imgsz=640, conf=conf, classes=ids, device=device,
                                       verbose=False)[0]
        boxes = zip(result.boxes.xyxy.tolist(), result.boxes.cls.tolist(), strict=True)
        for (x1, y1, x2, y2), cls in boxes:
            name = teacher.names[int(cls)]
            box = ((x1 + x2) / 2 / width, (y1 + y2) / 2 / height,
                   (x2 - x1) / width, (y2 - y1) / height)
            existing = [row[1:] for row in sample.labels + added if row[0] == name]
            if all(iou_xywh(box, other) < 0.5 for other in existing):
                added.append((name, *box))
    return added


# ------------------------------------------------------------ camera da ESP


def degrade(image: np.ndarray, rng: random.Random) -> tuple[np.ndarray, str]:
    """Aproxima uma foto boa do que a OV2640 entrega -- as condicoes que, medidas
    em pipeline_ablation.py, mais derrubam a deteccao."""
    kind = rng.choice(["escuro", "borrao", "jpeg", "baixa_res", "cor"])
    if kind == "escuro":
        linear = (image.astype(np.float32) / 255.0) ** 2.2 * rng.uniform(0.25, 0.5)
        noise = np.random.default_rng(rng.randrange(1 << 30)).normal(
            0, rng.uniform(0.015, 0.035), linear.shape
        )
        image = (np.clip(linear + noise, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)
    elif kind == "borrao":
        length = rng.choice([5, 7, 9, 11, 13])
        kernel = np.zeros((length, length), np.float32)
        if rng.random() < 0.7:
            kernel[length // 2, :] = 1.0  # passada: borrao sobretudo horizontal
        else:
            np.fill_diagonal(kernel, 1.0)
        image = cv2.filter2D(image, -1, kernel / kernel.sum())
    elif kind == "jpeg":
        quality = [int(cv2.IMWRITE_JPEG_QUALITY), rng.randint(20, 45)]
        ok, buffer = cv2.imencode(".jpg", image, quality)
        image = cv2.imdecode(buffer, cv2.IMREAD_COLOR) if ok else image
    elif kind == "baixa_res":
        height, width = image.shape[:2]
        small_size = (320, max(1, round(height * 320 / width)))
        small = cv2.resize(image, small_size, interpolation=cv2.INTER_AREA)
        image = cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)
    else:  # balanco de branco errado e pouco contraste
        gains = np.array([rng.uniform(0.85, 1.15) for _ in range(3)], np.float32)
        image = np.clip(image.astype(np.float32) * gains * 0.9 + 12, 0, 255).astype(np.uint8)
    return image, kind


def fit(image: np.ndarray, max_side: int) -> np.ndarray:
    height, width = image.shape[:2]
    scale = max_side / max(height, width)
    if scale >= 1:
        return image
    size = (round(width * scale), round(height * scale))
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)


def yolo_text(labels: list[Label], index: dict[str, int]) -> str:
    return "".join(
        f"{index[c]} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n"
        for c, cx, cy, w, h in labels
        if c in index
    )


# ------------------------------------------------------------------- build


def load_sources(args: argparse.Namespace, professor: bool) -> list[Sample]:
    cache = args.cache
    default = ["barriers", "homeobjects", "doordetect", "rod"]
    sources = args.sources or (default if professor else ["coco", *default])
    loaders = {
        "coco": lambda: source_coco(cache, min(args.coco, args.limit), args.seed),
        "barriers": lambda: source_barriers(cache, args.limit, args.seed, args.barriers_zip),
        "homeobjects": lambda: source_homeobjects(cache, args.limit, args.seed,
                                                  args.homeobjects_dir),
        "doordetect": lambda: source_doordetect(cache, args.limit, args.seed),
        "rod": lambda: source_rod(cache, min(args.rod, args.limit), args.seed),
    }
    samples: list[Sample] = []
    for name in sources:
        print(f"[fonte] {name}", flush=True)
        samples += loaders[name]()
    if args.esp and not professor:
        print(f"[fonte] esp ({args.esp})", flush=True)
        samples += source_esp(args.esp, args.esp_test, args.session_gap)
    return samples


def cmd_build(args: argparse.Namespace) -> None:
    professor = args.stage == "professor"
    vocab = NEW_CLASSES if professor else VOCAB
    index = vocab_index(vocab)
    samples = load_sources(args, professor)
    teachers = [] if professor or not args.teachers else load_teachers(args.teachers, vocab)
    rng = random.Random(args.seed)
    out = args.out
    if out.exists() and args.overwrite:
        shutil.rmtree(out)
    manifest, train_list = [], []
    counts: dict[str, Counter] = defaultdict(Counter)
    for number, sample in enumerate(samples, 1):
        image = sample.load()
        if image is None:
            continue
        image = fit(image, args.max_side)
        added = [] if professor else pseudo_labels(teachers, image, sample, args.pseudo_conf,
                                                   args.device)
        labels = [row for row in sample.labels + added if row[0] in index]
        digest = hashlib.md5(f"{sample.source}{sample.key}".encode()).hexdigest()
        split = sample.split or ("val" if int(digest, 16) % 100 < args.val * 100 else "train")
        degraded = ""
        if split == "train" and sample.source != "esp" and rng.random() < args.degrade:
            image, degraded = degrade(image, rng)
        stem = f"{sample.source}_{sample.key}"
        for kind in ("images", "labels"):
            (out / kind / split).mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out / "images" / split / f"{stem}.jpg"), image,
                    [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        (out / "labels" / split / f"{stem}.txt").write_text(yolo_text(labels, index))
        if split == "train":
            repeats = args.esp_repeat if sample.source == "esp" else 1
            train_list += [f"./images/train/{stem}.jpg"] * repeats
        counts[split].update(row[0] for row in labels)
        manifest.append({
            "arquivo": f"{split}/{stem}.jpg", "fonte": sample.source, "sessao": sample.session,
            "rotulos_humanos": len(sample.labels), "pseudo_rotulos": len(added),
            "degradacao": degraded,
        })
        if number % 500 == 0:
            print(f"  {number}/{len(samples)} imagens", flush=True)

    if not manifest:
        raise SystemExit("Nenhuma imagem carregada -- confira as fontes e os caminhos.")
    write_outputs(out, vocab, manifest, train_list, teachers)
    print(f"\n[ok] {out / 'data.yaml'}  ({len(vocab)} classes)")
    for split in ("train", "val", "test"):
        n = sum(1 for m in manifest if m["arquivo"].startswith(split + "/"))
        if n:
            print(f"  {split:5s} {n:6d} imagens  objetos: {sum(counts[split].values())}")
    rare = sorted(vocab, key=lambda c: counts["train"][c])[:6]
    print("  classes com menos exemplos no treino:",
          ", ".join(f"{c}={counts['train'][c]}" for c in rare))


def write_outputs(
    out: Path, vocab: list[str], manifest: list[dict], train_list: list[str],
    teachers: list[Teacher],
) -> None:
    import yaml

    (out / "train.txt").write_text("\n".join(train_list) + "\n")
    data = {"path": out.resolve().as_posix(), "train": "train.txt", "val": "images/val",
            "names": dict(enumerate(vocab))}
    if (out / "images" / "test").exists():
        data["test"] = "images/test"
    (out / "data.yaml").write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                                   encoding="utf-8")
    with (out / "manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manifest[0]))
        writer.writeheader()
        writer.writerows(manifest)
    rows = "\n".join(f"| {name} | {use} | {lic} |" for name, use, lic in LICENSES)
    used = ", ".join(Path(t.path).name for t in teachers) or "nenhum (so rotulos humanos)"
    (out / "SOURCES.md").write_text(
        "# Fontes do dataset\n\n| Fonte | Uso aqui | Licenca |\n|---|---|---|\n"
        f"{rows}\n\nPseudo-rotulos: {used}. Detalhe por imagem em manifest.csv.\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------- prelabel


def cmd_prelabel(args: argparse.Namespace) -> None:
    """Rotulos automaticos para a equipe so corrigir (CVAT: formato YOLO 1.1)."""
    images = list_images(args.images)
    kept, last = [], {}
    for image in images:
        match = ESP_NAME.match(image.stem)
        if match:
            device, ms = match["device"], int(match["ms"])
        else:
            device, ms = "", int(image.stat().st_mtime * 1000)
        if device in last and ms - last[device] < args.every_seconds * 1000:
            continue
        last[device] = ms
        kept.append(image)
    print(f"{len(kept)} de {len(images)} quadros (1 a cada {args.every_seconds:g} s)")
    teachers = load_teachers(args.teachers, VOCAB)
    index = vocab_index(VOCAB)
    out = args.out
    for folder in ("images", "labels", "cvat/obj_train_data"):
        (out / folder).mkdir(parents=True, exist_ok=True)
    counts: Counter = Counter()
    for image_path in kept:
        image = cv2.imread(str(image_path))
        if image is None:
            continue
        sample = Sample("esp", image_path.stem, from_file(image_path))
        rows = pseudo_labels(teachers, image, sample, args.conf, args.device)
        text = yolo_text(rows, index)
        shutil.copy2(image_path, out / "images" / image_path.name)
        (out / "labels" / f"{image_path.stem}.txt").write_text(text)
        (out / "cvat" / "obj_train_data" / f"{image_path.stem}.txt").write_text(text)
        counts.update(row[0] for row in rows)
    import yaml

    data = {"path": ".", "train": "images", "val": "images", "names": dict(enumerate(VOCAB))}
    (out / "data.yaml").write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                                   encoding="utf-8")
    cvat = out / "cvat"
    (cvat / "obj.names").write_text("\n".join(VOCAB) + "\n")
    (cvat / "obj.data").write_text(
        f"classes = {len(VOCAB)}\nnames = obj.names\ntrain = train.txt\n"
    )
    (cvat / "train.txt").write_text("".join(f"obj_train_data/{p.name}\n" for p in kept))
    shutil.make_archive(str(out / "cvat_yolo_1.1"), "zip", cvat)
    print(f"\n[ok] {len(kept)} imagens em {out / 'images'}")
    print(f"     rotulos para o CVAT: {out / 'cvat_yolo_1.1.zip'} (importar como YOLO 1.1)")
    print("     mais encontrados:", ", ".join(f"{c}={n}" for c, n in counts.most_common(10)))


# --------------------------------------------------------------------- cli


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    pre = sub.add_parser("prelabel", help="rotulos automaticos nos quadros da ESP")
    pre.add_argument("--images", type=Path, required=True, help="quadros crus (var/raw)")
    pre.add_argument("--out", type=Path, required=True)
    pre.add_argument("--every-seconds", type=float, default=5.0,
                     help="1 quadro a cada N s por dispositivo: vizinhos sao quase iguais")
    pre.add_argument("--teachers", nargs="+", default=["yolo26l.pt", "yolo26l-objv1-150.pt"])
    pre.add_argument("--conf", type=float, default=0.35,
                     help="mais baixo que no build: apagar um rotulo e mais rapido que desenhar")
    pre.add_argument("--device", default=default_device())

    build = sub.add_parser("build", help="junta as fontes num dataset YOLO")
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--stage", choices=["final", "professor"], default="final",
                       help="professor: so as classes novas, so rotulos humanos")
    build.add_argument("--sources", nargs="*",
                       choices=["coco", "barriers", "homeobjects", "doordetect", "rod"])
    build.add_argument("--esp", type=Path, help="exportacao YOLO revisada das imagens da ESP")
    build.add_argument("--teachers", nargs="*", default=["yolo26l.pt", "yolo26l-objv1-150.pt"])
    build.add_argument("--cache", type=Path, default=ROOT / "datasets" / "cache")
    build.add_argument("--coco", type=int, default=6000, help="imagens do COCO")
    build.add_argument("--rod", type=int, default=5000, help="imagens do ROD")
    build.add_argument("--limit", type=int, default=100_000, help="teto por fonte (teste rapido)")
    build.add_argument("--esp-test", type=float, default=0.25,
                       help="fracao das SESSOES da ESP separada para o teste")
    build.add_argument("--esp-repeat", type=int, default=4,
                       help="quantas vezes cada imagem da ESP entra no treino")
    build.add_argument("--session-gap", type=float, default=10.0,
                       help="minutos sem quadro que separam duas sessoes")
    build.add_argument("--degrade", type=float, default=0.3,
                       help="fracao das imagens publicas degradadas como a camera da ESP")
    build.add_argument("--pseudo-conf", type=float, default=0.5)
    build.add_argument("--val", type=float, default=0.1)
    build.add_argument("--max-side", type=int, default=1280)
    build.add_argument("--device", default=default_device())
    build.add_argument("--seed", type=int, default=2026)
    build.add_argument("--overwrite", action="store_true")
    build.add_argument("--barriers-zip", type=Path, help="zip ja baixado (pula os 8 GB)")
    build.add_argument("--homeobjects-dir", type=Path, help="HomeObjects ja extraido")
    args = parser.parse_args()
    {"build": cmd_build, "prelabel": cmd_prelabel}[args.command](args)


if __name__ == "__main__":
    main()
