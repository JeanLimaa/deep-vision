/**
 * Banco de teste do sensor ultrassonico -- primeiro contato com o hardware.
 *
 * Nao usa Wi-Fi, nao usa camera, nao depende do servidor. Faz uma coisa so:
 * medir distancia e mostrar, no monitor serial, o que o firmware completo vai
 * fazer com aquela medida. Serve para responder, antes de qualquer outra coisa,
 * se a ligacao esta correta e se o sensor mede o que deveria.
 *
 *   pio run -e bench_sonar -t upload
 *   pio device monitor
 *
 * Saida esperada (uma linha a cada 250 ms):
 *
 *   dist=  84.2 cm  zona=warning   [########----------]  bipe=lento
 *   dist=  47.9 cm  zona=critical  [####--------------]  bipe=rapido
 *   dist=   sem eco zona=safe      [------------------]  bipe=-
 *
 * Como validar: use uma fita metrica. Coloque um obstaculo plano (uma caixa,
 * uma parede) a 100 cm e confira a leitura. Erro tipico do HC-SR04 e de 1 a 3 cm.
 * Depois aproxime devagar e verifique se as fronteiras de 150 cm e 80 cm
 * disparam onde deveriam, e se a zona NAO fica oscilando sobre elas -- e a
 * histerese que esta sendo testada.
 *
 * Se aparecer "sem eco" o tempo todo, na ordem de probabilidade:
 *   1. TRIG e ECHO trocados;
 *   2. sensor alimentado em 3,3 V (o HC-SR04 precisa de 5 V);
 *   3. GND do sensor nao ligado ao GND do ESP32;
 *   4. divisor resistivo montado errado no ECHO.
 */

#include <Arduino.h>

#include "alerts.h"
#include "config.h"
#include "pins.h"
#include "ultrasonic.h"

namespace {

Ultrasonic g_sonar(PIN_SONAR_TRIG, PIN_SONAR_ECHO);
AlertBuzzer g_buzzer(PIN_BUZZER);

uint32_t g_lastPrintMs = 0;
uint32_t g_readings = 0;
uint32_t g_echoLost = 0;

/// Barra proporcional a distancia, ate 2 m -- ajuda a enxergar a variacao.
void printBar(float distanceCm) {
  constexpr int kWidth = 18;
  const int filled =
      isnan(distanceCm) ? 0 : constrain(static_cast<int>(distanceCm / 200.0f * kWidth), 0, kWidth);
  Serial.print('[');
  for (int i = 0; i < kWidth; ++i) {
    Serial.print(i < filled ? '#' : '-');
  }
  Serial.print(']');
}

const char* beepPattern(Zone zone) {
  switch (zone) {
    case Zone::Critical:
      return "rapido";
    case Zone::Warning:
      return "lento";
    default:
      return "-";
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(500);

  Serial.println("\n=== banco de teste: HC-SR04 + buzzer ===");
  Serial.printf("TRIG=GPIO%d  ECHO=GPIO%d  BUZZER=GPIO%d\n", PIN_SONAR_TRIG, PIN_SONAR_ECHO,
                PIN_BUZZER);
  Serial.printf("zonas: warning < %.0f cm   critical < %.0f cm   histerese %.0f cm\n\n",
                ZONE_WARNING_CM, ZONE_CRITICAL_CM, ZONE_HYSTERESIS_CM);

  g_sonar.begin();
  g_buzzer.begin();

  // Tres bipes de frequencia crescente: confirma que o buzzer esta ligado no
  // pino certo e que e passivo (um buzzer ativo emite sempre a mesma nota).
  Serial.println("testando o buzzer...");
  for (uint16_t frequency = 1500; frequency <= 2500; frequency += 500) {
    g_buzzer.chirp(frequency, 150);
    const uint32_t until = millis() + 250;
    while (millis() < until) {
      g_buzzer.update();
    }
  }
  Serial.println("pronto. aproxime um obstaculo do sensor.\n");
}

void loop() {
  if (g_sonar.update()) {
    g_readings++;
    if (!g_sonar.hasEcho()) {
      g_echoLost++;
    }
    g_buzzer.updateDistance(g_sonar.distanceCm());
  }
  g_buzzer.update();

  const uint32_t nowMs = millis();
  if (nowMs - g_lastPrintMs < 250) {
    return;
  }
  g_lastPrintMs = nowMs;

  const float distanceCm = g_sonar.distanceCm();
  if (isnan(distanceCm)) {
    Serial.print("dist=   sem eco ");
  } else {
    Serial.printf("dist=%7.1f cm ", distanceCm);
  }

  const Zone zone = g_buzzer.zone();
  Serial.printf(" zona=%-9s ", zoneName(zone));
  printBar(distanceCm);
  Serial.printf("  bipe=%s", beepPattern(zone));

  // A cada 40 linhas (~10 s), a taxa de perda de eco. Acima de uns 10% em
  // superficie plana indica problema de ligacao ou de alimentacao.
  if (g_readings > 0 && g_readings % 40 == 0) {
    Serial.printf("   | leituras=%lu  sem eco=%.0f%%", static_cast<unsigned long>(g_readings),
                  100.0f * g_echoLost / g_readings);
  }
  Serial.println();
}
