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
| `AVS_VISION__BACKEND` | `yolo` | `yolo` (se o modelo não carregar, o servidor **não sobe** e diz por quê), `fake` (detector simulado) ou `auto` (tenta YOLO e cai para o simulado, com tarja vermelha no vídeo) |
| `AVS_VISION__MODEL_PATH` | `auto` | escolhe os pesos pelo dispositivo (ver abaixo); um caminho fixa o modelo, procurado em `models/` e baixado para lá se faltar |
| `AVS_VISION__EXTRA_MODEL_PATHS` | `[]` | modelos que rodam ao lado do principal e só **acrescentam** classes que ele não tem (ex.: o modelo de degraus e portas treinado em `training/`) |
| `AVS_VISION__ALLOWED_LABELS` | perfil de mobilidade | classes que o detector pode reportar (lista em `app/vision/labels.py`); `[]` libera todas |
| `AVS_VISION__DEVICE` | `auto` | `cpu`, `cuda`, `mps`, `directml` (GPU AMD/Intel no Windows); `auto` tenta nessa ordem: CUDA, MPS, DirectML, CPU |
| `AVS_VISION__CONFIDENCE_THRESHOLD` | `0.35` | confiança mínima; o ruído de quadro único é cortado pelo rastreador (`MIN_HITS`) |
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
| `cpu` | `yolo26s.pt` | maior porte que ainda sustenta a taxa alvo |
| `cuda` | `yolo26l.pt` | ~3× de folga sobre o teto |
| `directml` | `yolo11l.pt` | GPU AMD/Intel via ONNX Runtime; RX 6600 faz o `l` em 23 ms (5× de folga). A exportação da família 26 não foi validada com DirectML |
| `mps` | `yolo26m.pt` | não medido; escolha conservadora |

Acurácia medida em **500 imagens do COCO val2017** — imagens que o modelo nunca
viu no treino (o procedimento está em `docs/testes.md`) — e tempo por quadro na
CPU desta máquina (Ryzen 5 5600H, 6 núcleos) e na GeForce GTX 1650 (4 GB,
driver 617), `imgsz=640`, lote 1:

| Pesos | mAP50-95 | mAP50 | CPU | GPU |
|---|---|---|---|---|
| `yolo11n.pt` | 39,3 | 54,4 | 38 ms | 13 ms |
| `yolo26n.pt` | 41,0 | 56,1 | 37 ms | |
| `yolo11s.pt` | 46,5 | 63,0 | 83 ms | 17 ms |
| **`yolo26s.pt`** | **49,0** | **66,0** | **85 ms** | 17 ms |
| `yolo11m.pt` | 52,1 | 69,3 | 274 ms | 34 ms |
| `yolo26m.pt` | 53,2 | 69,9 | 269 ms | |
| `yolo11l.pt` | 54,1 | 69,7 | 346 ms | 42 ms |
| **`yolo26l.pt`** | **56,7** | **74,0** | 332 ms | **41 ms** |

Com o Objects365 como modelo extra, `yolo26l` + `yolo26s-objv1-150` levam 58 ms
por quadro na GTX 1650 (~17 /s): ainda o dobro dos 8 quadros/s da placa.

A família 26 custa o mesmo da 11 em cada porte e acerta mais (+2,5 pontos no
`s`, +2,6 no `l`); também dispensa o NMS, a etapa que produzia duas caixas com
rótulos diferentes sobre o mesmo objeto. A 11 já tinha substituído a 8 pelo mesmo
motivo (47,0 contra 44,9 no porte `s`).

O critério de escolha é **folga sobre `MAX_INFERENCE_FPS` (8 /s, a taxa que a
placa envia)**, não a taxa máxima possível. Em CPU o porte `m` fica em ~3,7 /s,
abaixo do teto, e acumularia fila. Em GPU o `l` cabe com folga de ~3×.

**A GPU só é usada se o driver for novo o bastante para o build do PyTorch.** O
`torch` com CUDA 12.6 exige driver NVIDIA da série 528 ou mais nova; com driver
antigo, `torch.cuda.is_available()` devolve `False` e o servidor roda na CPU,
com o modelo menor. O log de partida agora diz isso explicitamente
(`GPU NVIDIA encontrada, mas o CUDA nao iniciou`). Na GTX 1650 desta máquina,
com o driver 512.74, é exatamente o que acontece: atualize o driver pelo site da
NVIDIA e reinicie o servidor.

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

### Medições que fixaram os padrões

Todas em 500 imagens do COCO val2017 (nunca vistas no treino), pelo **mesmo
caminho de um quadro real** — JPEG q88 da borda, decodificação e
pré-processamento do servidor —, reproduzíveis com
`server/training/pipeline_ablation.py`. IoU ≥ 0,5 e mesma classe.

> As medições anteriores usavam o COCO128, que é um recorte do conjunto de
> **treino** do COCO: o modelo já tinha visto aquelas imagens, e os números
> saíam inflados (yolo11s a conf 0,35: 55,7% de revocação no COCO128, contra
> 49,6% em imagens novas).

**Limiar de confiança** (`yolo26s`, perfil de mobilidade):

| conf | Precisão | Revocação | F1 | fantasmas | rótulo trocado |
|---|---|---|---|---|---|
| 0,25 | 75,6% | 58,2% | 0,657 | 234 | 39 |
| 0,30 | 78,9% | 56,0% | 0,655 | 182 | 34 |
| **0,35** | **81,9%** | **54,0%** | **0,651** | **139** | **29** |
| 0,40 | 84,2% | 51,4% | 0,639 | 117 | 24 |
| 0,45 | 86,8% | 49,6% | 0,632 | 81 | 20 |
| 0,50 | 88,8% | 47,3% | 0,617 | 58 | 18 |

"Fantasma" é detecção sem nenhum objeto real embaixo; "rótulo trocado", objeto
real com o nome de outra classe. Com as 80 classes liberadas, no mesmo limiar,
os rótulos trocados sobem de 29 para 52 — quase metade deles são trocas entre
classes que o perfil de mobilidade descarta (vaca → ovelha é a mais comum).

**Condições do protótipo** (conf 0,35, 80 classes):

| Variante | yolo11s P / R | yolo26s P / R |
|---|---|---|
| imagem original | 80,7% / 49,6% | 81,4% / 51,7% |
| escura + ruído de sensor | 77,3% / 31,9% | 78,4% / 31,6% |
| borrão de movimento (~11 px) | 79,5% / 29,3% | 79,7% / 29,5% |
| QVGA (placa sem PSRAM) | 82,7% / 42,1% | 83,8% / 43,0% |
| JPEG q40 em vez de q88 | 82,2% / 43,4% | — |

O transporte não é o gargalo: o JPEG q88 da borda custa ~2 pontos de revocação
contra a imagem original. O que derruba a detecção é a **imagem**: pouca luz e
borrão de quem anda tiram ~20 pontos, e nenhum modelo recupera isso — o `s` da
família 26 empata com o da 11 nessas condições. A saída está na câmera
(exposição curta, iluminação, `CAMERA_JPEG_QUALITY` alto) e em treinar com
imagens do próprio protótipo. O CLAHE, medido antes, piorou a revocação em todos
os cenários e segue desligado.

**Rastreador** (`yolo26s`, conf 0,35, 120 sequências de 12 quadros simulando
câmera presa ao peito; as mesmas detecções alimentam os dois rastreadores):

| | Precisão das caixas exibidas | Caixas fantasmas | Anúncios errados |
|---|---|---|---|
| antes (confirmação por acertos totais, 8 quadros de sobra) | 73,3% | 403 | 148 de 755 (20%) |
| agora (acertos seguidos, votação de rótulo, 2 quadros de sobra) | 79,2% | 204 | 99 de 632 (16%) |

O custo é revocação: 40,2% → 37,2% das caixas exibidas, porque o objeto que
some deixa de ser desenhado em 2 quadros em vez de 8.

### Mais classes sem treinar: modelos de outros datasets

O mAP publicado de cada família não se compara entre datasets — o YOLO26l tem
55,0 no COCO e o YOLOv8l tem 34,9 no Open Images, mas são provas diferentes
(600 classes, anotação hierárquica). A comparação justa é no **mesmo teste**:
500 imagens do COCO val, AP50 médio nas 37 classes de mobilidade que os três
datasets têm em comum, rótulos equivalentes mapeados (Person/Man/Woman → pessoa):

| Pesos | Treinados em | AP50 | CPU |
|---|---|---|---|
| **`yolo26l.pt`** | COCO (80 classes) | **70,2%** | 266 ms |
| `yolo26l-objv1-150.pt` | Objects365 (365) | 60,9% | 271 ms |
| `yolov8l-oiv7.pt` | Open Images V7 (601) | 35,8% | 424 ms |
| `yolo26s.pt` | COCO | 63,3% | 77 ms |
| `yolo26s-objv1-150.pt` | Objects365 | 56,0% | 82 ms |
| `yolov8s-oiv7.pt` | Open Images V7 | 30,3% | 94 ms |

O teste favorece o COCO (é o "terreno" dele), mas a conclusão prática vale: nas
classes comuns o modelo COCO é o melhor principal. O valor dos outros é o que
**só eles** têm:

- **Objects365** — poste de luz, cone, placa, lixeira, banqueta, cadeira de
  rodas, carrinho de bebê, extintor. Nas detecções mais confiantes em imagens de
  rua, praticamente todas estavam certas. Use como modelo extra, filtrado pelo
  perfil de mobilidade:

  ```
  AVS_VISION__EXTRA_MODEL_PATHS=["yolo26s-objv1-150.pt"]
  ```

  Custo: em CPU, `yolo26s` + extra ≈ 143 ms por quadro (~7 /s).
- **Open Images** — o único com porta e escada prontas, mas fraco: em 404 fotos
  de interiores, 23–33% de AP50 em portas, abaixo de um modelo treinado 50 min em
  CPU (42%). Degrau e escada exigem treino (`docs/testes.md`, seção 2.3).

### Se o detector errar ou deixar de ver

Na ordem em que vale conferir:

0. **O detector é o YOLO?** — o selo no topo do painel mostra os pesos e o
   dispositivo (`detector: yolo26s.pt em cpu`). Em vermelho, `SIMULADO`, as
   caixas são sintéticas e **não vêm da câmera**: pessoa, cadeira e carro
   passeando sobre qualquer imagem. Isso acontece com `AVS_VISION__BACKEND=auto`
   quando o YOLO não carrega — por exemplo, servidor iniciado antes de instalar o
   `torch` e nunca reiniciado. O padrão agora é `yolo`, que recusa subir nesse caso;
1. **Orientação da câmera** — uma imagem de cabeça para baixo praticamente
   zera a detecção de pessoas. Confira no painel e ajuste `CAMERA_VFLIP` em
   `firmware/esp32cam/include/config.h`;
2. **Luz** — a OV2640 no escuro entrega pouco sinal e muito ruído; nenhum
   ajuste do servidor recupera isso (ver a tabela acima);
3. `INFERENCE_SIZE` abaixo de 640 — abaixo da resolução de treino a queda é visível;
4. `CONFIDENCE_THRESHOLD` — subir reduz falsos positivos ao custo de perder
   objetos reais (tabela acima);
5. `AVS_TRACKING__MIN_HITS` — quantos quadros o mesmo rótulo precisa aparecer
   na mesma região antes de virar fala. É o filtro certo para o ruído de um
   quadro só (`cat`, `toothbrush`); alto demais, a câmera presa ao corpo
   balança e o rastro se perde antes de ser confirmado;
6. `AVS_VISION__IGNORED_LABELS` — silencia classes que só produzem ruído no
   ambiente de teste, sem mexer no detector.

### Fazendo o simulador valer como medida

Os padrões do `simulator/virtual_device.py` espelham
`firmware/esp32cam/include/config.h`: no máximo **640×480** (`FRAMESIZE_VGA`),
**8 quadros/s** (`FRAME_INTERVAL_MS 125`). A imagem é reduzida **mantendo a
proporção** — esticar uma webcam 16:9 para 4:3 custa 4 pontos de revocação.
Medida feita com outros valores não descreve o que a placa vai entregar — se
mudar um, registre no texto.

## Pré-processamento

| Variável | Padrão | Descrição |
|---|---|---|
| `AVS_PREPROCESS__ENABLED` | `true` | liga a cadeia inteira |
| `AVS_PREPROCESS__ROTATE_DEG` | `0` | rotação do quadro recebido (`0`, `90`, `180`, `270`); imagem de cabeça para baixo praticamente zera a detecção de pessoas |
| `AVS_PREPROCESS__CLAHE` | `false` | equalização adaptativa; medida, piorou a revocação em todos os cenários |
| `AVS_PREPROCESS__CLAHE_CLIP_LIMIT` | `2.0` | agressividade da equalização |
| `AVS_PREPROCESS__DENOISE` | `false` | filtro bilateral; custa ~8 ms por quadro |

## Rastreio

| Variável | Padrão | Descrição |
|---|---|---|
| `AVS_TRACKING__IOU_MATCH_THRESHOLD` | `0.30` | sobreposição mínima para casar quadros |
| `AVS_TRACKING__MIN_HITS` | `3` | detecções **seguidas** antes de confirmar o objeto; uma falha antes disso descarta o rastro |
| `AVS_TRACKING__MAX_MISSES` | `8` | quadros sem ver antes de descartar o rastro (preserva a identidade: o objeto que pisca não é reanunciado) |
| `AVS_TRACKING__COAST_FRAMES` | `2` | quadros sem ver em que o rastro ainda aparece no painel e na narração; depois some, mesmo vivo |

O rótulo de um rastro é decidido por votação ponderada pela confiança: a mesma
cadeira rotulada `chair` num quadro e `couch` no seguinte continua um objeto só,
com o nome mais votado. Um rastro só aceita detecção de outra classe quando as
caixas são praticamente iguais (IoU ≥ 0,6), para que uma pessoa sentada e a
cadeira embaixo dela continuem separadas.

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
| `AVS_STORAGE__SAVE_RAW_FRAMES` | `false` | salva o JPEG cru da placa, sem caixas, em `var/raw/` — material para anotar e medir precisão e revocação no ambiente real |
| `AVS_STORAGE__RAW_FRAMES_INTERVAL_MS` | `1000` | no máximo um quadro cru a cada intervalo (1 por segundo = 600 imagens em 10 min) |

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
