/**
 * Captura de audio para os comandos de voz (Secao 5.5 do TCC).
 *
 * Junto com o alto-falante, este e o hardware que ainda falta no prototipo. O
 * codigo esta completo e ativo quando HAS_I2S_MICROPHONE=1; nas demais placas
 * as chamadas viram no-ops e os comandos continuam disponiveis pelo painel web
 * ou pelo simulador, atravessando exatamente o mesmo pipeline de intencoes do
 * servidor.
 *
 * Fluxo: botao start/stop pressionado -> grava MIC_RECORD_MS de PCM 16 kHz ->
 * envia como quadro binario -> servidor transcreve, casa a intencao e responde.
 */

#pragma once

#include <Arduino.h>

class Microphone {
 public:
  bool begin();

  /// Inicia a captura de um comando. Devolve false se ja houver uma em curso.
  bool startCapture();

  /// Chamado a cada loop(). Devolve true quando o buffer esta completo; nesse
  /// caso ``data``/``length`` apontam para o PCM pronto para envio.
  bool poll(const uint8_t*& data, size_t& length);

  bool capturing() const { return _capturing; }
  bool available() const { return _ready; }

 private:
  bool _ready = false;
  bool _capturing = false;
  uint32_t _startedAtMs = 0;
  size_t _written = 0;
  uint8_t* _buffer = nullptr;
  size_t _capacity = 0;
};

extern Microphone g_microphone;
