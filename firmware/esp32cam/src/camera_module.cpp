#include "camera_module.h"

#include "config.h"
#include "pins.h"

bool Camera::begin() {
  _hasPsram = psramFound();

  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_1;  // canal 0 e do buzzer
  config.ledc_timer = LEDC_TIMER_1;
  config.pin_d0 = CAM_PIN_D0;
  config.pin_d1 = CAM_PIN_D1;
  config.pin_d2 = CAM_PIN_D2;
  config.pin_d3 = CAM_PIN_D3;
  config.pin_d4 = CAM_PIN_D4;
  config.pin_d5 = CAM_PIN_D5;
  config.pin_d6 = CAM_PIN_D6;
  config.pin_d7 = CAM_PIN_D7;
  config.pin_xclk = CAM_PIN_XCLK;
  config.pin_pclk = CAM_PIN_PCLK;
  config.pin_vsync = CAM_PIN_VSYNC;
  config.pin_href = CAM_PIN_HREF;
  config.pin_sccb_sda = CAM_PIN_SIOD;
  config.pin_sccb_scl = CAM_PIN_SIOC;
  config.pin_pwdn = CAM_PIN_PWDN;
  config.pin_reset = CAM_PIN_RESET;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  if (_hasPsram) {
    config.frame_size = CAMERA_FRAME_SIZE;
    config.jpeg_quality = CAMERA_JPEG_QUALITY;
    config.fb_count = CAMERA_FB_COUNT;
    config.fb_location = CAMERA_FB_IN_PSRAM;
    // Captura sob demanda: evita acumular quadros velhos na fila, que so
    // aumentariam a latencia percebida pelo usuario.
    config.grab_mode = CAMERA_GRAB_LATEST;
  } else {
    // Sem PSRAM nao ha memoria para VGA com buffer duplo.
    Serial.println("[camera] PSRAM ausente: caindo para QVGA e buffer unico");
    config.frame_size = FRAMESIZE_QVGA;
    config.jpeg_quality = 15;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_DRAM;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  }

  const esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("[camera] falha na inicializacao: 0x%x\n", err);
    return false;
  }

  applySensorTuning();
  _ready = true;
  Serial.printf("[camera] pronta (psram=%s)\n", _hasPsram ? "sim" : "nao");
  return true;
}

void Camera::applySensorTuning() {
  sensor_t* sensor = esp_camera_sensor_get();
  if (sensor == nullptr) {
    return;
  }

  // O OV2640 sai de fabrica com a imagem espelhada e de cabeca para baixo na
  // montagem da AI-Thinker.
  sensor->set_vflip(sensor, 1);
  sensor->set_hmirror(sensor, 0);

  // Ganho e exposicao automaticos, com realce leve de contraste: ajuda nos
  // cenarios de baixa luminosidade apontados na Secao 6 do TCC.
  sensor->set_gain_ctrl(sensor, 1);
  sensor->set_exposure_ctrl(sensor, 1);
  sensor->set_whitebal(sensor, 1);
  sensor->set_contrast(sensor, 1);
  sensor->set_brightness(sensor, 1);
  sensor->set_saturation(sensor, 0);
}

camera_fb_t* Camera::capture() {
  if (!_ready) {
    return nullptr;
  }
  camera_fb_t* frame = esp_camera_fb_get();
  if (frame == nullptr) {
    Serial.println("[camera] captura falhou");
    return nullptr;
  }
  if (frame->format != PIXFORMAT_JPEG) {
    esp_camera_fb_return(frame);
    return nullptr;
  }
  return frame;
}

void Camera::release(camera_fb_t* frame) {
  if (frame != nullptr) {
    esp_camera_fb_return(frame);
  }
}
