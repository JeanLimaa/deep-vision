# Configuração

Toda variável tem o prefixo `AVS_` e usa `__` para separar níveis. Pode vir do
ambiente ou de um arquivo `.env` na raiz do projeto ou em `server/`.

```bash
# exemplo: forçar o detector simulado e mandar o áudio só para o navegador
AVS_VISION__BACKEND=fake
AVS_AUDIO__SINKS=["dashboard"]
```

Listas usam sintaxe JSON.

## Servidor

| Variável | Padrão | Descrição |
|---|---|---|
| `AVS_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `AVS_SERVER__HOST` | `0.0.0.0` | endereço de escuta |
| `AVS_SERVER__PORT` | `8000` | porta |
| `AVS_SERVER__DEVICE_TOKEN` | vazio | token compartilhado; vazio desliga a autenticação |
| `AVS_SERVER__CORS_ORIGINS` | `["*"]` | origens permitidas |

## Visão

| Variável | Padrão | Descrição |
|---|---|---|
| `AVS_VISION__BACKEND` | `auto` | `yolo`, `fake` ou `auto` (tenta YOLO, cai para `fake`) |
| `AVS_VISION__MODEL_PATH` | `auto` | escolhe os pesos pelo dispositivo (ver abaixo); um caminho fixa o modelo, procurado em `models/` antes de baixar |
| `AVS_VISION__DEVICE` | `auto` | `cpu`, `cuda`, `mps` |
| `AVS_VISION__CONFIDENCE_THRESHOLD` | `0.50` | confiança mínima; abaixo de 0,45 aparecem falsos positivos com rótulo plausível |
| `AVS_VISION__IOU_THRESHOLD` | `0.45` | supressão de não-máximos |
| `AVS_VISION__MAX_DETECTIONS` | `20` | objetos por quadro |
| `AVS_VISION__INFERENCE_SIZE` | `640` | lado maior da entrada da rede (múltiplo de 32); é a resolução de treino do YOLO |
| `AVS_VISION__IGNORED_LABELS` | `[]` | classes que nunca são narradas |
| `AVS_VISION__MAX_INFERENCE_FPS` | `8.0` | teto de quadros inferidos por segundo; `0` = sem limite |
| `AVS_VISION__WARMUP_ON_STARTUP` | `true` | tira a primeira inferência do caminho crítico |

## Câmera (parâmetros ópticos)

| Variável | Padrão | Descrição |
|---|---|---|
| `AVS_CAMERA__HORIZONTAL_FOV_DEG` | `66.0` | campo de visão da lente; base do cálculo de distância |
| `AVS_CAMERA__CENTER_BAND` | `0.34` | fração central do quadro tratada como "à frente" |
| `AVS_CAMERA__MIN_ESTIMATED_DISTANCE_M` | `0.30` | abaixo disso a estimativa é descartada |
| `AVS_CAMERA__MAX_ESTIMATED_DISTANCE_M` | `12.0` | acima disso a estimativa é descartada |

Trocou a lente? Ajuste `HORIZONTAL_FOV_DEG` — é o parâmetro que mais afeta a
precisão da distância estimada.

### Escolha do modelo

`MODEL_PATH=auto` (padrão) resolve os pesos pelo dispositivo detectado:

| Dispositivo | Pesos | Motivo |
|---|---|---|
| `cpu` | `yolo11s.pt` | maior porte que ainda sustenta a taxa alvo |
| `cuda` | `yolo11l.pt` | 3× de folga sobre o teto, por 0,33 GB de VRAM |
| `mps` | `yolo11m.pt` | não medido; escolha conservadora |

Tempo por quadro medido nesta máquina — CPU de 6 threads e GeForce GTX 1650
(4 GB, compute 7.5), entrada 640×480, `imgsz=640`:

| Pesos | CPU | GPU | Taxa GPU | COCO mAP50-95 | VRAM |
|---|---|---|---|---|---|
| `yolo11n.pt` | 46 ms | 13 ms | 76 /s | 39,5 | |
| **`yolo11s.pt`** | **93 ms** | **17 ms** | **60 /s** | **47,0** | |
| `yolo11m.pt` | 226 ms | 34 ms | 29 /s | 51,5 | 0,22 GB |
| **`yolo11l.pt`** | 278 ms | **42 ms** | **24 /s** | **53,4** | 0,33 GB |
| `yolo11x.pt` | — | 79 ms | 13 /s | 54,7 | 0,64 GB |
| `yolov8s.pt` | 90 ms | — | | 44,9 | |
| `yolov8m.pt` | 207 ms | — | | 50,2 | |

A família 11 custa o mesmo da 8 em cada porte e acerta mais — não há razão para
ficar na 8.

O critério de escolha é **folga sobre `MAX_INFERENCE_FPS` (8 /s, a taxa que a
placa envia)**, não a taxa máxima possível. Em CPU o porte `m` fica em 4,4 /s,
abaixo do teto, e acumularia fila. Em GPU o `x` custaria o dobro do tempo do `l`
por 1,3 ponto de mAP, e derrubaria a folga de 3× para 1,6× — pouco para uma
demonstração ao vivo, em que a mesma GPU também desenha a tela.

VRAM não é o limite: mesmo o `x` reserva 0,64 GB dos 4,3 GB da placa.

**Build CUDA do PyTorch** (o índice padrão do PyPI instala a versão só-CPU):

```bat
cd server
uv pip install --index-url https://download.pytorch.org/whl/cu126 ^
  --reinstall-package torch --reinstall-package torchvision ^
  torch==2.13.0+cu126 torchvision==0.28.0+cu126
```

Para fixar um modelo, aponte o arquivo:

```
AVS_VISION__MODEL_PATH=yolo11n.pt     # máquina fraca
AVS_VISION__MODEL_PATH=models/best.pt # pesos refinados por training/train.py
```

### Se o detector errar o rótulo

Na ordem em que vale testar:

1. `INFERENCE_SIZE` abaixo de 640 — o quadro é reduzido antes da inferência
   (`pipeline/orchestrator.py`) *e* pelo próprio YOLO; abaixo da resolução de
   treino a queda é visível;
2. `CONFIDENCE_THRESHOLD` baixo — a 0,40 o `yolov8n` produz um fluxo de falsos
   positivos de um quadro só, com rótulos plausíveis (`toothbrush` para objeto
   alongado na mão, `cat` para textura de pele);
3. `AVS_PREPROCESS__CLAHE=false` — o realce de contraste existe para o cenário
   de baixa luminosidade da Seção 6; com iluminação boa ele adiciona contraste
   local que o modelo não viu no treino. Vale medir com e sem;
4. `AVS_TRACKING__MIN_HITS` — quantos quadros consecutivos o mesmo rótulo
   precisa aparecer na mesma região antes de virar fala. É o filtro de falso
   positivo mais barato do pipeline, e costuma valer mais que subir a
   confiança: a 8 quadros/s, o padrão de 4 são meio segundo, nada para um
   objeto real e muito para o ruído de um quadro só;
5. `AVS_VISION__IGNORED_LABELS` — silencia classes que só produzem ruído no
   ambiente de teste, sem mexer no detector.

### Fazendo o simulador valer como medida

Os padrões do `simulator/virtual_device.py` espelham
`firmware/esp32cam/include/config.h`: **640×480** (`FRAMESIZE_VGA`), **8
quadros/s** (`FRAME_INTERVAL_MS 125`) e qualidade JPEG equivalente à
`CAMERA_JPEG_QUALITY 12` do OV2640. Medida feita com outros valores não
descreve o que a placa vai entregar — se mudar um, registre no texto.

## Pré-processamento

| Variável | Padrão | Descrição |
|---|---|---|
| `AVS_PREPROCESS__ENABLED` | `true` | liga a cadeia inteira |
| `AVS_PREPROCESS__CLAHE` | `true` | equalização adaptativa; ajuda em baixa luminosidade |
| `AVS_PREPROCESS__CLAHE_CLIP_LIMIT` | `2.0` | agressividade da equalização |
| `AVS_PREPROCESS__DENOISE` | `false` | filtro bilateral; custa ~8 ms por quadro |

## Rastreio

| Variável | Padrão | Descrição |
|---|---|---|
| `AVS_TRACKING__IOU_MATCH_THRESHOLD` | `0.30` | sobreposição mínima para casar quadros |
| `AVS_TRACKING__MIN_HITS` | `2` | detecções antes de confirmar o objeto |
| `AVS_TRACKING__MAX_MISSES` | `8` | quadros sem ver antes de descartar o rastro |

## Proximidade

| Variável | Padrão | Descrição |
|---|---|---|
| `AVS_PROXIMITY__WARNING_DISTANCE_M` | `1.50` | fronteira SAFE → WARNING |
| `AVS_PROXIMITY__CRITICAL_DISTANCE_M` | `0.80` | fronteira WARNING → CRITICAL |
| `AVS_PROXIMITY__HYSTERESIS_M` | `0.10` | folga extra para sair de uma zona grave |
| `AVS_PROXIMITY__MAX_VALID_DISTANCE_M` | `4.00` | acima disso, considera "sem eco" |
| `AVS_PROXIMITY__READING_TTL_MS` | `1500` | validade de uma leitura de sonar |

Ao mudar os limiares, altere também `ZONE_WARNING_CM` e `ZONE_CRITICAL_CM` em
`firmware/esp32cam/include/config.h` — o alerta local usa os valores compilados
quando não há rede.

## Narração

| Variável | Padrão | Descrição |
|---|---|---|
| `AVS_NARRATION__AUTONOMOUS_NARRATION` | `true` | `false` = só responde a comandos |
| `AVS_NARRATION__OBJECT_COOLDOWN_MS` | `6000` | intervalo mínimo entre anúncios do mesmo objeto |
| `AVS_NARRATION__OBSTACLE_COOLDOWN_MS` | `2500` | idem para alertas de obstáculo |
| `AVS_NARRATION__CRITICAL_COOLDOWN_MS` | `1200` | idem para alertas críticos |
| `AVS_NARRATION__MAX_OBJECTS_PER_UTTERANCE` | `3` | objetos por frase de cena |
| `AVS_NARRATION__ANNOUNCE_DISTANCE` | `true` | inclui a distância estimada na fala |

Usuário reclamando de excesso de fala? Aumente os cooldowns e reduza
`MAX_OBJECTS_PER_UTTERANCE` antes de mexer em qualquer outra coisa.

## Síntese de voz

| Variável | Padrão | Descrição |
|---|---|---|
| `AVS_TTS__ENGINE` | `auto` | `pyttsx3` (offline), `gtts` (online), `null`, `auto` |
| `AVS_TTS__VOICE` | vazio | trecho do nome da voz; vazio busca uma voz pt-BR |
| `AVS_TTS__RATE_WPM` | `180` | velocidade da fala |
| `AVS_TTS__VOLUME` | `0.9` | volume da síntese (0–1) |
| `AVS_TTS__CACHE_SIZE` | `128` | frases mantidas em cache no disco |

## Áudio

| Variável | Padrão | Descrição |
|---|---|---|
| `AVS_AUDIO__SINKS` | `["dashboard","host"]` | `host`, `dashboard`, `device`, `null` |
| `AVS_AUDIO__DEFAULT_VOLUME_STEP` | `7` | volume inicial (0–10) |
| `AVS_AUDIO__MAX_QUEUE` | `16` | tamanho da fila de fala |

Acrescente `device` quando o alto-falante do protótipo estiver montado.

## Reconhecimento de fala

| Variável | Padrão | Descrição |
|---|---|---|
| `AVS_SPEECH__STT_ENGINE` | `auto` | `whisper`, `null`, `auto` |
| `AVS_SPEECH__WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium` |
| `AVS_SPEECH__WHISPER_COMPUTE_TYPE` | `int8` | `int8` em CPU, `float16` em GPU |
| `AVS_SPEECH__SOURCE` | `text` | `device` (microfone), `host`, `text` |

## Armazenamento

| Variável | Padrão | Descrição |
|---|---|---|
| `AVS_STORAGE__LOG_EVENTS` | `true` | grava `var/events.jsonl` |
| `AVS_STORAGE__SAVE_ANNOTATED_FRAMES` | `false` | salva quadros anotados em `var/snapshots/` |

Ligue `SAVE_ANNOTATED_FRAMES` para gerar as figuras do trabalho; desligue depois
— a escrita contínua enche o disco rápido.

## Perfis prontos

```bash
# Desenvolvimento sem hardware nenhum
AVS_VISION__BACKEND=fake
AVS_AUDIO__SINKS=["dashboard"]
AVS_TTS__ENGINE=null

# Ensaio de bancada com o protótipo
AVS_VISION__BACKEND=yolo
AVS_VISION__DEVICE=cuda
AVS_AUDIO__SINKS=["device","dashboard"]
AVS_STORAGE__SAVE_ANNOTATED_FRAMES=true

# Demonstração da banca (áudio no alto-falante da sala)
AVS_AUDIO__SINKS=["host","dashboard"]
AVS_NARRATION__OBJECT_COOLDOWN_MS=8000
```
