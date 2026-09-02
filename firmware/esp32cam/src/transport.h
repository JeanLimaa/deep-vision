/**
 * Canal com o servidor.
 *
 * Uma interface, duas implementacoes:
 *   WebSocketTransport  conexao persistente, quadro binario, canal de volta em
 *                       tempo real. Padrao.
 *   HttpTransport       um POST por quadro, como descrito na Secao 5.3 do TCC.
 *                       As acoes do servidor chegam na resposta do POST.
 *
 * O restante do firmware fala apenas com ``Transport`` e nao sabe qual dos dois
 * esta ativo.
 */

#pragma once

#include <Arduino.h>
#include <functional>

#include "alerts.h"
#include "config.h"

/// Acao recebida do servidor (fala, bipe ou configuracao).
struct ServerAction {
  String type;
  String text;
  String tone;
  int priority = 0;
  bool interrupt = false;
};

using ActionHandler = std::function<void(const ServerAction&)>;

class Transport {
 public:
  virtual ~Transport() = default;

  virtual bool begin() = 0;
  virtual void loop() = 0;
  virtual bool connected() const = 0;

  virtual bool sendFrame(const uint8_t* jpeg, size_t length, uint32_t sequence) = 0;
  virtual bool sendTelemetry(float distanceCm, Zone zone, uint8_t volumeStep) = 0;
  virtual bool sendButton(const char* button, bool longPress) = 0;
  virtual bool sendCommand(const char* text) = 0;
  virtual bool sendAudio(const uint8_t* pcm, size_t length) = 0;

  void onAction(ActionHandler handler) { _handler = std::move(handler); }

 protected:
  void dispatch(const ServerAction& action) {
    if (_handler) {
      _handler(action);
    }
  }

  ActionHandler _handler;
};

Transport* createTransport();
