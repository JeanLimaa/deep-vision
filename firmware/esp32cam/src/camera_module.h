/**
 * Inicializacao da camera OV2640 e captura de quadros JPEG.
 *
 * A codificacao JPEG e feita pelo proprio sensor, nao pelo processador -- e o
 * que torna viavel transmitir video de um microcontrolador. O framebuffer vive
 * na PSRAM; sem ela a placa nao sustenta VGA e o firmware cai para QVGA
 * automaticamente.
 */

#pragma once

#include <Arduino.h>
#include <esp_camera.h>

class Camera {
 public:
  bool begin();

  /// Captura um quadro. O ponteiro devolvido pertence ao driver e precisa ser
  /// liberado com ``release()`` -- nao libera-lo trava a captura no segundo
  /// quadro, que e o erro mais comum neste hardware.
  camera_fb_t* capture();
  void release(camera_fb_t* frame);

  bool ready() const { return _ready; }
  bool hasPsram() const { return _hasPsram; }

 private:
  void applySensorTuning();

  bool _ready = false;
  bool _hasPsram = false;
};
