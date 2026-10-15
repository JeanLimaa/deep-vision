#include "wifi_setup.h"

#include <ESPmDNS.h>
#include <Preferences.h>
#include <WiFi.h>
#include <mdns.h>

#include "config.h"

namespace {

constexpr const char* kPrefsNamespace = "avs";
constexpr const char* kKeyHost = "srv_host";
constexpr const char* kKeyPort = "srv_port";
constexpr int kHostLength = 40;
constexpr int kPortLength = 6;

// A consulta mDNS bloqueia por ate MDNS_QUERY_TIMEOUT_MS. No laco principal ela
// calaria o sonar por 3 s; por isso roda numa tarefa propria, que deixa o
// resultado aqui e termina.
struct Discovery {
  volatile bool running = false;
  volatile bool done = false;
  volatile uint32_t ip = 0;
  volatile uint16_t port = 0;
  uint32_t localIp = 0;
  uint32_t netmask = 0;
};
Discovery g_discovery;

void discoveryTask(void*) {
  mdns_result_t* results = nullptr;
  uint32_t chosenIp = 0;
  uint16_t chosenPort = 0;
  bool sameSubnet = false;

  if (mdns_query_ptr(MDNS_SERVICE, MDNS_PROTO, MDNS_QUERY_TIMEOUT_MS, 8, &results) == ESP_OK) {
    // O PC costuma ter varias interfaces (VirtualBox, WSL, VPN) e o servidor
    // anuncia o IP de todas. A certa e a que esta na mesma sub-rede da placa.
    for (mdns_result_t* r = results; r != nullptr && !sameSubnet; r = r->next) {
      for (mdns_ip_addr_t* a = r->addr; a != nullptr && !sameSubnet; a = a->next) {
        if (a->addr.type != ESP_IPADDR_TYPE_V4) {
          continue;
        }
        const uint32_t ip = a->addr.u_addr.ip4.addr;
        sameSubnet = (ip & g_discovery.netmask) == (g_discovery.localIp & g_discovery.netmask);
        if (chosenIp == 0 || sameSubnet) {
          chosenIp = ip;
          chosenPort = r->port;
        }
      }
    }
  }
  if (results != nullptr) {
    mdns_query_results_free(results);
  }

  g_discovery.ip = chosenIp;
  g_discovery.port = chosenPort;
  g_discovery.done = true;
  g_discovery.running = false;
  vTaskDelete(nullptr);
}

}  // namespace

WifiSetup::WifiSetup()
    : _hint("<p>Servidor: deixe em branco para a placa encontrar o PC sozinha.</p>"),
      _hostParam("server_host", "IP do servidor (opcional)", "", kHostLength),
      _portParam("server_port", "Porta do servidor", "", kPortLength) {}

bool WifiSetup::begin() {
  loadSaved();
  _hostParam.setValue(_savedHost.c_str(), kHostLength);
  const String port(_savedPort > 0 ? _savedPort : SERVER_PORT);
  _portParam.setValue(port.c_str(), kPortLength);

  // O WiFiManager le a rede salva da configuracao do radio, que so existe com
  // ele ligado.
  WiFi.mode(WIFI_STA);
  WiFi.setTxPower(WIFI_TX_POWER);  // limita o pico de corrente (ver config.h)
  WiFi.setSleep(false);  // o modo de economia adiciona centenas de ms de latencia

  const bool hasSaved = _wm.getWiFiIsSaved();

  _wm.setDebugOutput(false);
  _wm.setTitle("Sistema Assistivo");
  _wm.setHostname(DEVICE_ID);
  const char* menu[] = {"wifi", "exit"};
  _wm.setMenu(menu, 2);
  _wm.addParameter(&_hint);
  _wm.addParameter(&_hostParam);
  _wm.addParameter(&_portParam);
  _wm.setSaveParamsCallback([this]() { saveParams(); });
  _wm.setConnectTimeout(WIFI_CONNECT_TIMEOUT_MS / 1000);
  // Sem bloqueio: o portal e atendido em loop() e o sonar segue alertando.
  _wm.setConfigPortalBlocking(false);
  _wm.setConfigPortalTimeout(hasSaved ? PORTAL_TIMEOUT_S : 0);
  // Ao subir a rede propria o radio volta a potencia maxima -- o pico que
  // derruba a placa por queda de tensao. Limita de novo.
  _wm.setAPCallback([](WiFiManager*) { WiFi.setTxPower(WIFI_TX_POWER); });
  _wm.setConfigPortalTimeoutCallback([]() {
    Serial.println("[wifi] ninguem configurou; reiniciando para tentar a rede salva");
    Serial.flush();
    ESP.restart();
  });

  Serial.println(hasSaved ? "[wifi] conectando na rede salva" : "[wifi] nenhuma rede salva");
  const char* password = strlen(PORTAL_AP_PASSWORD) > 0 ? PORTAL_AP_PASSWORD : nullptr;
  if (_wm.autoConnect(PORTAL_AP_NAME, password)) {
    onConnected();
    return true;
  }

  Serial.printf("[wifi] modo de configuracao: conecte o celular na rede \"%s\"\n",
                PORTAL_AP_NAME);
  return false;
}

void WifiSetup::loop() {
  if (_wm.getConfigPortalActive() && _wm.process()) {
    Serial.println("[wifi] rede configurada pelo portal");
  }
  if (!connected()) {
    return;
  }
  if (!_connectedOnce) {
    onConnected();
  }
  pumpDiscovery();
}

bool WifiSetup::connected() const { return WiFi.status() == WL_CONNECTED; }

bool WifiSetup::portalActive() { return _wm.getConfigPortalActive(); }

bool WifiSetup::takeEndpoint(ServerEndpoint& out) {
  if (!_endpointChanged) {
    return false;
  }
  _endpointChanged = false;
  out = _endpoint;
  return true;
}

void WifiSetup::requestRediscovery() {
  if (_manualEndpoint || _rediscover) {
    return;
  }
  Serial.println("[mdns] servidor sem resposta; procurando de novo");
  _rediscover = true;
  _lastDiscoveryMs = 0;
  _discoveryAttempted = false;
}

void WifiSetup::forgetAndRestart() {
  Serial.println("[wifi] apagando rede e servidor salvos");
  Preferences prefs;
  prefs.begin(kPrefsNamespace, false);
  prefs.clear();
  prefs.end();
  _wm.resetSettings();
  Serial.flush();
  delay(200);
  ESP.restart();
  while (true) {
  }
}

void WifiSetup::loadSaved() {
  Preferences prefs;
  prefs.begin(kPrefsNamespace, true);
  _savedHost = prefs.getString(kKeyHost, "");
  _savedPort = prefs.getUShort(kKeyPort, 0);
  prefs.end();
}

void WifiSetup::saveParams() {
  String host = _hostParam.getValue();
  host.trim();
  const long port = String(_portParam.getValue()).toInt();

  Preferences prefs;
  prefs.begin(kPrefsNamespace, false);
  prefs.putString(kKeyHost, host);
  prefs.putUShort(kKeyPort, (port > 0 && port < 65536) ? port : SERVER_PORT);
  prefs.end();
  loadSaved();
  Serial.printf("[wifi] servidor salvo: %s\n",
                _savedHost.isEmpty() ? "(descoberta automatica)" : _savedHost.c_str());
}

void WifiSetup::onConnected() {
  _connectedOnce = true;
  Serial.printf("[wifi] conectado em %s | ip=%s rssi=%d dBm\n", WiFi.SSID().c_str(),
                WiFi.localIP().toString().c_str(), WiFi.RSSI());
  WiFi.setTxPower(WIFI_TX_POWER);  // o portal pode ter religado o radio no padrao

  if (!_mdnsStarted) {
    _mdnsStarted = MDNS.begin(DEVICE_ID);
  }

  // Endereco digitado no portal vence a descoberta: e a saida para redes que
  // bloqueiam mDNS.
  if (!_savedHost.isEmpty()) {
    _manualEndpoint = true;
    ServerEndpoint manual;
    manual.host = _savedHost;
    manual.port = _savedPort > 0 ? _savedPort : SERVER_PORT;
    publish(manual);
  }
}

void WifiSetup::pumpDiscovery() {
  if (_manualEndpoint || !_mdnsStarted) {
    return;
  }

  if (g_discovery.done) {
    g_discovery.done = false;
    ServerEndpoint found;
    if (g_discovery.ip != 0) {
      found.host = IPAddress(g_discovery.ip).toString();
      found.port = g_discovery.port;
      Serial.printf("[mdns] servidor encontrado em %s:%u\n", found.host.c_str(), found.port);
      _rediscover = false;
      publish(found);
    } else {
      Serial.println("[mdns] nenhum servidor anunciado na rede");
      if (!_endpoint.valid() && strlen(SERVER_HOST) > 0) {
        // Ultimo recurso: o IP fixado em secrets.h. A busca continua, e o
        // endereco anunciado substitui este quando aparecer.
        ServerEndpoint fallback;
        fallback.host = SERVER_HOST;
        fallback.port = SERVER_PORT;
        Serial.printf("[mdns] usando o IP de secrets.h: %s\n", SERVER_HOST);
        publish(fallback);
        _rediscover = true;
      }
    }
    return;
  }

  const bool wanted = !_endpoint.valid() || _rediscover;
  if (!wanted || g_discovery.running) {
    return;
  }
  const uint32_t nowMs = millis();
  if (_discoveryAttempted && nowMs - _lastDiscoveryMs < DISCOVERY_RETRY_MS) {
    return;
  }
  _discoveryAttempted = true;
  _lastDiscoveryMs = nowMs;

  g_discovery.localIp = static_cast<uint32_t>(WiFi.localIP());
  g_discovery.netmask = static_cast<uint32_t>(WiFi.subnetMask());
  g_discovery.running = true;
  if (xTaskCreate(discoveryTask, "mdns_query", 4096, nullptr, 1, nullptr) != pdPASS) {
    g_discovery.running = false;
  }
}

void WifiSetup::publish(const ServerEndpoint& endpoint) {
  if (endpoint.host == _endpoint.host && endpoint.port == _endpoint.port) {
    return;  // mesmo endereco: reconectar so derrubaria uma conexao boa
  }
  _endpoint = endpoint;
  _endpointChanged = true;
}
