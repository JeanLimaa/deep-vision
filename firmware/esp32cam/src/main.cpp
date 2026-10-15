/**
 * No de borda do sistema assistivo (Secao 5.3 do TCC).
 *
 * Responsabilidades, e nada alem disso: capturar imagem, medir distancia,
 * ler botoes, transmitir tudo ao servidor e reproduzir o retorno sonoro. A
 * inferencia acontece no servidor -- o ESP32 nao tem memoria nem capacidade de
 * calculo para o YOLO.
 *
 * O laco principal e cooperativo: nenhuma operacao bloqueia. A medida do
 * ultrassom e por interrupcao, a captura da camera acontece na cadencia do
 * temporizador e o WebSocket e servido a cada volta. E isso que permite manter
 * o alerta de colisao respondendo em dezenas de milissegundos mesmo enquanto um
 * quadro de 40 kB esta sendo transmitido.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <esp_sleep.h>
#include <esp_system.h>

#include "alerts.h"
#include "buttons.h"
#include "camera_module.h"
#include "config.h"
#include "microphone.h"
#include "pins.h"
#include "speaker.h"
#include "transport.h"
#include "ultrasonic.h"
#include "wifi_setup.h"

namespace {

Camera g_camera;
Ultrasonic g_sonar(PIN_SONAR_TRIG, PIN_SONAR_ECHO);
AlertBuzzer g_buzzer(PIN_BUZZER);
ButtonPanel g_buttons;
WifiSetup g_wifi;
Transport* g_transport = nullptr;
/// O transporte so e iniciado quando o endereco do servidor e conhecido.
bool g_transportStarted = false;

// Avisos sonoros do estado da rede: o usuario nao ve tela nem LED.
constexpr uint16_t kToneOnline[] = {1800, 2600};
constexpr uint16_t kToneOffline[] = {2600, 1800};
constexpr uint16_t kToneReset[] = {2600, 1800, 1200};
// Entrada e saida do mute: diferentes de todo o resto, para o usuario nunca
// ficar em duvida se a placa esta muda.
constexpr uint16_t kToneMuteOn[] = {700};
constexpr uint16_t kToneMuteOff[] = {1200, 1800, 2600};

/// Pedido enviado no toque simples; o servidor o trata como um comando de voz.
constexpr const char* kDescribeCommand = "o que tem na frente";

/// Estado operacional do dispositivo (o toque longo alterna ``streaming``).
bool g_streaming = true;
/// Mute total (toque duplo). Nao e salvo: a placa sempre liga com som.
bool g_muted = false;
uint32_t g_frameSequence = 0;
uint32_t g_lastFrameMs = 0;
uint32_t g_lastTelemetryMs = 0;
uint32_t g_lastStatusMs = 0;

void setStatusLed(bool on) {
#if PIN_STATUS_LED >= 0
  // A polaridade do LED embutido muda de placa para placa (ver pins.h).
#if STATUS_LED_ACTIVE_LOW
  digitalWrite(PIN_STATUS_LED, on ? LOW : HIGH);
#else
  digitalWrite(PIN_STATUS_LED, on ? HIGH : LOW);
#endif
#endif
}

bool serverLinkUp() { return g_transportStarted && g_transport->connected(); }

/// Acao vinda do servidor: fala sintetizada, bipe ou ajuste de configuracao.
void handleServerAction(const ServerAction& action) {
  if (action.type == "speak") {
    Serial.printf("[fala] %s\n", action.text.c_str());
    if (action.interrupt) {
      g_speaker.stop();
    }
    if (!action.tone.isEmpty()) {
      g_buzzer.chirp(action.tone == "pulse_fast" ? TONE_CRITICAL_HZ : TONE_WARNING_HZ);
    }
  } else if (action.type == "tone") {
    g_buzzer.chirp(action.tone == "pulse_fast" ? TONE_CRITICAL_HZ : TONE_WARNING_HZ);
  } else if (action.type == "volume") {
    const uint8_t step = static_cast<uint8_t>(action.priority);
    g_buzzer.setVolumeStep(step);
    g_speaker.setVolumeStep(step);
  }
}

/// Mute total: sem voz e sem bipe nenhum, inclusive o de obstaculo. A camera
/// para junto (economiza bateria) e o servidor deixa de narrar. Sair do mute
/// devolve tudo ao normal.
void setMuted(bool muted) {
  if (muted) {
    g_buzzer.playBlocking(kToneMuteOn, 1, 500, 0);
    g_buzzer.setMuted(true);
    g_speaker.stop();
    g_streaming = false;
  } else {
    g_buzzer.setMuted(false);
    g_streaming = true;
    g_buzzer.playBlocking(kToneMuteOff, 3);
  }
  g_muted = muted;
  Serial.printf("[mute] %s\n", muted ? "ligado" : "desligado");
  // Estado explicito (on/off), e nao alternancia: se uma mensagem se perder, a
  // proxima corrige em vez de inverter placa e servidor.
  if (serverLinkUp()) {
    g_transport->sendButton("mute", muted ? "on" : "off");
  }
}

/// Botao unico da ESP32-CAM, que sem microfone acumula as funcoes:
///   1 toque  -> "o que tem na frente?" (resposta falada pelo servidor)
///   2 toques -> mute total, liga e desliga
///   longo    -> pausa/retoma a voz; os bipes de obstaculo continuam
/// Com microfone, o toque simples grava um comando de voz no lugar da descricao.
void handleStartStop(const ButtonEvent& event) {
  if (event.taps >= 2) {
    setMuted(!g_muted);
    return;
  }
  if (g_muted) {
    return;  // mudo de verdade: so o toque duplo tira daqui
  }

  if (event.longPress) {
    g_streaming = !g_streaming;
    g_buzzer.chirp(g_streaming ? 2400 : 1400, 120);
    if (serverLinkUp()) {
      g_transport->sendButton("start_stop", "long_press");
    }
    return;
  }

  if (g_microphone.available()) {
    g_microphone.startCapture();
    g_buzzer.chirp(2400, 80);
    return;
  }
  if (!serverLinkUp()) {
    // Sem servidor nao vira resposta falada; o bipe grave avisa, para o usuario
    // nao ficar esperando.
    g_buzzer.chirp(600, 300);
    return;
  }
  g_transport->sendCommand(kDescribeCommand);
  g_buzzer.chirp(2000, 50);
}

void handleButton(const ButtonEvent& event) {
  if (event.veryLongPress) {
    if (event.button == Button::StartStop) {
      g_buzzer.setMuted(false);  // o aviso do reset precisa ser ouvido
      g_buzzer.playBlocking(kToneReset, 3, 180, 80);
      g_wifi.forgetAndRestart();
    }
    return;
  }

  Serial.printf("[botao] %s%s x%u\n", buttonName(event.button), event.longPress ? " (longo)" : "",
                event.taps);

  switch (event.button) {
    case Button::StartStop:
      handleStartStop(event);
      return;  // ja avisou o servidor do jeito certo para cada gesto

    case Button::VolumeUp:
    case Button::VolumeDown: {
      const int8_t delta = (event.button == Button::VolumeUp) ? 1 : -1;
      const uint8_t step = constrain(g_buzzer.volumeStep() + delta, 0, 10);
      g_buzzer.setVolumeStep(step);
      g_speaker.setVolumeStep(step);
      g_buzzer.chirp(1800 + step * 60, 70);
      break;
    }

    case Button::Power:
      if (event.longPress) {
        g_streaming = false;
        g_buzzer.silence();
        g_speaker.stop();
        Serial.println("[power] entrando em repouso");
        esp_deep_sleep_start();
      }
      break;

    default:
      break;
  }

  if (serverLinkUp()) {
    g_transport->sendButton(buttonName(event.button), event.longPress ? "long_press" : "press");
  }
}

void pumpCamera() {
  if (!g_streaming || !serverLinkUp()) {
    return;
  }
  const uint32_t nowMs = millis();
  if (nowMs - g_lastFrameMs < FRAME_INTERVAL_MS) {
    return;
  }
  g_lastFrameMs = nowMs;

  camera_fb_t* frame = g_camera.capture();
  if (frame == nullptr) {
    return;
  }
  g_transport->sendFrame(frame->buf, frame->len, ++g_frameSequence);
  g_camera.release(frame);
}

void pumpSonar() {
  // A medida roda sempre, mesmo com a transmissao pausada ou sem rede: e a
  // camada de seguranca que nao pode depender de nada externo.
  const bool updated = g_sonar.update();
  const bool zoneChanged = updated && g_buzzer.updateDistance(g_sonar.distanceCm());
  g_buzzer.update();

  const uint32_t nowMs = millis();
  const bool due = nowMs - g_lastTelemetryMs >= TELEMETRY_INTERVAL_MS;
  if (!zoneChanged && !due) {
    return;
  }
  g_lastTelemetryMs = nowMs;

  if (serverLinkUp()) {
    g_transport->sendTelemetry(g_sonar.distanceCm(), g_buzzer.zone(), g_buzzer.volumeStep());
  }
}

void pumpMicrophone() {
  const uint8_t* pcm = nullptr;
  size_t length = 0;
  if (g_microphone.poll(pcm, length) && serverLinkUp()) {
    Serial.printf("[mic] comando capturado (%u bytes)\n", static_cast<unsigned>(length));
    g_transport->sendAudio(pcm, length);
    g_buzzer.chirp(2000, 60);
  }
}

/// Nome legivel do motivo do ultimo reinicio -- distingue queda de tensao
/// (BROWNOUT) de travamento (WDT) e de reinicio normal, que e o que permite
/// saber se o problema e de alimentacao ou de software.
const char* resetReasonName() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "energia/reset";
    case ESP_RST_BROWNOUT: return "QUEDA DE TENSAO (alimentacao insuficiente)";
    case ESP_RST_PANIC: return "excecao do firmware";
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT: return "watchdog (travamento)";
    case ESP_RST_SW: return "reinicio pedido pelo firmware";
    case ESP_RST_DEEPSLEEP: return "retorno do repouso";
    default: return "desconhecido";
  }
}

/// A camera falha ao iniciar quando a tensao cai no pico de corrente. Tentar de
/// novo custa nada e recupera o video sem ninguem precisar reiniciar a placa.
void pumpCameraRetry() {
#if HAS_CAMERA
  static uint32_t lastAttemptMs = 0;
  if (g_camera.ready()) {
    return;
  }
  const uint32_t nowMs = millis();
  if (nowMs - lastAttemptMs < CAMERA_RETRY_INTERVAL_MS) {
    return;
  }
  lastAttemptMs = nowMs;
  Serial.println("[camera] tentando iniciar de novo...");
  g_camera.begin();
#endif
}

/// Sem Wi-Fi por tempo demais, reiniciar e a unica saida automatica. Enquanto
/// o radio estiver caido o alerta local do sonar continua funcionando.
void pumpNetworkWatchdog() {
  static uint32_t offlineSinceMs = 0;
  // Com o portal aberto a placa esta offline de proposito; quem decide o
  // reinicio e o tempo limite do portal (PORTAL_TIMEOUT_S).
  if (WiFi.status() == WL_CONNECTED || g_wifi.portalActive()) {
    offlineSinceMs = 0;
    return;
  }
  const uint32_t nowMs = millis();
  if (offlineSinceMs == 0) {
    offlineSinceMs = nowMs;
    return;
  }
  if (nowMs - offlineSinceMs >= WIFI_RESTART_AFTER_MS) {
    Serial.println("[wifi] sem rede ha tempo demais; reiniciando");
    Serial.flush();
    ESP.restart();
  }
}

/// Liga o transporte ao servidor quando o endereco aparece ou muda, e pede uma
/// nova busca quando o servidor encontrado some (o IP do PC pode ter mudado).
void pumpServer() {
  ServerEndpoint endpoint;
  if (g_wifi.takeEndpoint(endpoint)) {
    Serial.printf("[servidor] %s:%u\n", endpoint.host.c_str(), endpoint.port);
    g_transport->begin(endpoint.host, endpoint.port);
    g_transportStarted = true;
  }
  if (!g_transportStarted) {
    return;
  }

  // O servidor comeca cada conexao narrando. Se a placa estava muda quando o
  // enlace caiu, avisa de novo -- senao a voz voltaria sozinha no celular.
  static bool wasLinked = false;
  const bool linked = g_transport->connected();
  if (linked && !wasLinked && g_muted) {
    g_transport->sendButton("mute", "on");
  }
  wasLinked = linked;

  static uint32_t lostSinceMs = 0;
  if (g_transport->connected() || !g_wifi.connected()) {
    lostSinceMs = 0;
    return;
  }
  const uint32_t nowMs = millis();
  if (lostSinceMs == 0) {
    lostSinceMs = nowMs;
  } else if (nowMs - lostSinceMs >= SERVER_REDISCOVER_AFTER_MS) {
    g_wifi.requestRediscovery();
    lostSinceMs = nowMs;
  }
}

/// Bipes do estado da rede: dois agudos subindo ao conectar, descendo ao cair,
/// e, com o portal aberto, dois graves a cada 4 s ("esperando configuracao").
void pumpNetworkBeeps() {
  static bool wasOnline = false;
  const bool online = g_wifi.connected();
  if (online != wasOnline) {
    wasOnline = online;
    g_buzzer.playBlocking(online ? kToneOnline : kToneOffline, 2);
  }

  static uint32_t lastPortalBeepMs = 0;
  static bool secondBeepPending = false;
  if (!g_wifi.portalActive()) {
    secondBeepPending = false;
    return;
  }
  const uint32_t nowMs = millis();
  if (secondBeepPending && nowMs - lastPortalBeepMs >= 250) {
    g_buzzer.chirp(1200, 90);
    secondBeepPending = false;
  } else if (!secondBeepPending && nowMs - lastPortalBeepMs >= 4000) {
    g_buzzer.chirp(1200, 90);
    lastPortalBeepMs = nowMs;
    secondBeepPending = true;
  }
}

void logStatus() {
  const uint32_t nowMs = millis();
  if (nowMs - g_lastStatusMs < 5000) {
    return;
  }
  g_lastStatusMs = nowMs;
  Serial.printf("[status] quadros=%lu zona=%s dist=%.1fcm heap=%u rssi=%d\n",
                static_cast<unsigned long>(g_frameSequence), zoneName(g_buzzer.zone()),
                g_sonar.distanceCm(), ESP.getFreeHeap(), WiFi.RSSI());
  setStatusLed(serverLinkUp());
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n=== Sistema Assistivo | no de borda " FIRMWARE_VERSION " ===");

#if PIN_STATUS_LED >= 0
  pinMode(PIN_STATUS_LED, OUTPUT);
  setStatusLed(false);
#endif

#if defined(PIN_FLASH_LED)
  pinMode(PIN_FLASH_LED, OUTPUT);
  digitalWrite(PIN_FLASH_LED, LOW);
#endif

  Serial.printf("[boot] motivo do ultimo reinicio: %s\n", resetReasonName());

  g_buzzer.begin();
  g_sonar.begin();
  g_buttons.begin();
  g_speaker.begin();
  g_microphone.begin();

  // Wi-Fi primeiro, camera depois. A calibracao do radio e o maior pico de
  // corrente do boot; com a camera ainda desligada, esse pico acontece sozinho.
  // Fazer os dois juntos e o que derruba a placa quando a alimentacao esta no
  // limite -- e foi o que o log mostrou ("Brownout detector was triggered").
  // Sem rede salva (ou se ela falhar) abre o portal de configuracao, que nao
  // bloqueia: o sonar continua alertando enquanto alguem configura pelo celular.
  g_wifi.begin();
  delay(200);

  if (!g_camera.begin()) {
    // Sem camera o dispositivo ainda entrega a deteccao de obstaculos; nao ha
    // motivo para parar por completo -- e ``pumpCameraRetry`` segue tentando.
    Serial.println("[setup] camera indisponivel; seguindo apenas com o sonar");
  }

  g_transport = createTransport();
  g_transport->onAction(handleServerAction);

  g_buzzer.chirp(2600, 120);
  Serial.println("[setup] pronto");
}

void loop() {
  g_wifi.loop();
  pumpServer();
  if (g_transportStarted) {
    g_transport->loop();
  }

  ButtonEvent event;
  if (g_buttons.poll(event)) {
    handleButton(event);
  }

  pumpSonar();
  pumpCamera();
  pumpCameraRetry();
  pumpMicrophone();
  pumpNetworkWatchdog();
  pumpNetworkBeeps();
  logStatus();

  // Cede o processador para a pilha Wi-Fi; sem isso o watchdog dispara.
  delay(1);
}
