/**
 * Rede Wi-Fi e endereco do servidor, sem nada fixo no firmware.
 *
 * Primeiro uso (ou rede salva que nao responde): a placa cria a rede
 * PORTAL_AP_NAME. O celular conecta nela, a pagina de configuracao abre
 * sozinha, e a rede escolhida fica salva na memoria da placa -- sobrevive a
 * desligar e a regravar o firmware. O portal nao bloqueia o laco principal: o
 * alerta do sonar continua funcionando enquanto alguem configura.
 *
 * O servidor e encontrado por mDNS (servico _avs._tcp), entao a troca de IP do
 * PC nao exige nada. Para redes que bloqueiam mDNS, o portal aceita um IP
 * digitado, e secrets.h pode fixar um ultimo recurso.
 *
 * Segurar o botao start/stop por BUTTON_RESET_HOLD_MS apaga tudo isso
 * (forgetAndRestart) e a placa volta ao modo de configuracao.
 */

#pragma once

#include <Arduino.h>
#include <WiFiManager.h>

struct ServerEndpoint {
  String host;
  uint16_t port = 0;

  bool valid() const { return host.length() > 0 && port > 0; }
};

class WifiSetup {
 public:
  WifiSetup();

  /// Tenta a rede salva; sem sucesso, abre o portal e retorna (nao bloqueia).
  /// Devolve true se ja saiu conectado.
  bool begin();

  /// Atende o portal e a descoberta do servidor. Chamar a cada volta do laco.
  void loop();

  bool connected() const;
  bool portalActive();

  /// Entrega o endereco do servidor quando ele aparece ou muda.
  bool takeEndpoint(ServerEndpoint& out);

  /// O servidor achado parou de responder: procura de novo, o IP pode ter
  /// mudado. Sem efeito quando o endereco foi digitado no portal.
  void requestRediscovery();

  /// Apaga rede e servidor salvos e reinicia no modo de configuracao.
  [[noreturn]] void forgetAndRestart();

 private:
  void loadSaved();
  void saveParams();
  void onConnected();
  void pumpDiscovery();
  void publish(const ServerEndpoint& endpoint);

  WiFiManager _wm;
  WiFiManagerParameter _hint;
  WiFiManagerParameter _hostParam;
  WiFiManagerParameter _portParam;

  String _savedHost;
  uint16_t _savedPort = 0;

  ServerEndpoint _endpoint;
  bool _endpointChanged = false;
  bool _manualEndpoint = false;  // digitado no portal: nunca substituido
  bool _rediscover = false;
  bool _connectedOnce = false;
  bool _mdnsStarted = false;
  uint32_t _lastDiscoveryMs = 0;
  bool _discoveryAttempted = false;
};
