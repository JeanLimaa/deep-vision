#include "transport.h"

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WebSocketsClient.h>
#include <WiFi.h>

#include "speaker.h"

namespace {

// Cabecalho dos quadros binarios: tipo(1) + sequencia(8) + timestamp(8), tudo
// big-endian. Precisa ser identico ao BINARY_HEADER do servidor.
constexpr uint8_t kFrameImage = 0x01;
constexpr uint8_t kFrameAudio = 0x02;
constexpr uint8_t kServerAudio = 0x81;
constexpr size_t kHeaderSize = 17;

void writeUint64(uint8_t* out, uint64_t value) {
  for (int8_t i = 7; i >= 0; --i) {
    out[i] = static_cast<uint8_t>(value & 0xFF);
    value >>= 8;
  }
}

size_t buildHeader(uint8_t* out, uint8_t kind, uint32_t sequence) {
  out[0] = kind;
  writeUint64(out + 1, sequence);
  // O ESP32 nao tem relogio de parede sem NTP. Enviar millis() aqui e proposital:
  // o servidor detecta a divergencia de base e simplesmente omite a latencia de
  // rede da medicao, em vez de publicar um numero incorreto.
  writeUint64(out + 9, millis());
  return kHeaderSize;
}

ServerAction parseAction(const JsonDocument& doc) {
  ServerAction action;
  action.type = doc["type"].as<const char*>() ? doc["type"].as<const char*>() : "";
  action.text = doc["text"].as<const char*>() ? doc["text"].as<const char*>() : "";
  action.tone = doc["tone"].as<const char*>() ? doc["tone"].as<const char*>() : "";
  action.priority = doc["priority"] | 0;
  action.interrupt = doc["interrupt"] | false;
  return action;
}

String telemetryJson(float distanceCm, Zone zone, uint8_t volumeStep, bool withDeviceId) {
  JsonDocument doc;
  doc["type"] = "telemetry";
  if (withDeviceId) {
    doc["device_id"] = DEVICE_ID;
  }
  JsonArray sensors = doc["sensors"].to<JsonArray>();
  JsonObject front = sensors.add<JsonObject>();
  front["sensor_id"] = "front";
  if (isnan(distanceCm)) {
    front["distance_cm"] = nullptr;
  } else {
    front["distance_cm"] = distanceCm;
  }
  doc["rssi_dbm"] = WiFi.RSSI();
  doc["uptime_ms"] = millis();
  doc["free_heap"] = ESP.getFreeHeap();
  doc["firmware"] = FIRMWARE_VERSION;
  // Campos locais, uteis no painel para conferir se borda e servidor concordam.
  doc["local_zone"] = zoneName(zone);
  doc["volume_step"] = volumeStep;

  String out;
  serializeJson(doc, out);
  return out;
}

}  // namespace

// ---------------------------------------------------------------- WebSocket

class WebSocketTransport : public Transport {
 public:
  bool begin() override {
    _client.begin(SERVER_HOST, SERVER_PORT,
                  WS_PATH "?device_id=" DEVICE_ID "&token=" DEVICE_TOKEN);
    _client.setReconnectInterval(RECONNECT_INTERVAL_MS);
    _client.enableHeartbeat(WS_HEARTBEAT_MS, WS_HEARTBEAT_MS / 3, 2);
    _client.onEvent([this](WStype_t type, uint8_t* payload, size_t length) {
      onEvent(type, payload, length);
    });
    return true;
  }

  void loop() override { _client.loop(); }

  bool connected() const override { return _connected; }

  bool sendFrame(const uint8_t* jpeg, size_t length, uint32_t sequence) override {
    if (!_connected) {
      return false;
    }
    // O buffer e alocado na PSRAM: um quadro VGA passa de 40 kB e nao caberia
    // com folga na SRAM junto com a pilha de rede.
    const size_t total = kHeaderSize + length;
    uint8_t* buffer = static_cast<uint8_t*>(heap_caps_malloc(total, MALLOC_CAP_SPIRAM));
    if (buffer == nullptr) {
      buffer = static_cast<uint8_t*>(malloc(total));
    }
    if (buffer == nullptr) {
      return false;
    }
    buildHeader(buffer, kFrameImage, sequence);
    memcpy(buffer + kHeaderSize, jpeg, length);
    const bool ok = _client.sendBIN(buffer, total);
    free(buffer);
    return ok;
  }

  bool sendTelemetry(float distanceCm, Zone zone, uint8_t volumeStep) override {
    if (!_connected) {
      return false;
    }
    // sendTXT recebe String& (nao const), entao o payload precisa de um lvalue.
    String payload = telemetryJson(distanceCm, zone, volumeStep, false);
    return _client.sendTXT(payload);
  }

  bool sendButton(const char* button, bool longPress) override {
    if (!_connected) {
      return false;
    }
    JsonDocument doc;
    doc["type"] = "button";
    doc["button"] = button;
    doc["action"] = longPress ? "long_press" : "press";
    String out;
    serializeJson(doc, out);
    return _client.sendTXT(out);
  }

  bool sendCommand(const char* text) override {
    if (!_connected) {
      return false;
    }
    JsonDocument doc;
    doc["type"] = "command";
    doc["text"] = text;
    String out;
    serializeJson(doc, out);
    return _client.sendTXT(out);
  }

  bool sendAudio(const uint8_t* pcm, size_t length) override {
    if (!_connected) {
      return false;
    }
    const size_t total = kHeaderSize + length;
    uint8_t* buffer = static_cast<uint8_t*>(heap_caps_malloc(total, MALLOC_CAP_SPIRAM));
    if (buffer == nullptr) {
      return false;
    }
    buildHeader(buffer, kFrameAudio, 0);
    memcpy(buffer + kHeaderSize, pcm, length);
    const bool ok = _client.sendBIN(buffer, total);
    free(buffer);
    return ok;
  }

 private:
  void onEvent(WStype_t type, uint8_t* payload, size_t length) {
    switch (type) {
      case WStype_CONNECTED: {
        _connected = true;
        Serial.println("[ws] conectado");
        JsonDocument doc;
        doc["type"] = "hello";
        doc["firmware"] = FIRMWARE_VERSION;
        String out;
        serializeJson(doc, out);
        _client.sendTXT(out);
        break;
      }
      case WStype_DISCONNECTED:
        _connected = false;
        Serial.println("[ws] desconectado");
        break;
      case WStype_TEXT: {
        JsonDocument doc;
        if (deserializeJson(doc, payload, length) != DeserializationError::Ok) {
          return;
        }
        const char* kind = doc["type"];
        if (kind == nullptr) {
          return;
        }
        if (strcmp(kind, "frame_ack") == 0 || strcmp(kind, "pong") == 0) {
          return;  // sem acao; serve so para medir a saude do enlace
        }
        dispatch(parseAction(doc));
        break;
      }
      case WStype_BIN:
        // Audio sintetizado pelo servidor. Placas sem amplificador I2S
        // descartam o quadro e ficam apenas com os bipes locais.
        if (length > 2 && payload[0] == kServerAudio) {
          onAudioFrame(payload + 2, length - 2);
        }
        break;
      default:
        break;
    }
  }

  void onAudioFrame(const uint8_t* data, size_t length);

  WebSocketsClient _client;
  bool _connected = false;
};

// --------------------------------------------------------------------- HTTP

class HttpTransport : public Transport {
 public:
  bool begin() override {
    _base = String("http://") + SERVER_HOST + ":" + String(SERVER_PORT);
    return true;
  }

  void loop() override {}

  bool connected() const override { return WiFi.status() == WL_CONNECTED; }

  bool sendFrame(const uint8_t* jpeg, size_t length, uint32_t sequence) override {
    if (!connected()) {
      return false;
    }
    HTTPClient http;
    http.begin(_base + HTTP_FRAME_PATH);
    http.addHeader("Content-Type", "image/jpeg");
    http.addHeader("X-Device-Id", DEVICE_ID);
    http.addHeader("X-Device-Token", DEVICE_TOKEN);
    http.addHeader("X-Frame-Seq", String(sequence));
    http.setTimeout(4000);

    const int status = http.POST(const_cast<uint8_t*>(jpeg), length);
    if (status == 200) {
      handleResponse(http.getString());
    }
    http.end();
    return status == 200;
  }

  bool sendTelemetry(float distanceCm, Zone zone, uint8_t volumeStep) override {
    return postJson(HTTP_TELEMETRY_PATH, telemetryJson(distanceCm, zone, volumeStep, true));
  }

  bool sendButton(const char* button, bool longPress) override {
    JsonDocument doc;
    doc["device_id"] = DEVICE_ID;
    doc["button"] = button;
    doc["action"] = longPress ? "long_press" : "press";
    String out;
    serializeJson(doc, out);
    return postJson(HTTP_BUTTON_PATH, out);
  }

  bool sendCommand(const char* text) override {
    JsonDocument doc;
    doc["device_id"] = DEVICE_ID;
    doc["text"] = text;
    doc["source"] = "device";
    String out;
    serializeJson(doc, out);
    return postJson("/api/v1/command", out);
  }

  bool sendAudio(const uint8_t*, size_t) override {
    // No modo HTTP o audio do comando de voz iria para /api/v1/ingest/audio.
    // Nao implementado porque as placas com microfone usam WebSocket.
    return false;
  }

 private:
  bool postJson(const char* path, const String& body) {
    if (!connected()) {
      return false;
    }
    HTTPClient http;
    http.begin(_base + path);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-Device-Token", DEVICE_TOKEN);
    http.setTimeout(3000);
    const int status = http.POST(body);
    if (status == 200) {
      handleResponse(http.getString());
    }
    http.end();
    return status == 200;
  }

  /// No modo HTTP as acoes voltam dentro da resposta, no campo "actions".
  void handleResponse(const String& body) {
    JsonDocument doc;
    if (deserializeJson(doc, body) != DeserializationError::Ok) {
      return;
    }
    JsonArrayConst actions = doc["actions"];
    for (JsonObjectConst item : actions) {
      ServerAction action;
      action.type = item["type"].as<const char*>() ? item["type"].as<const char*>() : "";
      action.text = item["text"].as<const char*>() ? item["text"].as<const char*>() : "";
      action.tone = item["tone"].as<const char*>() ? item["tone"].as<const char*>() : "";
      action.priority = item["priority"] | 0;
      action.interrupt = item["interrupt"] | false;
      dispatch(action);
    }
  }

  String _base;
};

void WebSocketTransport::onAudioFrame(const uint8_t* data, size_t length) {
  // Repassa ao amplificador. Sem hardware de audio, g_speaker.play() e no-op e
  // o quadro simplesmente se perde -- de proposito, para nao bloquear o envio
  // de video enquanto o alto-falante nao existe.
  g_speaker.play(data, length);
}

Transport* createTransport() {
#if USE_WEBSOCKET
  return new WebSocketTransport();
#else
  return new HttpTransport();
#endif
}
