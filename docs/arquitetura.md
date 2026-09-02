# Arquitetura e decisões de projeto

Este documento registra **por que** o sistema é como é, e onde a implementação
se afasta do que está escrito no artigo — com a justificativa técnica de cada
desvio. Serve tanto de guia para quem for mexer no código quanto de insumo para
a redação da versão final do TCC.

## 1. Divisão Edge-to-Cloud

O ESP32 tem 520 KB de SRAM e nenhum acelerador de rede neural. Um YOLOv8n
quantizado ainda exige alguns MB e dezenas de GFLOPs por quadro — inviável.
A divisão adotada é a do artigo:

| Camada | Responsabilidade | Justificativa |
|---|---|---|
| Borda (ESP32) | captura, sensores, botões, alerta sonoro local | latência mínima, funciona sem rede |
| Servidor | inferência, rastreio, narração, síntese de voz | onde há CPU/GPU e memória |

O ponto que o artigo não detalha e que a implementação resolve: **o que acontece
quando a rede cai**. A resposta é a redundância local — a classificação de zonas
e o buzzer rodam inteiramente no firmware, com os mesmos limiares do servidor.
O usuário perde a descrição do ambiente, nunca o aviso de colisão.

## 2. Transporte: WebSocket em vez de POST por quadro

**O artigo descreve** requisições HTTP POST, uma por quadro JPEG.

**A implementação oferece as duas**, com WebSocket como padrão:

| | HTTP POST por quadro | WebSocket |
|---|---|---|
| Conexão | nova a cada quadro (handshake TCP) | uma só, persistente |
| Overhead por quadro | cabeçalhos HTTP + handshake | 17 bytes de cabeçalho binário |
| Canal de volta | só na resposta do POST | imediato, a qualquer momento |
| Alerta do servidor | espera o próximo quadro | chega na hora |

O canal de volta é o argumento decisivo: em modo HTTP, um aviso gerado logo
após a resposta de um POST só chega ao usuário no POST seguinte — até 125 ms de
atraso a 8 quadros/s, sem contar retransmissões. Para um sistema cuja função é
avisar de obstáculos, isso é um custo difícil de justificar.

O modo HTTP foi mantido íntegro (`POST /api/v1/ingest/frame`), porque é o que
está publicado e porque permite comparar os dois transportes na bancada — basta
`--transport http` no simulador ou `USE_WEBSOCKET 0` no firmware.

## 3. Zonas de proximidade: a faixa que faltava

O artigo define duas fronteiras: acima de 1,5 m o sistema apenas monitora,
abaixo de 0,8 m dispara alerta contínuo. Entre 0,8 m e 1,5 m nada é
especificado — na prática, isso produziria um salto de silêncio absoluto para
alarme contínuo.

A implementação nomeia essa faixa como **WARNING**, com bipe lento:

| Zona | Distância | Comportamento |
|---|---|---|
| SAFE | > 1,5 m | silêncio; o fluxo de vídeo segue normalmente |
| WARNING | 0,8 m – 1,5 m | bipe pulsante lento (700 ms) + descrição falada |
| CRITICAL | < 0,8 m | bipe rápido (220 ms), interrompe a fala em curso |

Duas proteções acompanham a mudança:

- **Histerese de 10 cm** — sair de uma zona mais grave exige folga extra. Sem
  isso, uma leitura oscilando sobre a fronteira faria o alarme piscar.
- **Filtro de mediana (5 amostras)** — o HC-SR04 produz ecos espúrios isolados
  em superfícies oblíquas. A mediana remove o pico sem o atraso de uma média
  longa. O filtro existe nas duas pontas: no firmware e no servidor.

## 4. O que a narração acrescenta ao YOLO

O YOLO devolve `chair 0.87 [x1,y1,x2,y2]`. Isso não é utilizável por alguém que
não enxerga. O caminho até a fala útil:

1. **Rastreio por IoU** — dá identidade estável aos objetos entre quadros. Sem
   isso o sistema repetiria "cadeira à frente" a 8 Hz.
2. **Direção** — a terça parte central do quadro é "à frente"; o resto é
   esquerda ou direita.
3. **Distância monocular** — modelo pinhole, `d = (H · f) / h`, com a altura
   real média `H` de cada classe. É uma estimativa grosseira, e por isso é
   substituída pela medida do sonar sempre que as duas fontes concordam.
4. **Fusão sonar + visão** — o sonar mede bem mas não sabe *o que* mediu; a
   câmera sabe o que vê mas estima mal a distância. Juntos: "cadeira à frente, a
   oitenta centímetros".
5. **Decisão de falar** — só anuncia objeto novo, que mudou de lado, ou que se
   aproximou o bastante para mudar de zona.
6. **Fila com prioridade** — CRITICAL > ALERT > resposta a comando > informação
   ambiente. Um alerta crítico esvazia a fila e interrompe a fala em curso.

O passo 5 é o mais importante do ponto de vista assistivo. A literatura citada
no próprio TCC (LANUTTI, 2019) aponta o abandono do dispositivo como principal
risco; verbosidade excessiva é um caminho direto para isso.

## 5. Interação por voz sem modelo de linguagem

O artigo menciona PLN para processar as intenções. A implementação usa um
casador determinístico por expressões regulares, e a escolha é deliberada:

- o vocabulário é fechado (doze comandos);
- a resposta é imediata, sem carregar modelo;
- o resultado é reproduzível — requisito para uma avaliação experimental;
- não depende de rede nem de GPU, coerente com a operação em rede local.

A transcrição (áudio → texto) sim usa modelo: `faster-whisper`, local. A
interface `SpeechToText` isola essa decisão; trocar por outro motor não afeta
nada além de uma classe.

## 6. Hardware ausente não bloqueia o software

Alto-falante e microfone ainda não estão montados. Em vez de deixar buracos no
código, cada um virou uma abstração com implementações intercambiáveis:

| Interface | Implementações |
|---|---|
| `AudioSink` | `host` (alto-falante do PC), `dashboard` (navegador), `device` (ESP32), `null` |
| `SpeechToText` | `faster-whisper`, `null` (comandos por texto) |
| `TextToSpeech` | `pyttsx3` (offline), `gtts` (online), `null` |
| `ObjectDetector` | `yolo`, `fake` (cena sintética coerente) |
| `Transport` (firmware) | WebSocket, HTTP |

O caminho de código exercitado hoje é o mesmo que rodará com o hardware
completo: o servidor **já sintetiza e já envia** o áudio ao dispositivo; um
ESP32 sem amplificador apenas descarta o quadro. Quando a peça chegar, muda-se
uma flag de compilação.

O detector `fake` merece nota: ele não é um mock trivial. Encena objetos que
entram no quadro, atravessam, se aproximam e saem — de modo que rastreio,
estimativa de distância, cooldowns e fila de fala são exercitados de verdade,
sem GPU e sem os pesos do modelo.

## 7. Concorrência no servidor

O servidor precisa receber vídeo continuamente, responder comandos e transmitir
eventos ao painel ao mesmo tempo. Regras seguidas em todo o pipeline:

- **Nada bloqueia o laço de eventos.** Inferência, síntese de voz, codificação
  JPEG e escrita em disco vão para threads (`asyncio.to_thread`).
- **Uma inferência por vez** (semáforo). O modelo é o gargalo; paralelizar só
  aumentaria a latência de cada quadro sem elevar a vazão.
- **Descartar, não enfileirar.** Quadros acima da taxa útil são descartados. Em
  vídeo ao vivo, quadro atrasado vale menos que quadro nenhum.
- **Assinante lento não trava ninguém.** O barramento de eventos descarta os
  eventos mais antigos da fila de quem não consome rápido o bastante.

O estado de percepção (rastreio, sonar, decisão de narrar) é **por dispositivo**;
a fila de fala e a saída de áudio são **globais**, porque o modelo do projeto é
de um usuário com um único fone. Vários dispositivos podem se conectar ao mesmo
servidor — útil para rodar protótipo e simulador lado a lado —, mas todos
compartilham o mesmo canal de áudio. Atender vários usuários simultâneos exigiria
tornar a fila de fala uma propriedade da sessão; é a única mudança estrutural
necessária para isso.

## 8. Medição de latência e relógios

O ESP32 não sincroniza com NTP, então o carimbo de tempo que ele envia está em
uma base diferente da do servidor. Subtrair um do outro produziria números sem
sentido — e números sem sentido em uma seção de resultados são piores que
ausência de dados.

A implementação detecta a divergência (diferença negativa ou acima de 60 s) e,
nesse caso, reporta apenas o tempo gasto dentro do servidor, omitindo a parcela
de rede. Para medir a latência de ponta a ponta de verdade, sincronize o
firmware por NTP ou use o simulador, que já compartilha o relógio da máquina.

## 9. Limitações conhecidas

- **Distância monocular** depende de o objeto estar inteiro no quadro e de a
  altura média da classe ser representativa. Erros de 30–50% são esperados; por
  isso a fala usa faixas ("cerca de dois metros") e não valores exatos.
- **Rastreio por IoU** não faz reidentificação: um objeto ocluído por vários
  quadros volta com identidade nova e é reanunciado.
- **Baixa luminosidade** continua degradando a detecção, como o próprio TCC
  relata. O CLAHE no canal de luminância ajuda, mas não resolve.
- **Classes urbanas** (degrau, buraco, poste, placa) exigem o dataset próprio.
  A infraestrutura de treino e os rótulos em português já estão prontos; falta
  a coleta e anotação das imagens.
