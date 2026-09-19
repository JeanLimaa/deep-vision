/**
 * Sensor ultrassonico HC-SR04, medicao nao bloqueante.
 *
 * A funcao pulseIn() do Arduino trava o nucleo por ate 25 ms enquanto espera o
 * eco. A 8 quadros por segundo isso comeria um quinto do tempo de CPU e
 * atrasaria o envio das imagens. Aqui o eco e medido por interrupcao nas duas
 * bordas do sinal, e loop() apenas colhe o resultado quando ele fica pronto.
 */

#pragma once

#include <Arduino.h>

#include "config.h"

class Ultrasonic {
 public:
  Ultrasonic(uint8_t trigPin, uint8_t echoPin);

  void begin();

  /// Deve ser chamado a cada iteracao de loop(). Devolve true quando uma nova
  /// medida ficou disponivel.
  bool update();

  /// Ultima distancia filtrada em centimetros; NAN quando nao houve eco.
  float distanceCm() const { return _filtered; }

  bool hasEcho() const { return !isnan(_filtered); }

 private:
  static void IRAM_ATTR onEchoChange();

  void startPing();
  void pushSample(float cm);
  float median();

  uint8_t _trigPin;
  uint8_t _echoPin;

  uint32_t _lastPingMs = 0;
  float _samples[SONAR_MEDIAN_WINDOW];
  uint8_t _sampleCount = 0;
  uint8_t _sampleIndex = 0;
  float _filtered = NAN;
  bool _awaitingEcho = false;  // disparo feito, eco ainda nao contabilizado

  // Estado compartilhado com a rotina de interrupcao.
  static Ultrasonic* _instance;
  volatile uint32_t _echoStartUs = 0;
  volatile uint32_t _echoWidthUs = 0;
  volatile bool _echoReady = false;
};
