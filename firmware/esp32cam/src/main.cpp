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

namespace {

Camera g_camera;
Ultrasonic g_sonar(PIN_SONAR_TRIG, PIN_SONAR_ECHO);
AlertBuzzer g_buzzer(PIN_BUZZER);
ButtonPanel g_buttons;
Transport* g_transport = nullptr;

/// Estado operacional do dispositivo (o botao start/stop alterna ``streaming``).
bool g_streaming = true;
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

void connectWiFi() {
  Serial.printf("[wifi] conectando em %s\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setTxPower(WIFI_TX_POWER);  // limita o pico de corrente (ver config.h)
  WiFi.setSleep(false);  // o modo de economia adiciona centenas de ms de latencia
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  const uint32_t startedAt = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startedAt < WIFI_CONNECT_TIMEOUT_MS) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[wifi] conectado | ip=%s rssi=%d dBm\n", WiFi.localIP().toString().c_str(),
                  WiFi.RSSI());
  } else {
    // Sem rede o dispositivo continua util: o alerta de obstaculo e local.
    Serial.println("[wifi] falhou; seguindo apenas com o alerta local de proximidade");
  }
}

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

void handleButton(const ButtonEvent& event) {
  Serial.printf("[botao] %s%s\n", buttonName(event.button), event.longPress ? " (longo)" : "");

  switch (event.button) {
    case Button::StartStop:
      if (g_microphone.available() && !event.longPress) {
        // Com microfone presente, o toque curto grava um comando de voz; o
        // toque longo continua alternando a transmissao.
        g_microphone.startCapture();
        g_buzzer.chirp(2400, 80);
        return;
      }
      g_streaming = !g_streaming;
      g_buzzer.chirp(g_streaming ? 2400 : 1400, 120);
      break;

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

  if (g_transport != nullptr) {
    g_transport->sendButton(buttonName(event.button), event.longPress);
  }
}

void pumpCamera() {
  if (!g_streaming || g_transport == nullptr || !g_transport->connected()) {
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

  if (g_transport != nullptr && g_transport->connected()) {
    g_transport->sendTelemetry(g_sonar.distanceCm(), g_buzzer.zone(), g_buzzer.volumeStep());
  }
}

void pumpMicrophone() {
  const uint8_t* pcm = nullptr;
  size_t length = 0;
  if (g_microphone.poll(pcm, length) && g_transport != nullptr) {
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
  if (WiFi.status() == WL_CONNECTED) {
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

void logStatus() {
  const uint32_t nowMs = millis();
  if (nowMs - g_lastStatusMs < 5000) {
    return;
  }
  g_lastStatusMs = nowMs;
  Serial.printf("[status] quadros=%lu zona=%s dist=%.1fcm heap=%u rssi=%d\n",
                static_cast<unsigned long>(g_frameSequence), zoneName(g_buzzer.zone()),
                g_sonar.distanceCm(), ESP.getFreeHeap(), WiFi.RSSI());
  setStatusLed(g_transport != nullptr && g_transport->connected());
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
  connectWiFi();
  delay(200);

  if (!g_camera.begin()) {
    // Sem camera o dispositivo ainda entrega a deteccao de obstaculos; nao ha
    // motivo para parar por completo -- e ``pumpCameraRetry`` segue tentando.
    Serial.println("[setup] camera indisponivel; seguindo apenas com o sonar");
  }

  g_transport = createTransport();
  g_transport->onAction(handleServerAction);
  g_transport->begin();

  g_buzzer.chirp(2600, 120);
  Serial.println("[setup] pronto");
}

void loop() {
  g_transport->loop();

  ButtonEvent event;
  if (g_buttons.poll(event)) {
    handleButton(event);
  }

  pumpSonar();
  pumpCamera();
  pumpCameraRetry();
  pumpMicrophone();
  pumpNetworkWatchdog();
  logStatus();

  // Cede o processador para a pilha Wi-Fi; sem isso o watchdog dispara.
  delay(1);
}
