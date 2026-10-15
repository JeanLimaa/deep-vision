/**
 * Parametros de operacao do no de borda.
 *
 * Os limites de zona repetem os valores do servidor de proposito: o alerta de
 * risco imediato precisa funcionar mesmo com o Wi-Fi caido (Secao 5.4 do TCC).
 * O servidor reenvia esses valores na mensagem "config" ao conectar, e o
 * firmware os atualiza -- assim existe uma unica fonte de verdade em operacao
 * normal, e um padrao seguro quando nao ha rede.
 */

#pragma once

// secrets.h e opcional. A rede Wi-Fi e o endereco do servidor sao configurados
// pelo celular no primeiro uso (ver network.h); o arquivo so sobrescreve os
// padroes abaixo -- util em desenvolvimento, para fixar o IP do PC.
#if __has_include("secrets.h")
#include "secrets.h"
#endif

// Ultimo recurso para achar o servidor, quando nem o portal nem o mDNS deram um
// endereco. Vazio = depende so da descoberta automatica.
#ifndef SERVER_HOST
#define SERVER_HOST ""
#endif
#ifndef SERVER_PORT
#define SERVER_PORT 8000
#endif
// Deve ser igual a AVS_SERVER__DEVICE_TOKEN no servidor. Vazio = sem autenticacao.
#ifndef DEVICE_TOKEN
#define DEVICE_TOKEN ""
#endif

// --- Identificacao ---
#define DEVICE_ID "esp32cam-01"
#define FIRMWARE_VERSION "0.1.0"

// --- Transporte ---
// 1 = WebSocket (recomendado); 0 = HTTP POST por quadro (o modo do artigo).
#define USE_WEBSOCKET 1
#define WS_PATH "/api/v1/stream"
#define HTTP_FRAME_PATH "/api/v1/ingest/frame"
#define HTTP_TELEMETRY_PATH "/api/v1/ingest/telemetry"
#define HTTP_BUTTON_PATH "/api/v1/ingest/button"

// --- Camera ---
// VGA (640x480) equilibra detalhe e tamanho do quadro. Acima disso o JPEG
// passa de 60 kB e a taxa util cai mesmo em Wi-Fi bom.
#define CAMERA_FRAME_SIZE FRAMESIZE_VGA
#define CAMERA_JPEG_QUALITY 10  // 10 (melhor) .. 63 (pior)

// Orientacao da imagem. Depende de como a fita da camera foi dobrada na placa,
// entao confira no painel: o YOLO praticamente nao reconhece pessoas de cabeca
// para baixo. Chao embaixo e teto em cima = correto. Espelhamento horizontal
// nao afeta a deteccao, mas troca "a esquerda" por "a direita" na narracao.
#define CAMERA_VFLIP 1
#define CAMERA_HMIRROR 0
#define CAMERA_FB_COUNT 2       // buffer duplo exige PSRAM

// Clock do sensor. 20 MHz e o maximo; 10 MHz reduz consumo e aquecimento ao
// custo de metade da taxa bruta de captura -- em VGA ainda da ~12 quadros/s,
// acima dos 8 que o projeto envia. Fica em 10 MHz porque a queda de tensao
// (brownout) e o problema real desta placa, e nao a taxa de captura.
#define CAMERA_XCLK_HZ 20000000

// A inicializacao da camera falha quando a tensao cai no pico de corrente.
// Em vez de seguir cego sem video, o firmware tenta de novo neste intervalo.
#define CAMERA_RETRY_INTERVAL_MS 10000

// Sem Wi-Fi por este tempo, o dispositivo se reinicia: e o unico jeito de sair
// sozinho de um estado ruim da pilha de rede. O alerta local do sonar continua
// funcionando enquanto isso, e so para durante os ~2 s do reinicio.
#define WIFI_RESTART_AFTER_MS 120000

// Intervalo alvo entre quadros enviados. 125 ms = 8 quadros/s, taxa em que o
// servidor consegue inferir sem acumular fila.
#define FRAME_INTERVAL_MS 125

// --- Sensor ultrassonico ---
#define SONAR_INTERVAL_MS 60        // >= 60 ms evita eco residual da medida anterior
#define SONAR_TIMEOUT_US 25000UL    // ~4,3 m; alem disso considera-se sem eco
#define SONAR_MEDIAN_WINDOW 5
#define TELEMETRY_INTERVAL_MS 200   // envio das leituras ao servidor

// Velocidade do som a 20 C, em cm/us. A ida e volta divide por 2.
#define SOUND_SPEED_CM_PER_US 0.0343f

// --- Zonas de proximidade (Secao 5.4) ---
#define ZONE_WARNING_CM 150.0f
#define ZONE_CRITICAL_CM 80.0f
#define ZONE_HYSTERESIS_CM 10.0f

// --- Alertas sonoros locais ---
#define TONE_WARNING_HZ 1800
#define TONE_CRITICAL_HZ 2600
#define TONE_WARNING_PERIOD_MS 700   // bipe lento
#define TONE_CRITICAL_PERIOD_MS 220  // bipe rapido
#define TONE_BEEP_MS 90

// --- Botoes ---
#define BUTTON_DEBOUNCE_MS 40
#define BUTTON_LONG_PRESS_MS 900
// Janela para o segundo toque do toque duplo. E tambem o atraso do toque
// simples, que so e entregue depois dela.
#define BUTTON_MULTI_TAP_MS 350
// Segurar o start/stop por este tempo apaga a rede salva e reinicia no modo de
// configuracao. Longe o bastante do toque longo para nao acontecer sem querer.
#define BUTTON_RESET_HOLD_MS 10000

// --- Rede ---
// Potencia maxima de transmissao. O padrao (19,5 dBm) puxa picos que derrubam a
// ESP32-CAM alimentada por USB + protoboard ("Brownout detector was triggered").
// 13 dBm corta o pico e ainda sobra alcance para um roteador no mesmo comodo;
// se o RSSI no log ficar abaixo de -75 dBm, suba para WIFI_POWER_15dBm.
#define WIFI_TX_POWER WIFI_POWER_13dBm
#define WIFI_CONNECT_TIMEOUT_MS 20000
#define RECONNECT_INTERVAL_MS 3000
#define WS_HEARTBEAT_MS 15000

// --- Configuracao pelo celular (portal) ---
// Sem rede salva, ou se ela nao responder, a placa cria esta rede. Conectando
// o celular nela, a pagina de configuracao abre sozinha.
#define PORTAL_AP_NAME "AVS-Config"
// Vazio = rede aberta. Com senha, ela precisa de 8 caracteres ou mais (WPA2).
#define PORTAL_AP_PASSWORD ""
// Com uma rede ja salva, o portal fica aberto por este tempo e a placa reinicia
// para tentar a rede de novo: uma queda temporaria do roteador nao a deixa presa
// no modo de configuracao. Sem rede salva, o portal nao fecha.
#define PORTAL_TIMEOUT_S 180

// --- Descoberta do servidor (mDNS) ---
// O servidor se anuncia como _avs._tcp na rede local (app/discovery.py).
#define MDNS_SERVICE "_avs"
#define MDNS_PROTO "_tcp"
#define MDNS_QUERY_TIMEOUT_MS 3000
#define DISCOVERY_RETRY_MS 10000
// Servidor achado por mDNS e sem resposta ha este tempo: procura de novo, porque
// o IP do PC pode ter mudado (DHCP).
#define SERVER_REDISCOVER_AFTER_MS 30000

// --- Audio (somente placas com amplificador I2S) ---
#define AUDIO_SAMPLE_RATE 16000
#define MIC_RECORD_MS 3000  // duracao da captura de um comando de voz
