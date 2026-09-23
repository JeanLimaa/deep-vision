# Sistema Assistivo para Deficientes Visuais

Implementação do protótipo descrito no TCC: visão computacional (YOLO), sensores
ultrassônicos e ESP32, sob arquitetura **Edge-to-Cloud** — o dispositivo de borda
captura, o servidor infere, o usuário recebe retorno sonoro em português.

O projeto **roda por inteiro sem nenhum hardware**. Um dispositivo virtual
substitui o ESP32-CAM, e o retorno de áudio sai no alto-falante do computador ou
no navegador enquanto o fone do protótipo não está montado.

```
┌──────────────────────────────┐        Wi-Fi 802.11         ┌────────────────────────────────┐
│  Borda — ESP32-CAM           │  JPEG + telemetria (WS/HTTP)│  Servidor — FastAPI + YOLO     │
│                              │ ──────────────────────────▶ │                                │
│  câmera OV2640               │                             │  pré-processamento             │
│  HC-SR04 (alerta local)      │                             │  detecção → rastreio           │
│  botões, buzzer              │ ◀────────────────────────── │  direção + distância           │
│  alto-falante I2S (opcional) │  fala sintetizada + bipes   │  narração pt-BR + TTS          │
└──────────────────────────────┘                             └────────────────────────────────┘
```

## Início rápido (sem hardware)

Os comandos abaixo estão em sintaxe do **Prompt de Comando do Windows** (`cmd`),
que é onde o projeto foi desenvolvido. No PowerShell, troque `copy` por `cp`; no
Git Bash ou Linux, use `/` no lugar de `\` e `.venv/bin/python`.

```bat
:: 1. dependências do servidor (uma vez só)
cd server
uv venv --python 3.12
uv pip install -e ".[dev,audio,vision]"

:: 2. servidor
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

```bat
:: 3. em outro terminal, a partir da raiz do projeto: dispositivo virtual
server\.venv\Scripts\python simulator\virtual_device.py --sonar approach
```

> No `cmd`, caminhos usam contrabarra. `server/.venv/...` faz o interpretador ler
> `server` como nome de comando e falhar com "não é reconhecido como um comando".

Abra <http://localhost:8000> para o painel: vídeo anotado, objetos detectados,
zona de proximidade, log de falas e uma caixa para digitar comandos de voz.

Os pesos do YOLO são baixados sozinhos para `models/` na primeira execução. Se
o YOLO não carregar (dependências de visão ausentes, por exemplo), o servidor
**não sobe** e diz o motivo — antes ele caía em silêncio para um detector
simulado, cujas caixas sintéticas pareciam erros do YOLO. Para rodar sem o YOLO
de propósito, use `AVS_VISION__BACKEND=fake`: o vídeo sai com a tarja vermelha
"DETECTOR SIMULADO" e o selo do painel fica vermelho.

> Instalou dependências com o servidor no ar? **Reinicie o servidor** — o
> detector é carregado uma única vez, na partida.

## Estrutura

| Diretório | Conteúdo |
|---|---|
| `server/app/vision/` | detector (YOLO e simulado), pré-processamento, rastreio, estimativa de direção/distância |
| `server/app/proximity/` | zonas do sonar, histerese, fusão sonar + visão |
| `server/app/narration/` | frases pt-BR, decisão do que falar, fila com prioridade |
| `server/app/audio/` | TTS, destinos de áudio, serviço de saída |
| `server/app/speech/` | STT, intenções, comandos |
| `server/app/pipeline/` | orquestrador do fluxo completo |
| `server/app/api/` | rotas HTTP/WebSocket |
| `server/app/web/` | painel de monitoramento |
| `server/training/` | transferência de aprendizado, avaliação, análise dos ensaios |
| `simulator/` | ESP32-CAM virtual |
| `firmware/esp32cam/` | firmware do nó de borda (PlatformIO) |
| `docs/` | arquitetura, protocolo, hardware, roteiro de testes |

## Documentação

- [`docs/arquitetura.md`](docs/arquitetura.md) — decisões de projeto e o que mudou em relação ao artigo
- [`docs/protocolo.md`](docs/protocolo.md) — contrato entre borda e servidor
- [`docs/hardware.md`](docs/hardware.md) — montagem, pinagem e o que ainda falta comprar
- [`docs/testes.md`](docs/testes.md) — roteiro de validação e como levantar as métricas
- [`docs/configuracao.md`](docs/configuracao.md) — todas as variáveis de ambiente

## Testes

```bat
cd server
uv run pytest
uv run ruff check app
```

85 testes, sem hardware e sem modelo baixado.

## Aceleração por GPU (opcional)

Sem GPU o servidor usa o `yolo26s` na CPU (~85 ms por quadro). Com GPU, o
`AVS_VISION__DEVICE=auto` detecta a placa e sobe para um modelo maior e mais
preciso (`yolo26l` em CUDA, `yolo11l` em DirectML):

| Placa | Como instalar | modelo `l` por quadro |
|---|---|---|
| **NVIDIA** | `uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126` | ~42 ms (GTX 1650) |
| **AMD / Intel** (Windows) | `uv pip install -e ".[gpu-directml]"` | ~23 ms (RX 6600) |

Na NVIDIA, o build `cu126` exige **driver recente** (série 528 ou mais nova). Com
driver antigo o PyTorch não enxerga a placa e o servidor roda na CPU — o log de
partida avisa com `GPU NVIDIA encontrada, mas o CUDA nao iniciou`. Atualizar o
driver resolve; nenhum pacote precisa ser reinstalado.

Na AMD o modelo roda pelo ONNX Runtime com DirectML; o `.onnx` é exportado
sozinho do `.pt` na primeira execução. **Não instale o pacote `onnxruntime`
junto com o `onnxruntime-directml`**: os dois têm o mesmo nome de módulo, o de
CPU vence e a GPU deixa de ser usada sem erro nenhum — o log de partida mostra
`em directml` quando está certo.

Medições de acurácia por modelo, limiar e pré-processamento: `docs/configuracao.md`.

## Estado do hardware

| Componente | Situação |
|---|---|
| ESP32-CAM + OV2640 | implementado |
| HC-SR04 | implementado, com alerta local independente da rede |
| Botões e buzzer | implementados |
| Alto-falante I2S (MAX98357A) | **código pronto, hardware pendente** — áudio sai no PC/navegador |
| Microfone I2S | **código pronto, hardware pendente** — comandos por texto no painel |

Os dois pendentes estão atrás de flags de compilação (`HAS_I2S_SPEAKER`,
`HAS_I2S_MICROPHONE`) e de configuração do servidor (`AVS_AUDIO__SINKS`,
`AVS_SPEECH__SOURCE`). Quando as peças chegarem, nada mais precisa ser escrito.

## Testando aos poucos

Não é preciso ter tudo para começar. Cada etapa fecha uma dúvida:

| Etapa | Precisa | Prova que funciona |
|---|---|---|
| 1 | nada | servidor + simulador (todo o software) |
| 2 | ESP32 + HC-SR04 + resistores | ligação, medida, zonas, buzzer |
| 3 | + Wi-Fi | telemetria real chegando ao servidor |
| 4 | + câmera | pipeline completo de ponta a ponta |
| 5 | + amplificador e microfone | áudio e voz no próprio dispositivo |

A etapa 2 tem um firmware só dela — `pio run -e bench_sonar -t upload` — que
não usa Wi-Fi nem servidor: mede e imprime no monitor serial. Passo a passo,
critérios de aceite e lista de compras por prioridade em
[`docs/hardware.md`](docs/hardware.md).

## Firmware

```bat
cd firmware\esp32cam
copy include\secrets.example.h include\secrets.h
:: edite secrets.h com o SSID, a senha e o IP do PC que roda o servidor
pio run -e esp32cam -t upload
pio device monitor
```

| Ambiente | Placa | Escopo |
|---|---|---|
| `bench_sonar` | ESP32-CAM | só sonar + buzzer, sem rede — primeiro teste |
| `bench_sonar_devkit` | ESP32 DevKit | idem, para placa sem câmera |
| `esp32cam` | ESP32-CAM AI-Thinker | câmera + sonar + buzzer + 1 botão |
| `esp32cam_pcf` | ESP32-CAM + PCF8574 | acrescenta os 4 botões via I2C |
| `esp32devkit` | ESP32 DevKit | sem câmera; sonar + 4 botões |
| `esp32s3_sense` | XIAO ESP32-S3 Sense | tudo, incluindo áudio e microfone |

Todos os seis compilam (verificado com `pio run`).
