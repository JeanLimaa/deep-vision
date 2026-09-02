#include "microphone.h"

#include "config.h"
#include "pins.h"

Microphone g_microphone;

#if HAS_I2S_MICROPHONE

#include <driver/i2s.h>

namespace {
constexpr i2s_port_t kPort = I2S_NUM_1;  // porta 0 fica com o alto-falante
constexpr size_t kBytesPerSample = sizeof(int16_t);
}  // namespace

bool Microphone::begin() {
  _capacity = static_cast<size_t>(AUDIO_SAMPLE_RATE) * MIC_RECORD_MS / 1000 * kBytesPerSample;
  _buffer = static_cast<uint8_t*>(heap_caps_malloc(_capacity, MALLOC_CAP_SPIRAM));
  if (_buffer == nullptr) {
    Serial.println("[mic] sem PSRAM para o buffer de gravacao");
    return false;
  }

  i2s_config_t config = {};
  // PDM: o microfone embutido da XIAO ESP32-S3 Sense. Para um INMP441 (I2S
  // padrao) basta remover I2S_MODE_PDM desta linha.
  config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM);
  config.sample_rate = AUDIO_SAMPLE_RATE;
  config.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  config.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  config.dma_buf_count = 8;
  config.dma_buf_len = 256;

  if (i2s_driver_install(kPort, &config, 0, nullptr) != ESP_OK) {
    Serial.println("[mic] falha ao instalar o driver I2S");
    return false;
  }

  i2s_pin_config_t pins = {};
  pins.bck_io_num = I2S_PIN_NO_CHANGE;
  pins.ws_io_num = PIN_MIC_WS;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = PIN_MIC_SD;

  if (i2s_set_pin(kPort, &pins) != ESP_OK) {
    Serial.println("[mic] falha ao configurar os pinos I2S");
    return false;
  }

  _ready = true;
  Serial.println("[mic] entrada I2S pronta");
  return true;
}

bool Microphone::startCapture() {
  if (!_ready || _capturing) {
    return false;
  }
  _capturing = true;
  _written = 0;
  _startedAtMs = millis();
  i2s_start(kPort);
  return true;
}

bool Microphone::poll(const uint8_t*& data, size_t& length) {
  if (!_capturing) {
    return false;
  }

  // Le o que estiver pronto no DMA sem bloquear: a captura acontece em paralelo
  // com o envio dos quadros de video.
  size_t read = 0;
  const size_t remaining = _capacity - _written;
  if (remaining > 0) {
    i2s_read(kPort, _buffer + _written, min(remaining, static_cast<size_t>(1024)), &read, 0);
    _written += read;
  }

  const bool full = _written >= _capacity;
  const bool timeout = millis() - _startedAtMs >= MIC_RECORD_MS;
  if (!full && !timeout) {
    return false;
  }

  i2s_stop(kPort);
  _capturing = false;
  data = _buffer;
  length = _written;
  return _written > 0;
}

#else  // placa sem microfone digital

bool Microphone::begin() {
  _ready = false;
  return false;
}

bool Microphone::startCapture() { return false; }

bool Microphone::poll(const uint8_t*&, size_t&) { return false; }

#endif
