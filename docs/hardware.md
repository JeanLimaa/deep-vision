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

## Ordem recomendada de montagem

Não monte tudo de uma vez. Cada etapa fecha uma dúvida antes de a próxima
começar — quando algo falhar, você sabe onde olhar.

| Etapa | O que precisa | O que fica provado |
|---|---|---|
| 1 | nada | servidor + simulador (todo o software) |
| 2 | ESP32 + HC-SR04 + resistores | ligação, medida, zonas, buzzer |
| 3 | + Wi-Fi | telemetria real chegando ao servidor |
| 4 | + módulo com câmera | pipeline completo de ponta a ponta |
| 5 | + amplificador e microfone | áudio e voz no próprio dispositivo |

As etapas 1 a 3 não exigem nenhuma compra além do que já se tem.

## Etapa 2 — primeiro teste com hardware

Um ambiente de compilação existe só para isso: `bench_sonar`. Sem Wi-Fi, sem
câmera, sem servidor. Ele mede e imprime.

```bat
cd firmware\esp32cam
pio device list                  :: confirme a porta COM
pio run -e bench_sonar -t upload
pio device monitor
```

(`bench_sonar` usa a pinagem da AI-Thinker; para um ESP32 comum sem camera o
ambiente equivalente e `bench_sonar_devkit`.)

```
=== banco de teste: HC-SR04 + buzzer ===
TRIG=GPIO14  ECHO=GPIO13  BUZZER=GPIO15
zonas: warning < 150 cm   critical < 80 cm   histerese 10 cm

dist=  184.3 cm  zona=safe      [################--]  bipe=-
dist=   84.2 cm  zona=warning   [########----------]  bipe=lento
dist=   47.9 cm  zona=critical  [####--------------]  bipe=rapido
```

**O que verificar, com fita métrica:**

1. obstáculo plano a 100 cm → leitura entre 97 e 103 cm (erro típico do
   HC-SR04 é de 1 a 3 cm);
2. aproximando devagar, a zona muda em 150 cm e em 80 cm;
3. parando exatamente sobre uma fronteira, a zona **não** fica oscilando — é a
   histerese sendo testada;
4. a taxa de "sem eco", impressa a cada 10 s, fica abaixo de 10% em superfície
   plana.

**Se der "sem eco" o tempo todo**, na ordem de probabilidade: TRIG e ECHO
trocados; sensor alimentado em 3,3 V em vez de 5 V; GND não ligado em comum;
divisor resistivo montado errado.

O buzzer é opcional nesta etapa — sem ele, a coluna `bipe=` ainda mostra o que
o firmware faria. Se você tiver um, confirme que é **passivo**: um buzzer ativo
emite sempre a mesma nota, então os três bipes de teste sairão iguais.

## Etapas 3 e 4 — sistema completo

Trocando o ambiente, o mesmo hardware passa a conversar com o servidor:

```bat
copy include\secrets.example.h include\secrets.h
:: edite secrets.h: SSID, senha e IP do PC que roda o servidor
pio run -e esp32cam -t upload    :: ou esp32devkit, se a placa não tiver câmera
pio device monitor
```

Com a ESP32-CAM, isso já é o pipeline inteiro: vídeo + sonar saindo do
dispositivo real. Sem câmera (`esp32devkit`), chega só a telemetria do sonar e o
vídeo continua vindo do simulador, em paralelo — são duas sessões independentes
no mesmo servidor.

O que observar no painel: a zona mudando conforme o sonar, os objetos detectados
com direção e distância, e as falas aparecendo no log.

Se o `[wifi] falhou` aparecer no monitor serial, o dispositivo continua útil — o
alerta de obstáculo é local e não depende de rede. Confira o IP em `secrets.h`
(`ipconfig` no PC), que o PC e o ESP32 estão na mesma rede, e libere a porta 8000
no firewall do Windows.

## Placas suportadas

O firmware traz um ambiente de compilação para cada uma.

### `esp32devkit` — ESP32 comum, sem câmera

A placa de bancada. Sobram GPIOs, então os quatro botões vão direto, sem
expansor. Entrega toda a camada de segurança do projeto; o vídeo fica por conta
do simulador.

| Função | GPIO | Observação |
|---|---|---|
| HC-SR04 TRIG | 5 | saída |
| HC-SR04 ECHO | 18 | **divisor resistivo obrigatório** |
| Buzzer passivo | 19 | PWM por LEDC |
| Botão start/stop | 21 | pull-up interno |
| Botão volume + | 22 | |
| Botão volume − | 23 | |
| Botão power | 25 | toque longo desliga |
| LED de status | 2 | embutido, acende em nível alto |

Pinos evitados: 0, 2, 15 (strapping), 6–11 (flash interna), 34–39 (só entrada,
sem pull-up).

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

### Etapas 1 a 3 — o que basta para começar

| Item | Qtd | Nota |
|---|---|---|
| ESP32-CAM + placa-mãe ESP32-CAM-MB | 1 | a MB dispensa o gravador FTDI |
| HC-SR04 | 1 | alimentar em 5 V, nunca em 3,3 V |
| Protoboard e jumpers | — | macho-fêmea para o sensor |
| Resistores para o divisor | 1 par | proporção 1:2, entre 1 kΩ e 10 kΩ |
| Cabo USB de dados | 1 | cuidado: cabo "só de carga" não enumera a porta |

Com a ESP32-CAM + MB isso já fecha as **quatro** primeiras etapas, incluindo o
pipeline de visão completo. Sem buzzer o teste continua válido — a zona aparece
no monitor serial e o servidor fala pelo computador.

### Falta comprar

Em ordem de impacto no trabalho:

| Prioridade | Item | Preço típico | Destrava |
|---|---|---|---|
| **1** | Buzzer **passivo** 5 V | R$ 3 | alerta local audível — o mais barato e o que mais falta |
| 2 | Botão táctil 6×6 mm | R$ 1 | start/stop no dispositivo |
| 3 | MAX98357A (amplificador I2S) | R$ 25 | etapa 5: fala no próprio dispositivo |
| 3 | Alto-falante 4 Ω / 3 W, ou fone com plugue P2 | R$ 10 | reprodução da voz |
| 3 | INMP441 (microfone I2S) | R$ 30 | comandos de voz no dispositivo |
| 4 | Power bank ou bateria 18650 + TP4056 | R$ 40 | teste em movimento, fora da bancada |
| 4 | PCF8574 (só se ficar na AI-Thinker) | R$ 8 | os quatro botões sem sacrificar o sensor |

| 4 | XIAO ESP32-S3 Sense (troca de placa) | R$ 130 | protótipo completo numa placa só |

**Sobre a troca de placa.** A AI-Thinker já entrega o resultado principal do
trabalho — visão, sonar, zonas, narração. O que ela não comporta são os quatro
botões e o áudio no próprio dispositivo, pela falta de pinos explicada no início
deste documento. A XIAO ESP32-S3 Sense resolve os dois de uma vez e ainda traz
microfone embutido, substituindo três compras (PCF8574, INMP441 e parte da
fiação). Vale a pena **se** você quiser demonstrar a interação por voz rodando na
peça física; para os resultados do TCC, não é obrigatória.

Nada da prioridade 3 em diante bloqueia o trabalho: enquanto esses itens não chegam, o
áudio sai no computador ou no navegador (`AVS_AUDIO__SINKS`) e os comandos de
voz são digitados no painel, atravessando exatamente o mesmo pipeline do
servidor.

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

Qualquer par na proporção 1:2 serve — o que estiver na sua caixa de resistores:

| R1 (para o ECHO) | R2 (para o GND) | Tensão no GPIO |
|---|---|---|
| 1 kΩ | 2 kΩ | 3,33 V |
| 1 kΩ | 2,2 kΩ | 3,44 V |
| 2,2 kΩ | 4,7 kΩ | 3,40 V |
| 10 kΩ | 20 kΩ | 3,33 V |

Acima de ~20 kΩ o divisor fica lento demais para o pulso do eco; abaixo de
~500 Ω desperdiça corrente. Fique na faixa de 1 kΩ a 10 kΩ.

| HC-SR04 | ESP32-CAM (via MB) | ESP32 DevKit |
|---|---|---|
| VCC | 5V | VIN / 5 V |
| GND | GND | GND |
| TRIG | GPIO 14 | GPIO 5 |
| ECHO | GPIO 13 (via divisor) | GPIO 18 (via divisor) |

Na ESP32-CAM-MB os pinos `IO12`, `IO13`, `IO14`, `IO15`, `IO2`, `IO4`, `5V` e
`GND` saem na barra de pinos da própria placa-mãe — dá para espetar os jumpers
direto nela, sem soldar nada na ESP32-CAM.

No DevKit v1, o pino de 5 V é o `VIN`, e ele só entrega 5 V quando a placa está
alimentada pela USB ou por fonte externa — nunca use o pino de 3,3 V.

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

```bat
cd firmware\esp32cam
copy include\secrets.example.h include\secrets.h
pio run -e esp32cam -t upload
pio device monitor
```

O `SERVER_HOST` em `secrets.h` é o IP do computador que roda o servidor —
confira com `ipconfig` e libere a porta 8000 no firewall do Windows.

### Com a placa-mãe ESP32-CAM-MB

É o caminho fácil: a MB traz um conversor USB-serial CH340 e cuida do GPIO 0
sozinha. Encaixe a ESP32-CAM na MB, ligue o cabo USB e grave — sem FTDI, sem
jumper.

- **Driver CH340**: se a placa não aparecer como porta COM no Windows, instale o
  driver do CH340 (o Windows nem sempre o traz). Confira com `pio device list`.
- Se a gravação falhar logo no início, segure o botão **BOOT/IO0** da MB, encoste
  rapidamente em **RST** e solte o BOOT quando o `Connecting...` aparecer. Nas MB
  mais novas isso é automático.
- Se der erro de sincronismo, acrescente `upload_speed = 115200` ao ambiente
  `esp32cam` no `platformio.ini`.
- **Use um cabo USB de dados.** Cabo "só de carga" não enumera a porta, e o
  sintoma é idêntico ao de driver faltando.

### Sem a placa-mãe (só a ESP32-CAM e um FTDI)

Ligue `U0T`→RX, `U0R`→TX, `5V`→5V, `GND`→GND, e **GPIO 0 ao GND** antes de
energizar. Remova o jumper do GPIO 0 e reinicie a placa depois de gravar.

## Consumo e autonomia

| Estado | Corrente aproximada |
|---|---|
| Wi-Fi conectado, sem transmitir | 80–120 mA |
| Transmitindo vídeo a 8 quadros/s | 180–250 mA |
| Pico de transmissão Wi-Fi | até 500 mA |

Com uma bateria de 2000 mAh: cerca de 8 h em repouso e 4 h transmitindo. Uma
fonte que não sustente os picos causa reinícios aparentemente aleatórios — é a
causa mais comum de instabilidade nesta placa.
