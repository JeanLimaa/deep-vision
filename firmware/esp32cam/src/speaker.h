/**
 * Saida de audio digital (I2S) para amplificador MAX98357A.
 *
 * Este e o unico componente do projeto que ainda nao foi montado no prototipo.
 * O codigo esta completo e compila quando HAS_I2S_SPEAKER=1; nas placas sem
 * amplificador todas as chamadas viram no-ops e o retorno ao usuario fica por
 * conta dos bipes do buzzer (``AlertBuzzer``) e do audio reproduzido no painel
 * web. Ligar o alto-falante nao exige mudanca em nenhuma outra parte do
 * firmware -- apenas trocar o ambiente de compilacao.
 *
 * O servidor envia WAV PCM 16 bits mono. O cabecalho de 44 bytes e ignorado e o
 * restante vai direto para o barramento I2S.
 */

#pragma once

#include <Arduino.h>

class Speaker {
 public:
  bool begin();

  /// Reproduz um bloco WAV/PCM recebido do servidor.
  void play(const uint8_t* wav, size_t length);

  /// Interrompe a reproducao atual (usado por alertas com prioridade).
  void stop();

  void setVolumeStep(uint8_t step) { _volumeStep = constrain(step, 0, 10); }

  bool available() const { return _ready; }

 private:
  bool _ready = false;
  uint8_t _volumeStep = 7;
};

extern Speaker g_speaker;
