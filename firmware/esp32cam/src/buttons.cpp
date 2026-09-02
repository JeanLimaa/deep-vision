#include "buttons.h"

#if HAS_I2C_EXPANDER
#include <Wire.h>
#endif

namespace {

constexpr int8_t kPins[static_cast<uint8_t>(Button::Count)] = {
    PIN_BTN_POWER,
    PIN_BTN_START_STOP,
    PIN_BTN_VOLUME_UP,
    PIN_BTN_VOLUME_DOWN,
};

#if HAS_I2C_EXPANDER
// Cache da ultima leitura do expansor: uma transacao I2C por varredura, e nao
// uma por botao.
uint8_t g_expanderState = 0xFF;

uint8_t readExpander() {
  Wire.requestFrom(PCF8574_ADDRESS, 1);
  return Wire.available() ? Wire.read() : 0xFF;
}
#endif

}  // namespace

const char* buttonName(Button button) {
  switch (button) {
    case Button::Power:
      return "power";
    case Button::StartStop:
      return "start_stop";
    case Button::VolumeUp:
      return "volume_up";
    case Button::VolumeDown:
      return "volume_down";
    default:
      return "unknown";
  }
}

void ButtonPanel::begin() {
#if HAS_I2C_EXPANDER
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  g_expanderState = readExpander();
#else
  for (uint8_t i = 0; i < static_cast<uint8_t>(Button::Count); ++i) {
    if (kPins[i] >= 0) {
      pinMode(kPins[i], INPUT_PULLUP);
    }
  }
#endif
}

bool ButtonPanel::readRaw(uint8_t index) const {
#if HAS_I2C_EXPANDER
  // Botao ligado ao GND: bit em zero significa pressionado.
  return (g_expanderState & (1 << index)) == 0;
#else
  const int8_t pin = kPins[index];
  return pin >= 0 && digitalRead(pin) == LOW;
#endif
}

bool ButtonPanel::poll(ButtonEvent& event) {
  const uint32_t nowMs = millis();
  if (nowMs - _lastPollMs < 5) {
    return false;  // varrer mais rapido que isso so gasta CPU
  }
  _lastPollMs = nowMs;

#if HAS_I2C_EXPANDER
  g_expanderState = readExpander();
#endif

  for (uint8_t i = 0; i < static_cast<uint8_t>(Button::Count); ++i) {
    State& state = _states[i];
    const bool pressed = readRaw(i);

    if (pressed != state.pressed) {
      if (nowMs - state.changedAtMs < BUTTON_DEBOUNCE_MS) {
        continue;  // ruido mecanico do contato
      }
      state.changedAtMs = nowMs;
      state.pressed = pressed;

      // O evento sai na soltura, exceto quando o toque longo ja foi emitido.
      if (!pressed && !state.longFired) {
        event = {static_cast<Button>(i), false};
        return true;
      }
      if (!pressed) {
        state.longFired = false;
      }
      continue;
    }

    if (pressed && !state.longFired && nowMs - state.changedAtMs >= BUTTON_LONG_PRESS_MS) {
      state.longFired = true;
      event = {static_cast<Button>(i), true};
      return true;
    }
  }
  return false;
}
