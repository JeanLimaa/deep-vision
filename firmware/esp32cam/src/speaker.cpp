#include "speaker.h"

#include "config.h"
#include "pins.h"

Speaker g_speaker;

#if HAS_I2S_SPEAKER

#include <driver/i2s.h>

namespace {
constexpr i2s_port_t kPort = I2S_NUM_0;
constexpr size_t kWavHeaderBytes = 44;
}  // namespace

bool Speaker::begin() {
  i2s_config_t config = {};
  config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_TX);
  config.sample_rate = AUDIO_SAMPLE_RATE;
  config.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  config.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  config.dma_buf_count = 8;
  config.dma_buf_len = 256;
  config.use_apll = false;

  if (i2s_driver_install(kPort, &config, 0, nullptr) != ESP_OK) {
    Serial.println("[audio] falha ao instalar o driver I2S");
    return false;
  }

  i2s_pin_config_t pins = {};
  pins.bck_io_num = PIN_I2S_BCLK;
  pins.ws_io_num = PIN_I2S_LRC;
  pins.data_out_num = PIN_I2S_DOUT;
  pins.data_in_num = I2S_PIN_NO_CHANGE;

  if (i2s_set_pin(kPort, &pins) != ESP_OK) {
    Serial.println("[audio] falha ao configurar os pinos I2S");
    return false;
  }

  _ready = true;
  Serial.println("[audio] saida I2S pronta");
  return true;
}

void Speaker::play(const uint8_t* wav, size_t length) {
  if (!_ready || _volumeStep == 0 || length <= kWavHeaderBytes) {
    return;
  }

  // Pula o cabecalho WAV; o corpo ja e PCM 16 bits mono.
  const int16_t* samples = reinterpret_cast<const int16_t*>(wav + kWavHeaderBytes);
  const size_t sampleCount = (length - kWavHeaderBytes) / sizeof(int16_t);

  // Escala pelo volume antes de escrever, em blocos pequenos para nao alocar
  // uma copia inteira do audio na memoria.
  constexpr size_t kBlock = 256;
  int16_t block[kBlock];
  size_t written = 0;

  for (size_t offset = 0; offset < sampleCount; offset += kBlock) {
    const size_t count = min(kBlock, sampleCount - offset);
    for (size_t i = 0; i < count; ++i) {
      block[i] = static_cast<int16_t>((static_cast<int32_t>(samples[offset + i]) * _volumeStep) / 10);
    }
    i2s_write(kPort, block, count * sizeof(int16_t), &written, portMAX_DELAY);
  }
}

void Speaker::stop() {
  if (_ready) {
    i2s_zero_dma_buffer(kPort);
  }
}

#else  // placa sem amplificador I2S

bool Speaker::begin() {
  _ready = false;
  return false;
}

void Speaker::play(const uint8_t*, size_t) {}

void Speaker::stop() {}

#endif
