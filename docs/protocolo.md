# Protocolo borda ↔ servidor (v1)

Contrato comum ao firmware (`firmware/esp32cam/src/transport.cpp`), ao simulador
(`simulator/virtual_device.py`) e ao servidor (`server/app/api/routes_ingest.py`).
Alterar qualquer um dos três exige alterar os outros dois.

Base: `http://<servidor>:8000`

## Autenticação

Token compartilhado, opcional. Quando `AVS_SERVER__DEVICE_TOKEN` está definido:

- HTTP: cabeçalho `X-Device-Token: <token>`
- WebSocket: parâmetro de consulta `?token=<token>`

Vazio (padrão) desliga a verificação — adequado a rede local isolada, não à
internet aberta.

---

## WebSocket (transporte recomendado)

```
WS /api/v1/stream?device_id=<id>&token=<token>
```

### Quadros binários — dispositivo → servidor

Cabeçalho fixo de 17 bytes, big-endian, seguido do corpo:

| Offset | Tamanho | Campo |
|---|---|---|
| 0 | 1 | tipo: `0x01` imagem, `0x02` áudio |
| 1 | 8 | número de sequência (uint64) |
| 9 | 8 | carimbo de captura em ms (uint64) |
| 17 | n | corpo — JPEG ou PCM 16 bits mono 16 kHz |

O carimbo vem do relógio do dispositivo. Se ele não estiver sincronizado por
NTP, o servidor detecta a divergência e omite a latência de rede das métricas em
vez de publicar um valor incorreto.

### Quadros binários — servidor → dispositivo

| Offset | Tamanho | Campo |
|---|---|---|
| 0 | 1 | `0x81` — áudio sintetizado |
| 1 | 1 | formato: `0x01` WAV, `0x02` MP3 |
| 2 | n | áudio |

Dispositivos sem amplificador descartam esses quadros; os bipes locais seguem
funcionando.

### Mensagens JSON — dispositivo → servidor

```jsonc
{"type": "hello", "firmware": "0.1.0"}

{"type": "telemetry",
 "sensors": [{"sensor_id": "front", "distance_cm": 84.5}],  // null = sem eco
 "rssi_dbm": -58, "uptime_ms": 120345, "free_heap": 180000,
 "firmware": "0.1.0"}

{"type": "button", "button": "start_stop", "action": "press"}  // ou "long_press"

{"type": "command", "text": "o que tem na minha frente?"}

{"type": "ping"}
```

`button` aceita `power`, `start_stop`, `volume_up`, `volume_down`.

### Mensagens JSON — servidor → dispositivo

```jsonc
{"type": "config", "max_fps": 8.0, "warning_cm": 150, "critical_cm": 80}

{"type": "speak", "text": "Uma cadeira, a frente, a cerca de dois metros.",
 "priority": 10, "interrupt": false, "tone": null}

{"type": "tone", "tone": "pulse_fast"}     // pulse_slow | pulse_fast

{"type": "frame_ack", "sequence": 42, "dropped": false,
 "objects": 3, "inference_ms": 38.2}

{"type": "pong", "at_ms": 123456}
```

Prioridades: `0` ambiente · `10` informação · `20` resposta a comando ·
`30` alerta · `40` crítico. `interrupt: true` significa que o dispositivo deve
abortar a locução em curso.

Todo quadro de imagem recebe exatamente um `frame_ack`, mas não necessariamente
na ordem de envio. `dropped: true` significa que o quadro não foi inferido: ou
chegou enquanto o anterior ainda era processado e foi substituído por um mais
novo, ou a sessão está pausada, ou o limite de taxa foi atingido. É o sinal para
o dispositivo enviar menos quadros por segundo — o simulador já recua sozinho.

---

## HTTP (transporte do artigo)

Sem conexão persistente, as ações do servidor voltam no campo `actions` da
resposta de cada requisição.

### `POST /api/v1/ingest/frame`

Corpo: JPEG cru. Cabeçalhos:

| Cabeçalho | Descrição |
|---|---|
| `Content-Type` | `image/jpeg` |
| `X-Device-Id` | identificador do dispositivo |
| `X-Frame-Seq` | número de sequência |
| `X-Captured-At` | carimbo de captura em ms (opcional) |
| `X-Device-Token` | token, se configurado |

Resposta `200`:

```jsonc
{"device_id": "esp32cam-01", "sequence": 42,
 "detections": [
   {"label": "chair", "label_pt": "cadeira", "confidence": 0.87,
    "box": [120.0, 80.0, 310.0, 420.0], "direction": "center",
    "distance_m": 1.9, "track_id": 7}],
 "inference_ms": 38.2, "total_ms": 44.1, "dropped": false,
 "actions": [{"type": "speak", "text": "Uma cadeira, a frente..."}]}
```

`dropped: true` indica que o quadro foi descartado (sessão pausada, limite de
taxa atingido ou JPEG inválido) — não é erro.

### `POST /api/v1/ingest/telemetry`

```jsonc
// requisição
{"device_id": "esp32cam-01",
 "sensors": [{"sensor_id": "front", "distance_cm": 84.5}],
 "battery_pct": 87.0, "rssi_dbm": -58, "firmware": "0.1.0"}

// resposta
{"zone": "warning", "nearest_distance_m": 0.845, "actions": []}
```

Identificadores de sensor reconhecidos: `front`/`center` (à frente), `left`,
`right`. Outros são aceitos, mas não participam da fusão direcional.

### `POST /api/v1/ingest/button`

```jsonc
{"device_id": "esp32cam-01", "button": "start_stop", "action": "press"}
→ {"result": "stopped", "active": false}
```

### `POST /api/v1/ingest/audio`

Corpo: WAV PCM 16 bits mono. Retorna a intenção reconhecida e a resposta falada.
Devolve `503` quando o reconhecimento de fala não está disponível.

---

## Rotas de controle e monitoramento

| Rota | Descrição |
|---|---|
| `GET /api/v1/health` | motores ativos e estado dos dispositivos |
| `GET /api/v1/state` | estado e métricas por dispositivo |
| `POST /api/v1/command` | comando em texto (mesmo pipeline do comando de voz) |
| `POST /api/v1/devices/{id}/active?active=` | equivalente remoto do start/stop |
| `POST /api/v1/devices/{id}/volume?step=` | volume 0–10 |
| `WS /api/v1/events` | fluxo de eventos em tempo real (painel) |
| `GET /api/v1/media/preview.mjpg` | vídeo anotado (MJPEG) |
| `GET /api/v1/media/snapshot.jpg` | último quadro anotado |
| `GET /api/v1/media/tts?text=` | áudio sintetizado de uma frase |
| `GET /docs` | OpenAPI interativo |

### Eventos do fluxo `/api/v1/events`

```
device.connected      device.disconnected     device.button
vision.detections     proximity.reading       proximity.zone_changed
audio.speech_queued   audio.speech_played     audio.tone
speech.command_received   speech.command_handled
session.state         system.error
```

Formato:

```jsonc
{"type": "vision.detections", "device_id": "sim-01",
 "at_ms": 123456, "payload": { /* específico do evento */ }}
```

Os mesmos eventos são gravados em `server/var/events.jsonl` quando
`AVS_STORAGE__LOG_EVENTS=true`, e é desse arquivo que `training/analyze_events.py`
extrai as métricas dos ensaios.
