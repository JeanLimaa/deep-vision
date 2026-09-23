"""Traducao das classes COCO e alturas medias usadas na estimativa de distancia.

O modelo base (Secao 5.2 do TCC) parte de pesos pre-treinados no COCO, cujas
80 classes tem rotulos em ingles. A narracao e em pt-BR, entao a traducao vive
aqui, isolada, e a mesma tabela e reutilizada pelo casador de intencoes de voz.
"""

from __future__ import annotations

# Rotulo COCO -> (nome pt-BR, artigo definido)
COCO_PT: dict[str, tuple[str, str]] = {
    "person": ("pessoa", "uma"),
    "bicycle": ("bicicleta", "uma"),
    "car": ("carro", "um"),
    "motorcycle": ("moto", "uma"),
    "airplane": ("aviao", "um"),
    "bus": ("onibus", "um"),
    "train": ("trem", "um"),
    "truck": ("caminhao", "um"),
    "boat": ("barco", "um"),
    "traffic light": ("semaforo", "um"),
    "fire hydrant": ("hidrante", "um"),
    "stop sign": ("placa de pare", "uma"),
    "parking meter": ("parquimetro", "um"),
    "bench": ("banco", "um"),
    "bird": ("passaro", "um"),
    "cat": ("gato", "um"),
    "dog": ("cachorro", "um"),
    "horse": ("cavalo", "um"),
    "sheep": ("ovelha", "uma"),
    "cow": ("vaca", "uma"),
    "elephant": ("elefante", "um"),
    "bear": ("urso", "um"),
    "zebra": ("zebra", "uma"),
    "giraffe": ("girafa", "uma"),
    "backpack": ("mochila", "uma"),
    "umbrella": ("guarda-chuva", "um"),
    "handbag": ("bolsa", "uma"),
    "tie": ("gravata", "uma"),
    "suitcase": ("mala", "uma"),
    "frisbee": ("frisbee", "um"),
    "skis": ("esqui", "um"),
    "snowboard": ("snowboard", "um"),
    "sports ball": ("bola", "uma"),
    "kite": ("pipa", "uma"),
    "baseball bat": ("taco", "um"),
    "baseball glove": ("luva", "uma"),
    "skateboard": ("skate", "um"),
    "surfboard": ("prancha", "uma"),
    "tennis racket": ("raquete", "uma"),
    "bottle": ("garrafa", "uma"),
    "wine glass": ("taca", "uma"),
    "cup": ("copo", "um"),
    "fork": ("garfo", "um"),
    "knife": ("faca", "uma"),
    "spoon": ("colher", "uma"),
    "bowl": ("tigela", "uma"),
    "banana": ("banana", "uma"),
    "apple": ("maca", "uma"),
    "sandwich": ("sanduiche", "um"),
    "orange": ("laranja", "uma"),
    "broccoli": ("brocolis", "um"),
    "carrot": ("cenoura", "uma"),
    "hot dog": ("cachorro-quente", "um"),
    "pizza": ("pizza", "uma"),
    "donut": ("rosquinha", "uma"),
    "cake": ("bolo", "um"),
    "chair": ("cadeira", "uma"),
    "couch": ("sofa", "um"),
    "potted plant": ("vaso de planta", "um"),
    "bed": ("cama", "uma"),
    "dining table": ("mesa", "uma"),
    "toilet": ("vaso sanitario", "um"),
    "tv": ("televisao", "uma"),
    "laptop": ("notebook", "um"),
    "mouse": ("mouse", "um"),
    "remote": ("controle remoto", "um"),
    "keyboard": ("teclado", "um"),
    "cell phone": ("celular", "um"),
    "microwave": ("micro-ondas", "um"),
    "oven": ("forno", "um"),
    "toaster": ("torradeira", "uma"),
    "sink": ("pia", "uma"),
    "refrigerator": ("geladeira", "uma"),
    "book": ("livro", "um"),
    "clock": ("relogio", "um"),
    "vase": ("vaso", "um"),
    "scissors": ("tesoura", "uma"),
    "teddy bear": ("ursinho", "um"),
    "hair drier": ("secador", "um"),
    "toothbrush": ("escova de dente", "uma"),
    # Classes urbanas previstas como trabalho futuro na Secao 6 do TCC.
    # Ja mapeadas para que o modelo refinado (training/) narre sem alterar codigo.
    "step": ("degrau", "um"),
    "stairs": ("escada", "uma"),
    "pothole": ("buraco", "um"),
    "pole": ("poste", "um"),
    "curb": ("meio-fio", "um"),
    "tactile paving": ("piso tatil", "um"),
    "door": ("porta", "uma"),
    "crosswalk": ("faixa de pedestres", "uma"),
    # Classes de interiores do HomeObjects-3K que o COCO nao tem.
    "window": ("janela", "uma"),
    "wardrobe": ("guarda-roupa", "um"),
    "lamp": ("luminaria", "uma"),
}

# Altura media real, em metros. Base do modelo pinhole em ``vision.spatial``.
# Valores aproximados: servem para uma estimativa grosseira ("cerca de 2 metros"),
# nao para medicao -- a medicao precisa vem do sensor ultrassonico.
OBJECT_HEIGHTS_M: dict[str, float] = {
    "person": 1.70,
    "bicycle": 1.05,
    "car": 1.50,
    "motorcycle": 1.20,
    "bus": 3.20,
    "truck": 3.00,
    "traffic light": 0.75,
    "fire hydrant": 0.75,
    "stop sign": 0.75,
    "parking meter": 1.20,
    "bench": 0.90,
    "dog": 0.55,
    "cat": 0.30,
    "backpack": 0.45,
    "umbrella": 0.90,
    "handbag": 0.30,
    "suitcase": 0.60,
    "bottle": 0.25,
    "cup": 0.12,
    "chair": 0.90,
    "couch": 0.85,
    "potted plant": 0.50,
    "bed": 0.60,
    "dining table": 0.75,
    "toilet": 0.75,
    "tv": 0.60,
    "laptop": 0.25,
    "cell phone": 0.15,
    "microwave": 0.30,
    "oven": 0.85,
    "refrigerator": 1.75,
    "sink": 0.25,
    "book": 0.25,
    "clock": 0.30,
    "door": 2.05,
    "wardrobe": 1.90,
    "pole": 3.00,
    "stairs": 1.00,
    "step": 0.18,
}

# Perfil "mobilidade": o que o detector pode reportar por padrao
# (``vision.allowed_labels``). Entram obstaculos, veiculos, mobiliario urbano e
# domestico, e os objetos pessoais que o usuario procura por comando de voz
# ("onde esta o meu celular?"). Ficam de fora as classes do COCO que nunca sao a
# resposta certa para quem caminha -- girafa, aviao, pizza, esqui, gravata --
# e que, quando aparecem, sao sempre um engano do modelo: um anuncio absurdo
# desses custa mais confianca no dispositivo do que um objeto nao anunciado.
MOBILITY_LABELS: frozenset[str] = frozenset(
    {
        # pessoas e animais que cruzam o caminho
        "person", "dog", "cat", "horse",
        # veiculos
        "bicycle", "car", "motorcycle", "bus", "truck", "train",
        # mobiliario urbano
        "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
        # obstaculos no chao e objetos carregados
        "backpack", "umbrella", "handbag", "suitcase",
        # mobiliario e eletrodomesticos
        "chair", "couch", "bed", "dining table", "toilet", "sink", "refrigerator",
        "oven", "microwave", "tv", "potted plant",
        # objetos pessoais procurados por comando de voz
        "cell phone", "laptop", "keyboard", "mouse", "remote", "book", "clock",
        "bottle", "cup", "vase",
        # classes proprias previstas para o modelo refinado (training/)
        "step", "stairs", "pothole", "pole", "curb", "tactile paving", "door", "crosswalk",
        "window", "wardrobe", "lamp",
    }
)

# Classes que representam risco imediato de colisao/queda e por isso furam o
# cooldown normal de narracao.
HAZARD_LABELS: frozenset[str] = frozenset(
    {
        "car",
        "bus",
        "truck",
        "motorcycle",
        "bicycle",
        "train",
        "pothole",
        "step",
        "stairs",
        "pole",
        "curb",
    }
)


def label_pt(label: str) -> str:
    """Nome em pt-BR do rotulo; devolve o proprio rotulo se desconhecido."""
    entry = COCO_PT.get(label)
    return entry[0] if entry else label


def label_pt_with_article(label: str) -> str:
    """Ex.: ``chair`` -> ``uma cadeira``."""
    entry = COCO_PT.get(label)
    if not entry:
        return label
    name, article = entry
    return f"{article} {name}"


def real_height_m(label: str) -> float | None:
    return OBJECT_HEIGHTS_M.get(label)


def is_hazard(label: str) -> bool:
    return label in HAZARD_LABELS


def resolve_label_from_pt(text: str) -> str | None:
    """Casa um termo em pt-BR (ex.: "cadeira") com o rotulo COCO correspondente.

    Usado pelo comando de voz "tem uma cadeira aqui?".
    """
    needle = text.strip().lower()
    for label, (name, _) in COCO_PT.items():
        if needle in (name, label):
            return label
    for label, (name, _) in COCO_PT.items():
        if needle in name or name in needle:
            return label
    return None
