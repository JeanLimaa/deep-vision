"""Dispositivo de borda virtual -- ESP32-CAM emulado.

Reproduz, do ponto de vista do servidor, tudo o que o prototipo fisico faz:
envia quadros JPEG, publica leituras dos sensores ultrassonicos, emite eventos
de botao e recebe de volta as locucoes e os bipes. Serve para tres coisas:

1. desenvolver e validar o servidor sem depender do hardware montado;
2. reproduzir cenarios de teste identicos (mesmo video, mesmo perfil de sonar),
   o que e necessario para comparar medidas entre execucoes;
3. demonstrar o sistema completo quando o prototipo nao esta disponivel.

Exemplos:

    # cena sintetica + sonar oscilante, por WebSocket
    python simulator/virtual_device.py

    # webcam do notebook como camera da borda, com janela de preview
    python simulator/virtual_device.py --source 0 --preview

    # arquivo de video, no transporte HTTP descrito no artigo
    python simulator/virtual_device.py --source ruas.mp4 --transport http

    # aproximacao de obstaculo, para exercitar as zonas de proximidade
    python simulator/virtual_device.py --sonar approach
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

FRAME_MAGIC_IMAGE = 0x01
BINARY_HEADER = struct.Struct(">BQQ")

# --- controle de taxa ---
# O servidor descarta quadros quando a inferencia nao acompanha (o limitador de
# vision.max_inference_fps). Insistir na taxa cheia so gasta Wi-Fi e bateria sem
# produzir uma deteccao a mais, entao o dispositivo recua rapido e volta devagar.
# E o comportamento que o firmware deve ter na placa; aqui ele e exercitado.
BACKOFF_FACTOR = 1.25       # recuo a cada quadro descartado
RECOVER_FACTOR = 0.92       # avanco, depois de uma sequencia limpa
RECOVER_AFTER_ACKS = 8      # quadros aceitos seguidos antes de acelerar
MAX_INTERVAL_S = 1.0        # piso de 1 quadro/s, mesmo saturado


def now_ms() -> int:
    return int(time.time() * 1000)


# --------------------------------------------------------------------- video


class FrameSource:
    """Origem dos quadros: webcam, arquivo de video, pasta ou cena sintetica."""

    def __init__(self, source: str, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._capture: cv2.VideoCapture | None = None
        self._images: list[Path] = []
        self._index = 0
        self._tick = 0

        if source == "synthetic":
            return
        if source.isdigit():
            self._capture = cv2.VideoCapture(int(source), cv2.CAP_DSHOW)
        elif Path(source).is_dir():
            suffixes = {".jpg", ".jpeg", ".png"}
            self._images = sorted(
                path for path in Path(source).iterdir() if path.suffix.lower() in suffixes
            )
            if not self._images:
                raise SystemExit(f"Nenhuma imagem em {source}")
        else:
            self._capture = cv2.VideoCapture(source)

        if self._capture is not None and not self._capture.isOpened():
            raise SystemExit(f"Nao foi possivel abrir a fonte de video: {source}")

    def read(self) -> np.ndarray:
        self._tick += 1
        if self._capture is not None:
            ok, frame = self._capture.read()
            if not ok:
                # Video acabou: recomeca, para o teste rodar indefinidamente.
                self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self._capture.read()
            if ok:
                return self._fit(frame)
        if self._images:
            image = cv2.imread(str(self._images[self._index % len(self._images)]))
            self._index += 1
            if image is not None:
                return self._fit(image)
        return self._synthetic()

    def _fit(self, image: np.ndarray) -> np.ndarray:
        """Reduz para caber em width x height SEM mudar a proporcao.

        Forcar 640x480 numa webcam 16:9 estreitava tudo em 33%: medido no
        COCO128, isso sozinho custava 4 pontos de revocacao ao YOLO.
        """
        height, width = image.shape[:2]
        scale = min(self.width / width, self.height / height, 1.0)
        if scale == 1.0:
            return image
        size = (int(round(width * scale)), int(round(height * scale)))
        return cv2.resize(image, size, interpolation=cv2.INTER_AREA)

    def _synthetic(self) -> np.ndarray:
        """Cena artificial com formas em movimento.

        Nao contem objetos reais, entao o YOLO nao detecta nada -- de proposito:
        e o cenario para validar o transporte, a telemetria e a fila de fala
        usando o detector simulado (``AVS_VISION__BACKEND=fake``).
        """
        frame = np.full((self.height, self.width, 3), 24, dtype=np.uint8)
        phase = self._tick / 20.0

        # "chao" da cena, para dar uma referencia de perspectiva
        floor_top = int(self.height * 0.72)
        cv2.rectangle(frame, (0, floor_top), (self.width, self.height), (44, 44, 52), -1)
        for index in range(3):
            x = int((0.5 + 0.4 * math.sin(phase + index * 2.1)) * self.width)
            y = int(self.height * (0.45 + 0.1 * index))
            size = 40 + index * 22
            color = [(90, 160, 240), (110, 210, 140), (200, 140, 230)][index]
            cv2.rectangle(frame, (x - size // 2, y - size), (x + size // 2, y), color, -1)

        cv2.putText(
            frame,
            f"simulador  t={self._tick}",
            (12, 26),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (210, 210, 210),
            1,
            cv2.LINE_AA,
        )
        return frame

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()


# --------------------------------------------------------------------- sonar


@dataclass
class SonarSimulator:
    """Gera leituras plausiveis do HC-SR04, com ruido e falhas de eco.

    Perfis:
      ``oscillate``  vai e volta entre 0,3 m e 3,0 m (passa por todas as zonas)
      ``approach``   aproximacao continua ate o risco imediato, depois reinicia
      ``clear``      caminho sempre livre
      ``random``     passeio aleatorio
    """

    profile: str = "oscillate"
    seed: int = 3
    _tick: int = 0
    _distance: float = 2.5

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def read_cm(self) -> float | None:
        self._tick += 1
        if self.profile == "clear":
            base = 3.5
        elif self.profile == "approach":
            base = max(0.25, 3.0 - (self._tick % 120) * 0.025)
        elif self.profile == "random":
            self._distance = min(4.0, max(0.2, self._distance + self._rng.uniform(-0.25, 0.25)))
            base = self._distance
        else:
            base = 1.65 + 1.35 * math.sin(self._tick / 25.0)

        # 3% das leituras sem eco, como acontece com superficies obliquas.
        if self._rng.random() < 0.03:
            return None
        noise = self._rng.uniform(-0.02, 0.02)
        return round(max(0.02, base + noise) * 100, 1)


# ----------------------------------------------------------------- transporte


class SimulatedDevice:
    """Laco principal do dispositivo virtual."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.source = FrameSource(args.source, args.width, args.height)
        self.sonar = SonarSimulator(profile=args.sonar)
        self.sequence = 0
        self.jpeg_params = [int(cv2.IMWRITE_JPEG_QUALITY), args.quality]
        self.running = True
        self.stats = {"sent": 0, "spoken": 0, "tones": 0, "dropped": 0}

        # Intervalo pedido na linha de comando; "floor" e o menor intervalo
        # permitido, apertado quando o servidor anuncia seu proprio teto.
        self.target_interval = 1.0 / args.fps
        self.floor_interval = self.target_interval
        self.interval = self.target_interval
        self._clean_acks = 0
        self._preview_window = f"simulador -- {args.device_id} (q para sair)"

    # ------------------------------------------------------------- utilitarios

    def next_jpeg(self) -> bytes:
        frame = self.source.read()
        ok, buffer = cv2.imencode(".jpg", frame, self.jpeg_params)
        if not ok:
            raise RuntimeError("Falha ao codificar JPEG")
        self.sequence += 1
        return buffer.tobytes()

    def pump_preview(self, jpeg: bytes) -> None:
        """Janela local com o quadro exatamente como o servidor vai receber.

        Mostra o JPEG ja codificado e decodificado de volta, e nao o que a
        webcam entregou: a perda de ``--quality`` e a reducao de ``--width``
        aparecem aqui. E o que o detector enxerga.

        O painel web (`/api/v1/media/preview.mjpg`) mostra o mesmo quadro ja
        com as caixas desenhadas; esta janela existe para separar "a camera
        esta ruim" de "o detector esta errando".
        """
        if not self.args.preview:
            return
        image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return

        label = f"seq={self.sequence}  {len(jpeg) / 1024:.1f} kB  {image.shape[1]}x{image.shape[0]}"
        cv2.rectangle(image, (0, 0), (image.shape[1], 24), (0, 0, 0), -1)
        cv2.putText(
            image, label, (8, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 230, 0), 1, cv2.LINE_AA
        )
        try:
            cv2.imshow(self._preview_window, image)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                self.running = False
        except cv2.error as exc:
            # Instalacao headless do OpenCV: segue sem janela em vez de morrer.
            print(f"[simulador] preview indisponivel ({exc}); continuando sem janela")
            self.args.preview = False

    def close_preview(self) -> None:
        if self.args.preview:
            try:
                cv2.destroyWindow(self._preview_window)
            except cv2.error:
                pass

    def telemetry_payload(self) -> dict:
        return {
            "type": "telemetry",
            "sensors": [{"sensor_id": "front", "distance_cm": self.sonar.read_cm()}],
            "battery_pct": 87.0,
            "rssi_dbm": -58,
            "uptime_ms": now_ms() % 10_000_000,
            "firmware": "simulator-0.1.0",
        }

    def note_ack(self, dropped: bool) -> None:
        """Ajusta a taxa de envio conforme o servidor consegue ou nao acompanhar.

        Recuo multiplicativo, recuperacao gradual: um unico descarte ja reduz a
        taxa, mas voltar a acelerar exige uma sequencia limpa. Evita oscilar em
        volta do limite do servidor.
        """
        if not self.args.backpressure:
            return

        if dropped:
            self.stats["dropped"] += 1
            self._clean_acks = 0
            adjusted = min(MAX_INTERVAL_S, self.interval * BACKOFF_FACTOR)
        else:
            self._clean_acks += 1
            if self._clean_acks < RECOVER_AFTER_ACKS:
                return
            self._clean_acks = 0
            adjusted = max(self.floor_interval, self.interval * RECOVER_FACTOR)

        if abs(adjusted - self.interval) < 1e-6:
            return
        self.interval = adjusted
        if self.args.verbose:
            print(f"  .. taxa ajustada para {1.0 / self.interval:.1f} quadros/s")

    def on_action(self, action: dict) -> None:
        """Como o firmware reagiria a uma acao vinda do servidor."""
        kind = action.get("type")
        if kind == "speak":
            self.stats["spoken"] += 1
            marker = "!!" if action.get("interrupt") else "->"
            print(f"  {marker} FALA: {action.get('text')}")
            if action.get("tone"):
                print(f"     bipe: {action['tone']}")
        elif kind == "tone":
            self.stats["tones"] += 1
            print(f"  ~~ BIPE: {action.get('tone')}")
        elif kind == "config":
            print(f"  .. config do servidor: {action}")
            # O servidor anuncia quantos quadros por segundo ele consegue
            # inferir. Mandar acima disso e desperdicio garantido.
            max_fps = action.get("max_fps")
            if max_fps:
                self.floor_interval = max(self.target_interval, 1.0 / float(max_fps))
                self.interval = max(self.interval, self.floor_interval)

    # -------------------------------------------------------------- WebSocket

    async def run_websocket(self) -> None:
        import websockets

        url = (
            f"{self.args.server.replace('http', 'ws')}/api/v1/stream"
            f"?device_id={self.args.device_id}&token={self.args.token}"
        )
        print(f"[simulador] conectando em {url}")
        async with websockets.connect(url, max_size=8 * 1024 * 1024) as socket:
            await socket.send(json.dumps({"type": "hello", "firmware": "simulator-0.1.0"}))
            await asyncio.gather(
                self._send_frames(socket),
                self._send_telemetry(socket),
                self._receive(socket),
                self._read_keyboard(socket),
            )

    async def _send_frames(self, socket) -> None:
        while self.running:
            jpeg = await asyncio.to_thread(self.next_jpeg)
            header = BINARY_HEADER.pack(FRAME_MAGIC_IMAGE, self.sequence, now_ms())
            await socket.send(header + jpeg)
            self.stats["sent"] += 1
            self.pump_preview(jpeg)
            # Lido a cada volta: o ack pode ter mudado o intervalo no meio do sono.
            await asyncio.sleep(self.interval)

    async def _send_telemetry(self, socket) -> None:
        interval = 1.0 / self.args.sonar_hz
        while self.running:
            await socket.send(json.dumps(self.telemetry_payload()))
            await asyncio.sleep(interval)

    async def _receive(self, socket) -> None:
        async for message in socket:
            if isinstance(message, bytes):
                # Audio sintetizado: o prototipo com amplificador I2S tocaria aqui.
                print(f"  <> audio recebido: {len(message)} bytes")
                continue
            payload = json.loads(message)
            if payload.get("type") == "frame_ack":
                self.note_ack(bool(payload.get("dropped")))
                if self.args.verbose:
                    print(f"  .. ack seq={payload['sequence']} objs={payload['objects']}")
            else:
                self.on_action(payload)

    async def _read_keyboard(self, socket) -> None:
        """Botoes fisicos do prototipo, mapeados no teclado."""
        if not self.args.interactive:
            return
        print(
            "[simulador] teclas: [s] start/stop  [+/-] volume  "
            "[d] descrever cena  [q] sair"
        )
        loop = asyncio.get_running_loop()
        while self.running:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            key = line.strip().lower()
            if key in ("q", "quit", "sair"):
                self.running = False
                await socket.close()
                return
            mapping = {"s": "start_stop", "+": "volume_up", "-": "volume_down"}
            if key in mapping:
                await socket.send(json.dumps({"type": "button", "button": mapping[key]}))
            elif key == "d":
                await socket.send(
                    json.dumps({"type": "command", "text": "o que tem na minha frente?"})
                )
            elif key:
                await socket.send(json.dumps({"type": "command", "text": key}))

    # -------------------------------------------------------------------- HTTP

    async def run_http(self) -> None:
        """Modo POST por quadro -- o transporte descrito no artigo.

        Mantido para comparacao de latencia com o WebSocket; ver docs/protocolo.md.
        """
        import urllib.error
        import urllib.request

        base = self.args.server.rstrip("/")
        print(f"[simulador] enviando quadros por HTTP POST para {base}")
        telemetry_every = max(1, int(self.args.fps / self.args.sonar_hz))

        while self.running:
            jpeg = await asyncio.to_thread(self.next_jpeg)
            self.pump_preview(jpeg)
            try:
                response = await asyncio.to_thread(self._post_frame, base, jpeg)
            except urllib.error.URLError as exc:
                print(f"[simulador] servidor inacessivel: {exc}")
                await asyncio.sleep(1.0)
                continue

            self.stats["sent"] += 1
            self.note_ack(bool(response.get("dropped")))
            for action in response.get("actions", []):
                self.on_action(action)
            if self.args.verbose and not response.get("dropped"):
                print(
                    f"  .. seq={response['sequence']} objs={len(response['detections'])} "
                    f"inf={response['inference_ms']}ms"
                )

            if self.sequence % telemetry_every == 0:
                try:
                    telemetry = await asyncio.to_thread(self._post_telemetry, base)
                    for action in telemetry.get("actions", []):
                        self.on_action(action)
                except urllib.error.URLError:
                    pass

            await asyncio.sleep(self.interval)

    def _post_frame(self, base: str, jpeg: bytes) -> dict:
        import urllib.request

        request = urllib.request.Request(
            f"{base}/api/v1/ingest/frame",
            data=jpeg,
            method="POST",
            headers={
                "Content-Type": "image/jpeg",
                "X-Device-Id": self.args.device_id,
                "X-Frame-Seq": str(self.sequence),
                "X-Captured-At": str(now_ms()),
                "X-Device-Token": self.args.token,
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    def _post_telemetry(self, base: str) -> dict:
        import urllib.request

        payload = self.telemetry_payload()
        payload["device_id"] = self.args.device_id
        request = urllib.request.Request(
            f"{base}/api/v1/ingest/telemetry",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "X-Device-Token": self.args.token},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    # ------------------------------------------------------------------ ciclo

    async def run(self) -> None:
        try:
            if self.args.transport == "ws":
                await self.run_websocket()
            else:
                await self.run_http()
        finally:
            self.close_preview()
            self.source.close()
            print(
                f"[simulador] encerrado | quadros={self.stats['sent']} "
                f"descartados={self.stats['dropped']} falas={self.stats['spoken']} "
                f"taxa final={1.0 / self.interval:.1f}/s"
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ESP32-CAM virtual para testes do servidor")
    parser.add_argument("--server", default="http://127.0.0.1:8000", help="URL do servidor")
    parser.add_argument("--device-id", default="sim-01")
    parser.add_argument("--token", default="", help="Token do dispositivo, se configurado")
    parser.add_argument(
        "--source",
        default="synthetic",
        help="'synthetic', indice da webcam (ex.: 0), arquivo de video ou pasta de imagens",
    )
    parser.add_argument("--transport", choices=("ws", "http"), default="ws")
    # Os padroes abaixo espelham firmware/esp32cam/include/config.h de proposito:
    # FRAMESIZE_VGA (640x480), FRAME_INTERVAL_MS 125 (8 quadros/s) e
    # CAMERA_JPEG_QUALITY 12, que na escala do OV2640 (10 melhor .. 63 pior)
    # equivale a uma qualidade alta -- ~88 na escala do OpenCV. Medir com o
    # simulador so vale se ele entregar o mesmo quadro que a placa entregaria.
    parser.add_argument("--fps", type=float, default=8.0, help="Quadros por segundo enviados")
    parser.add_argument("--sonar-hz", type=float, default=5.0, dest="sonar_hz")
    parser.add_argument(
        "--sonar",
        choices=("oscillate", "approach", "clear", "random"),
        default="oscillate",
        help="Perfil das leituras do sensor ultrassonico",
    )
    parser.add_argument("--width", type=int, default=640, help="Largura maxima (mantem a proporcao)")
    parser.add_argument("--height", type=int, default=480, help="Altura maxima (mantem a proporcao)")
    parser.add_argument("--quality", type=int, default=88, help="Qualidade JPEG (1-100)")
    parser.add_argument(
        "--interactive", action="store_true", help="Habilita os botoes pelo teclado"
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Abre uma janela com o quadro que esta sendo enviado",
    )
    parser.add_argument(
        "--no-backpressure",
        dest="backpressure",
        action="store_false",
        help="Mantem a taxa fixa mesmo com quadros descartados (para comparacao)",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    device = SimulatedDevice(parse_args(argv))
    try:
        asyncio.run(device.run())
    except KeyboardInterrupt:
        print("\n[simulador] interrompido")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
