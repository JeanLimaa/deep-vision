"""Configuracao central do servidor.

Toda variavel pode ser sobrescrita por ambiente ou pelo arquivo ``.env`` com o
prefixo ``AVS_`` e ``__`` como separador de nivel.
Ex.: ``AVS_VISION__CONFIDENCE_THRESHOLD=0.5``
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.vision.labels import MOBILITY_LABELS

ROOT_DIR = Path(__file__).resolve().parents[2]
SERVER_DIR = ROOT_DIR / "server"
VAR_DIR = SERVER_DIR / "var"


class ServerSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    # Token compartilhado com o firmware. Vazio = autenticacao desabilitada (dev).
    device_token: str = ""
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])


class VisionSettings(BaseModel):
    # "yolo" usa ultralytics; "fake" gera deteccoes sinteticas (sem dependencias
    # pesadas) e serve para rodar/testar o sistema inteiro sem GPU nem modelo.
    #
    # O padrao e "yolo", e nao "auto": se o YOLO nao carregar, o servidor para
    # na partida com o motivo. "auto" cai para o simulado quando o YOLO falha, e
    # foi assim que um servidor iniciado antes de instalar o torch passou horas
    # desenhando caixas sinteticas sobre a webcam, parecendo um detector ruim.
    # Numa medicao do TCC, dado falso silencioso e o pior erro possivel.
    backend: Literal["yolo", "fake", "auto"] = "yolo"
    # "auto" escolhe os pesos pelo dispositivo: o maior modelo que ainda sustenta
    # a taxa alvo em CPU, ou um mais preciso quando ha GPU. A tabela de tempos
    # esta em vision/yolo_detector.py e em docs/configuracao.md. Para fixar,
    # aponte AVS_VISION__MODEL_PATH para um arquivo (ex.: yolo26n.pt).
    model_path: str = "auto"
    # Modelos que rodam ao lado do principal e so ACRESCENTAM classes que ele
    # nao tem, filtradas pelo mesmo allowed_labels. Ex.: o YOLO26 do Objects365,
    # pronto, acrescenta poste de luz, cone, placa e lixeira:
    #   AVS_VISION__EXTRA_MODEL_PATHS=["yolo26s-objv1-150.pt"]
    # ou um modelo treinado em training/ com degrau e porta.
    extra_model_paths: list[str] = Field(default_factory=list)
    # "directml" = qualquer GPU DirectX 12 no Windows (AMD, Intel), via ONNX Runtime.
    device: Literal["auto", "cpu", "cuda", "mps", "directml"] = "auto"
    # Revocacao/precisao com yolo26s, 500 imagens do COCO val2017, pelo caminho
    # do servidor e com o perfil de mobilidade (IoU >= 0,5):
    #     conf 0,25 -> 58,2% / 75,6%     conf 0,35 -> 54,0% / 81,9%
    #     conf 0,50 -> 47,3% / 88,8%
    # O F1 e praticamente o mesmo de 0,25 a 0,35; 0,50 descartaria um objeto
    # real em cada oito para ganhar 7 pontos de precisao. O ruido intermitente
    # ("cat", "toothbrush") e cortado pelo rastreador (min_hits), que e o filtro
    # certo para ele.
    confidence_threshold: float = 0.35
    iou_threshold: float = 0.45
    max_detections: int = 20
    # Resolucao de entrada da rede. Multiplo de 32. E a resolucao em que os
    # modelos YOLO foram treinados: abaixo dela a acuracia cai visivelmente, e
    # o quadro VGA da OV2640 (640x480) chega sem precisar ser reamostrado.
    inference_size: int = 640
    # Classes que o detector pode reportar; as demais sao descartadas antes do
    # rastreio. Padrao: o perfil de mobilidade de vision/labels.py, que tira do
    # COCO o que nunca e resposta certa para quem caminha (girafa, aviao,
    # pizza...). Lista vazia ([]) libera todas as classes do modelo.
    allowed_labels: list[str] = Field(default_factory=lambda: sorted(MOBILITY_LABELS))
    # Classes ignoradas na narracao (ruido para o usuario final).
    ignored_labels: list[str] = Field(default_factory=list)
    # Numero maximo de quadros por segundo efetivamente inferidos por dispositivo.
    max_inference_fps: float = 8.0
    warmup_on_startup: bool = True


class CameraSettings(BaseModel):
    """Parametros opticos da camera da borda (OV2640 do ESP32-CAM).

    Usados no modelo pinhole de ``vision.spatial`` para estimar distancia a
    partir da altura da caixa. Ajuste ``horizontal_fov_deg`` se trocar a lente.
    """

    horizontal_fov_deg: float = 66.0
    # Faixa em que a estimativa monocular ainda e util; fora disso e omitida.
    min_estimated_distance_m: float = 0.30
    max_estimated_distance_m: float = 12.0
    # Fracao central do quadro tratada como "a frente" (o resto e esquerda/direita).
    center_band: float = 0.34


class PreprocessSettings(BaseModel):
    enabled: bool = True
    # Rotacao aplicada ao quadro recebido, em graus no sentido horario. A
    # orientacao depende de como o modulo da camera foi montado, e uma imagem de
    # cabeca para baixo praticamente zera a deteccao de pessoas -- o YOLO nunca
    # viu o mundo invertido no treino. O certo e corrigir na camera
    # (CAMERA_VFLIP no firmware); isto resolve sem regravar a placa, e e
    # aplicado antes de tudo, entao o painel tambem sai na posicao certa.
    # int em vez de Literal: variavel de ambiente chega como texto, e Literal de
    # inteiros nao aceita "180" -- o campo e validado logo abaixo.
    rotate_deg: int = 0
    denoise: bool = False
    # Equalizacao adaptativa de histograma (CLAHE). Desligada: medida no COCO128
    # com yolo11s, piorou a revocacao em todos os cenarios -- 46,6% -> 43,4% com
    # boa luz e 32,5% -> 30,6% com a imagem escurecida, justamente o caso que
    # deveria ajudar. Realca ruido e cria halos que o modelo nunca viu no treino.
    clahe: bool = False
    clahe_clip_limit: float = 2.0
    clahe_tile_grid: int = 8

    @field_validator("rotate_deg")
    @classmethod
    def _quarter_turns_only(cls, value: int) -> int:
        if value not in (0, 90, 180, 270):
            raise ValueError("rotate_deg deve ser 0, 90, 180 ou 270")
        return value


class TrackingSettings(BaseModel):
    iou_match_threshold: float = 0.30
    # Quadros consecutivos sem deteccao antes de descartar o rastro. Enquanto
    # vive, o rastro guarda a identidade: o objeto que pisca nao e reanunciado.
    max_misses: int = 8
    # Quadros sem deteccao em que o rastro ainda e desenhado e narrado. Acima
    # disso ele segue vivo (max_misses), mas some do painel e das respostas: a
    # caixa de um objeto que ja saiu de cena nao pode ficar ~1 s na tela.
    coast_frames: int = 2
    # Deteccoes consecutivas antes de considerar o objeto confirmado.
    #
    # E o filtro de falso positivo mais barato que existe aqui, e vale mais que
    # subir a confianca: o rastro so e narrado depois de min_hits quadros
    # SEGUIDOS com deteccao na MESMA regiao -- uma falha antes disso descarta o
    # rastro. A 8 quadros/s, 3 acertos sao ~0,4 s -- pouco para um objeto real,
    # muito para o ruido intermitente que produz "toothbrush" ou "cat". Com 4, a
    # camera presa ao corpo balancava o bastante para o rastro se perder antes
    # de ser confirmado.
    min_hits: int = 3


class ProximitySettings(BaseModel):
    """Zonas da Secao 5.4 do TCC (valores em metros)."""

    warning_distance_m: float = 1.50
    critical_distance_m: float = 0.80
    # Histerese: distancia extra necessaria para sair de uma zona mais grave.
    hysteresis_m: float = 0.10
    # Leituras acima disso sao consideradas "sem eco" (fora de alcance).
    max_valid_distance_m: float = 4.00
    min_valid_distance_m: float = 0.02
    # Se nao chegar telemetria nesse prazo, o alerta local e considerado obsoleto.
    reading_ttl_ms: int = 1500


class NarrationSettings(BaseModel):
    language: str = "pt-BR"
    # Intervalo minimo entre anuncios do mesmo objeto/direcao.
    object_cooldown_ms: int = 6000
    # Intervalo minimo entre alertas de obstaculo da mesma zona.
    obstacle_cooldown_ms: int = 2500
    critical_cooldown_ms: int = 1200
    # Validade de uma locucao na fila. O TTS leva segundos por frase e a fila
    # acumula; "obstaculo a 1,3 metros" dito 5 s depois ja nao e verdade e induz
    # o usuario ao erro. Respostas a comandos nao expiram: foram pedidas.
    alert_max_age_ms: int = 2000
    info_max_age_ms: int = 6000
    # Quantos objetos no maximo por locucao de cena.
    max_objects_per_utterance: int = 3
    # Anuncia distancia estimada junto do objeto.
    announce_distance: bool = True
    # Fala automatica de objetos novos (modo "guia"). Se falso, so responde a comandos.
    autonomous_narration: bool = True


class TTSSettings(BaseModel):
    # "pyttsx3" (offline), "gtts" (online), "null" (apenas texto/eventos).
    engine: Literal["pyttsx3", "gtts", "null", "auto"] = "auto"
    voice: str = ""
    rate_wpm: int = 180
    volume: float = 0.9
    # Pistas usadas para escolher automaticamente uma voz em portugues do Brasil.
    language_hints: tuple[str, ...] = ("pt_br", "pt-br", "portugu", "brazil", "brasil")
    cache_dir: Path = VAR_DIR / "tts-cache"
    cache_size: int = 128


class AudioSettings(BaseModel):
    """Destinos de audio.

    O TCC preve um alto-falante/fone acoplado ao dispositivo. Enquanto esse
    hardware nao existe, ``host`` (alto-falante do proprio servidor) e
    ``dashboard`` (navegador) permitem validar o sistema completo.
    """

    sinks: list[Literal["host", "device", "dashboard", "null"]] = Field(
        default_factory=lambda: ["dashboard", "host"]
    )
    # Volume logico 0..10 controlado pelos botoes do dispositivo.
    default_volume_step: int = 7
    max_queue: int = 16


class SpeechSettings(BaseModel):
    """Entrada de voz (comandos do usuario)."""

    # "whisper" usa faster-whisper local; "null" desabilita STT mas mantem o
    # canal de comandos por texto (dashboard/simulador).
    stt_engine: Literal["whisper", "null", "auto"] = "auto"
    whisper_model: str = "small"
    whisper_compute_type: str = "int8"
    language: str = "pt"
    # Fonte de captura: "device" (microfone do dispositivo), "host" (microfone
    # do servidor) ou "text" (somente comandos digitados).
    source: Literal["device", "host", "text"] = "text"


class StorageSettings(BaseModel):
    var_dir: Path = VAR_DIR
    # Grava quadros anotados para uso nas figuras do TCC.
    save_annotated_frames: bool = False
    snapshots_dir: Path = VAR_DIR / "snapshots"
    # Grava o JPEG exatamente como chegou da placa, sem caixas, no maximo um a
    # cada raw_frames_interval_ms. E o material para anotar e medir precisao e
    # revocacao no ambiente real (docs/testes.md, secao 2) -- o quadro anotado
    # nao serve para isso, as caixas do proprio detector estariam desenhadas.
    save_raw_frames: bool = False
    raw_frames_interval_ms: int = 1000
    raw_dir: Path = VAR_DIR / "raw"
    event_log_path: Path = VAR_DIR / "events.jsonl"
    log_events: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AVS_",
        env_nested_delimiter="__",
        env_file=(ROOT_DIR / ".env", SERVER_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["dev", "prod"] = "dev"
    log_level: str = "INFO"

    server: ServerSettings = Field(default_factory=ServerSettings)
    vision: VisionSettings = Field(default_factory=VisionSettings)
    camera: CameraSettings = Field(default_factory=CameraSettings)
    preprocess: PreprocessSettings = Field(default_factory=PreprocessSettings)
    tracking: TrackingSettings = Field(default_factory=TrackingSettings)
    proximity: ProximitySettings = Field(default_factory=ProximitySettings)
    narration: NarrationSettings = Field(default_factory=NarrationSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    speech: SpeechSettings = Field(default_factory=SpeechSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)

    def ensure_directories(self) -> None:
        for path in (
            self.storage.var_dir,
            self.storage.snapshots_dir,
            self.storage.raw_dir,
            self.tts.cache_dir,
            self.storage.event_log_path.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    """Singleton preguicoso -- evita reler ambiente a cada requisicao."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings(new: Settings | None = None) -> None:
    """Usado pelos testes para injetar configuracao isolada."""
    global _settings
    _settings = new
