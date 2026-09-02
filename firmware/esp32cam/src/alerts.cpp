#include "alerts.h"

namespace {
// Canal LEDC dedicado ao buzzer; os demais ficam livres para outros perifericos.
constexpr uint8_t kBuzzerChannel = 0;
constexpr uint8_t kBuzzerResolution = 8;
}  // namespace

const char* zoneName(Zone zone) {
  switch (zone) {
    case Zone::Critical:
      return "critical";
    case Zone::Warning:
      return "warning";
    default:
      return "safe";
  }
}

AlertBuzzer::AlertBuzzer(int8_t pin) : _pin(pin) {}

void AlertBuzzer::begin() {
  if (_pin < 0) {
    return;
  }
  ledcSetup(kBuzzerChannel, 2000, kBuzzerResolution);
  ledcAttachPin(_pin, kBuzzerChannel);
  ledcWrite(kBuzzerChannel, 0);
}

Zone AlertBuzzer::classify(float distanceCm) const {
  if (isnan(distanceCm)) {
    return Zone::Safe;
  }

  // Histerese: sair de uma zona mais grave exige folga extra, senao uma leitura
  // oscilando sobre o limite faria o bipe piscar sem parar.
  float criticalLimit = ZONE_CRITICAL_CM;
  float warningLimit = ZONE_WARNING_CM;
  if (_zone == Zone::Critical) {
    criticalLimit += ZONE_HYSTERESIS_CM;
  }
  if (_zone != Zone::Safe) {
    warningLimit += ZONE_HYSTERESIS_CM;
  }

  if (distanceCm < criticalLimit) {
    return Zone::Critical;
  }
  if (distanceCm < warningLimit) {
    return Zone::Warning;
  }
  return Zone::Safe;
}

bool AlertBuzzer::updateDistance(float distanceCm) {
  const Zone next = classify(distanceCm);
  if (next == _zone) {
    return false;
  }
  _zone = next;
  if (_zone == Zone::Safe) {
    toneOff();
  }
  _lastToggleMs = 0;  // dispara o proximo bipe imediatamente
  return true;
}

void AlertBuzzer::update() {
  if (_pin < 0) {
    return;
  }

  const uint32_t nowMs = millis();

  if (_chirpUntilMs != 0) {
    if (nowMs >= _chirpUntilMs) {
      _chirpUntilMs = 0;
      toneOff();
    }
    return;  // o bipe de confirmacao tem prioridade sobre o padrao de zona
  }

  if (_zone == Zone::Safe || _volumeStep == 0) {
    if (_toneActive) {
      toneOff();
    }
    return;
  }

  const uint16_t period =
      (_zone == Zone::Critical) ? TONE_CRITICAL_PERIOD_MS : TONE_WARNING_PERIOD_MS;
  const uint16_t frequency = (_zone == Zone::Critical) ? TONE_CRITICAL_HZ : TONE_WARNING_HZ;

  if (_toneActive) {
    if (nowMs - _lastToggleMs >= TONE_BEEP_MS) {
      toneOff();
      _lastToggleMs = nowMs;
    }
  } else if (nowMs - _lastToggleMs >= period) {
    toneOn(frequency);
    _lastToggleMs = nowMs;
  }
}

void AlertBuzzer::chirp(uint16_t frequency, uint16_t durationMs) {
  if (_pin < 0 || _volumeStep == 0) {
    return;
  }
  toneOn(frequency);
  _chirpUntilMs = millis() + durationMs;
}

void AlertBuzzer::silence() {
  _chirpUntilMs = 0;
  toneOff();
}

void AlertBuzzer::toneOn(uint16_t frequency) {
  if (_pin < 0) {
    return;
  }
  ledcWriteTone(kBuzzerChannel, frequency);
  // Duty proporcional ao volume. 50% e o maximo util em buzzer passivo; acima
  // disso a forma de onda deixa de ser simetrica e o som distorce.
  const uint8_t duty = static_cast<uint8_t>((_volumeStep * 128) / 10);
  ledcWrite(kBuzzerChannel, duty);
  _toneActive = true;
}

void AlertBuzzer::toneOff() {
  if (_pin < 0) {
    return;
  }
  ledcWrite(kBuzzerChannel, 0);
  _toneActive = false;
}
