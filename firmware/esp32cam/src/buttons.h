/**
 * Botoes fisicos do prototipo (Secao 5 do TCC): liga/desliga, start/stop e
 * volume. Debounce por tempo e deteccao de toque longo.
 *
 * Duas origens possiveis, escolhidas em tempo de compilacao:
 *   - GPIO direto, com pull-up interno (placas com pinos sobrando);
 *   - expansor PCF8574 no barramento I2C, que gasta apenas dois pinos para os
 *     quatro botoes -- a saida para a AI-Thinker ESP32-CAM.
 */

#pragma once

#include <Arduino.h>

#include "config.h"
#include "pins.h"

enum class Button : uint8_t { Power = 0, StartStop = 1, VolumeUp = 2, VolumeDown = 3, Count = 4 };

const char* buttonName(Button button);

struct ButtonEvent {
  Button button;
  bool longPress;
};

class ButtonPanel {
 public:
  void begin();

  /// Le os botoes e devolve true quando ha um evento novo em ``event``.
  bool poll(ButtonEvent& event);

 private:
  bool readRaw(uint8_t index) const;

  struct State {
    bool pressed = false;
    bool longFired = false;
    uint32_t changedAtMs = 0;
  };

  State _states[static_cast<uint8_t>(Button::Count)];
  uint32_t _lastPollMs = 0;
};
