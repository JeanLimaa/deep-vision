#include "ultrasonic.h"

Ultrasonic* Ultrasonic::_instance = nullptr;

Ultrasonic::Ultrasonic(uint8_t trigPin, uint8_t echoPin)
    : _trigPin(trigPin), _echoPin(echoPin) {}

void Ultrasonic::begin() {
  pinMode(_trigPin, OUTPUT);
  // Pull-down: com o sensor desconectado o pino flutuaria e o ruido dispararia
  // a interrupcao, gerando distancias falsas. Com o divisor ligado, o resistor
  // para o GND ja domina e o pull-down interno (~45 kOhm) nao altera a tensao.
  pinMode(_echoPin, INPUT_PULLDOWN);
  digitalWrite(_trigPin, LOW);

  _instance = this;
  attachInterrupt(digitalPinToInterrupt(_echoPin), onEchoChange, CHANGE);

  for (uint8_t i = 0; i < SONAR_MEDIAN_WINDOW; ++i) {
    _samples[i] = NAN;
  }
}

void IRAM_ATTR Ultrasonic::onEchoChange() {
  Ultrasonic* self = _instance;
  if (self == nullptr) {
    return;
  }
  if (digitalRead(self->_echoPin) == HIGH) {
    self->_echoStartUs = micros();
  } else if (self->_echoStartUs != 0) {
    self->_echoWidthUs = micros() - self->_echoStartUs;
    self->_echoStartUs = 0;
    self->_echoReady = true;
  }
}

void Ultrasonic::startPing() {
  _echoReady = false;
  _echoStartUs = 0;
  _awaitingEcho = true;
  digitalWrite(_trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(_trigPin, HIGH);
  delayMicroseconds(10);  // pulso de disparo exigido pelo HC-SR04
  digitalWrite(_trigPin, LOW);
}

bool Ultrasonic::update() {
  const uint32_t nowMs = millis();

  if (_echoReady) {
    _echoReady = false;
    // Eco que chega depois do timeout ja foi contado como "sem eco" abaixo; o
    // HC-SR04 mantem o ECHO alto por ~38 ms quando nada reflete.
    if (!_awaitingEcho) {
      return false;
    }
    _awaitingEcho = false;
    const uint32_t widthUs = _echoWidthUs;
    // Ecos absurdamente longos vem de superficies obliquas ou de reflexao
    // multipla; sao descartados como "sem eco".
    const float cm =
        (widthUs == 0 || widthUs > SONAR_TIMEOUT_US) ? NAN : (widthUs * SOUND_SPEED_CM_PER_US) / 2.0f;
    pushSample(cm);
    return true;
  }

  // Sem eco dentro do timeout: obstaculo fora de alcance. Uma unica amostra
  // por disparo -- sem a flag, cada volta do loop() empurraria outro NAN e a
  // janela da mediana nunca guardaria uma medida valida.
  if (_awaitingEcho && (nowMs - _lastPingMs) > (SONAR_TIMEOUT_US / 1000UL + 5UL)) {
    _awaitingEcho = false;
    pushSample(NAN);
    return true;
  }

  if (nowMs - _lastPingMs >= SONAR_INTERVAL_MS) {
    _lastPingMs = nowMs;
    startPing();
  }
  return false;
}

void Ultrasonic::pushSample(float cm) {
  _samples[_sampleIndex] = cm;
  _sampleIndex = (_sampleIndex + 1) % SONAR_MEDIAN_WINDOW;
  if (_sampleCount < SONAR_MEDIAN_WINDOW) {
    _sampleCount++;
  }
  _filtered = median();
}

float Ultrasonic::median() {
  float valid[SONAR_MEDIAN_WINDOW];
  uint8_t count = 0;
  for (uint8_t i = 0; i < _sampleCount; ++i) {
    if (!isnan(_samples[i])) {
      valid[count++] = _samples[i];
    }
  }
  // Menos da metade das amostras validas: melhor declarar "sem eco" do que
  // reportar uma distancia em que nao se pode confiar.
  if (count == 0 || count * 2 < _sampleCount) {
    return NAN;
  }

  for (uint8_t i = 1; i < count; ++i) {
    const float key = valid[i];
    int8_t j = i - 1;
    while (j >= 0 && valid[j] > key) {
      valid[j + 1] = valid[j];
      j--;
    }
    valid[j + 1] = key;
  }
  return valid[count / 2];
}
