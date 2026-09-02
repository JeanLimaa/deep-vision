/**
 * Zonas de proximidade e alerta sonoro local.
 *
 * Este e o unico caminho de seguranca que nao depende da rede: mesmo com o
 * Wi-Fi caido ou o servidor fora do ar, um obstaculo a menos de 80 cm produz
 * bipes imediatos. E a redundancia exigida pela Secao 5.4 do TCC.
 */

#pragma once

#include <Arduino.h>

#include "config.h"

enum class Zone : uint8_t { Safe = 0, Warning = 1, Critical = 2 };

const char* zoneName(Zone zone);

class AlertBuzzer {
 public:
  explicit AlertBuzzer(int8_t pin);

  void begin();

  /// Reclassifica a distancia e ajusta o padrao de bipes. Devolve true quando
  /// a zona mudou -- o chamador usa isso para notificar o servidor.
  bool updateDistance(float distanceCm);

  /// Deve ser chamado a cada iteracao de loop(); toca os bipes sem bloquear.
  void update();

  void setVolumeStep(uint8_t step) { _volumeStep = constrain(step, 0, 10); }
  uint8_t volumeStep() const { return _volumeStep; }

  Zone zone() const { return _zone; }

  /// Bipe unico de confirmacao (botao pressionado, sistema pronto).
  void chirp(uint16_t frequency = 2000, uint16_t durationMs = 60);

  void silence();

 private:
  Zone classify(float distanceCm) const;
  void toneOn(uint16_t frequency);
  void toneOff();

  int8_t _pin;
  Zone _zone = Zone::Safe;
  uint8_t _volumeStep = 7;

  uint32_t _lastToggleMs = 0;
  uint32_t _chirpUntilMs = 0;
  bool _toneActive = false;
};
