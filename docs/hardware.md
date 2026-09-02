# Hardware do nó de borda

## Um problema que precisa ser dito

A AI-Thinker ESP32-CAM **não comporta o protótipo completo descrito no TCC**.
Não é limitação de código: são pinos. A câmera OV2640 consome 16 GPIOs e o slot
microSD reserva outros seis. Desabilitando o cartão (o projeto não o usa),
sobram GPIO 2, 4, 12, 13, 14, 15 — e nem todos são utilizáveis:

| GPIO | Restrição |
|---|---|
| 0 | entra em modo de gravação se estiver em nível baixo no boot |
| 2 | strapping; aceita pull-up, mas não pode estar pressionado no boot |
| 4 | aciona o LED de flash (pico de 2 A); a carga distorce a leitura de um botão |
| 12 | strapping MTDI: **nível alto no boot impede a placa de iniciar** |
| 16 | usado como CS da PSRAM nos módulos de 4 MB |

Restam, na prática, três pinos confiáveis: 13, 14 e 15. O sensor ultrassônico
consome dois e o buzzer um. Não sobra nada para os quatro botões, para o
amplificador I2S (3 pinos) nem para o microfone (3 pinos).

## Três caminhos

O firmware traz um ambiente de compilação para cada um.

### `esp32cam` — o que dá para montar hoje

Câmera + 1 sensor ultrassônico + buzzer + 1 botão (start/stop). Os demais
controles ficam disponíveis pelo painel web e pelas rotas HTTP. É o suficiente
para validar todo o pipeline e produzir os resultados do trabalho.

| Função | GPIO | Observação |
|---|---|---|
| HC-SR04 TRIG | 14 | saída |
| HC-SR04 ECHO | 13 | **divisor resistivo obrigatório** (ver abaixo) |
| Buzzer passivo | 15 | PWM por LEDC, canal 0 |
| Botão start/stop | 2 | pull-up interno; não segurar no boot |
| LED de status | 33 | embutido, lógica invertida |

### `esp32cam_pcf` — os quatro botões, sem sacrificar o sensor

Acrescenta um expansor **PCF8574** no barramento I2C: quatro botões em dois
pinos. SDA em GPIO 2 e SCL em GPIO 3 (RX0) — com este perfil o monitor serial
fica somente de saída.

### `esp32s3_sense` — o protótipo completo

**XIAO ESP32-S3 Sense** (Seeed Studio). Traz PSRAM de 8 MB, microfone digital
embutido e GPIOs sobrando. É a única das três que acomoda câmera + sonar +
quatro botões + microfone + alto-falante ao mesmo tempo, e ainda é menor que a
AI-Thinker — o que atende melhor ao requisito de ergonomia do trabalho (peça
presa à camisa).

Se o orçamento permitir a troca, é a recomendação.

## Lista de materiais

### Já disponível

| Item | Qtd | Nota |
|---|---|---|
| ESP32-CAM AI-Thinker (OV2640) | 1 | verifique se o módulo tem PSRAM |
| Gravador FTDI / adaptador USB-serial | 1 | a placa não tem USB |
| HC-SR04 | 1 | alimentar em 5 V |
| Buzzer passivo | 1 | passivo, não ativo — o firmware gera a frequência |
| Botão táctil | 1–4 | conforme o perfil |
| Resistores 1 kΩ e 2 kΩ | 1 par | divisor de tensão do ECHO |
| Regulador 5 V (2 A) ou power bank | 1 | o pico de Wi-Fi passa de 500 mA |

### Pendente

| Item | Para quê |
|---|---|
| MAX98357A (amplificador I2S) | saída de áudio no dispositivo |
| Alto-falante 4 Ω / 3 W, ou fone com plugue P2 | reprodução da fala |
| INMP441 ou microfone PDM | captura dos comandos de voz |
| PCF8574 | expansor de botões (só no perfil `esp32cam_pcf`) |

Enquanto os dois primeiros não chegam, o áudio sai no alto-falante do
computador ou no navegador (`AVS_AUDIO__SINKS`), e os comandos de voz são
digitados no painel — atravessando exatamente o mesmo pipeline do servidor.

## Ligações

### HC-SR04 — o divisor é obrigatório

O sensor opera em 5 V e devolve **5 V no pino ECHO**. Os GPIOs do ESP32 são
tolerantes a 3,3 V; ligar direto danifica a entrada.

```
HC-SR04 ECHO ──┬── R1 (1 kΩ) ──┬── GPIO 13
               │                │
               └────────────────┴── R2 (2 kΩ) ── GND
```

`3,3 V ≈ 5 V × 2k / (1k + 2k)`. O TRIG pode ir direto: 3,3 V é reconhecido como
nível alto pelo sensor.

| HC-SR04 | ESP32-CAM |
|---|---|
| VCC | 5 V |
| GND | GND |
| TRIG | GPIO 14 |
| ECHO | GPIO 13 (via divisor) |

### Buzzer

```
GPIO 15 ── buzzer passivo ── GND
```

Para volume maior, um transistor NPN (BC547) com resistor de base de 1 kΩ. O
firmware controla o volume pelo *duty cycle* do PWM (0–50%).

### Botão

```
GPIO 2 ── botão ── GND        (pull-up interno, ativo em nível baixo)
```

### MAX98357A (quando chegar, perfil `esp32s3_sense`)

| MAX98357A | XIAO ESP32-S3 |
|---|---|
| VIN | 5 V |
| GND | GND |
| BCLK | GPIO 43 |
| LRC | GPIO 44 |
| DIN | GPIO 42 |
| GAIN | deixe flutuando (ganho de 9 dB) |

## Gravação

```bash
cd firmware/esp32cam
cp include/secrets.example.h include/secrets.h   # preencha Wi-Fi e IP do servidor
pio run -e esp32cam -t upload
pio device monitor
```

Na AI-Thinker é preciso ligar **GPIO 0 ao GND** antes de energizar para entrar
em modo de gravação, e remover o jumper depois. O `SERVER_HOST` em `secrets.h`
é o IP do computador que roda o servidor — confira com `ipconfig` e libere a
porta 8000 no firewall.

## Consumo e autonomia

| Estado | Corrente aproximada |
|---|---|
| Wi-Fi conectado, sem transmitir | 80–120 mA |
| Transmitindo vídeo a 8 quadros/s | 180–250 mA |
| Pico de transmissão Wi-Fi | até 500 mA |

Com uma bateria de 2000 mAh: cerca de 8 h em repouso e 4 h transmitindo. Uma
fonte que não sustente os picos causa reinícios aparentemente aleatórios — é a
causa mais comum de instabilidade nesta placa.
