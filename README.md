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

```bash
# 1. dependências do servidor
cd server
uv venv --python 3.12
uv pip install -e ".[dev,audio,vision]"

# 2. servidor
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# 3. em outro terminal: dispositivo virtual
cd ..
server/.venv/Scripts/python simulator/virtual_device.py --sonar approach
```

Abra <http://localhost:8000> para o painel: vídeo anotado, objetos detectados,
zona de proximidade, log de falas e uma caixa para digitar comandos de voz.

Sem os pesos do YOLO ou sem GPU, o servidor cai sozinho para um detector
simulado e tudo continua funcionando — `AVS_VISION__BACKEND=fake` força esse modo.

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

```bash
cd server
uv run pytest          # 64 testes, sem hardware e sem modelo
uv run ruff check app  # estilo
```

## Aceleração por GPU (opcional)

A instalação padrão traz o PyTorch para CPU (~40 ms por quadro, suficiente para
os 8 quadros/s do projeto). Para usar a GPU:

```bash
cd server
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
```

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
