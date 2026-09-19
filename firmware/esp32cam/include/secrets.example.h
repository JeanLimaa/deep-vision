/**
 * Copie este arquivo para secrets.h e preencha com os dados da sua rede.
 * secrets.h esta no .gitignore -- credenciais nao entram no repositorio.
 */

#pragma once

#define WIFI_SSID "sua-rede"
#define WIFI_PASSWORD "sua-senha"

// Host e porta do servidor de inferencia (o PC que roda o FastAPI).
#define SERVER_HOST "192.168.0.101"
#define SERVER_PORT 8000

// Deve ser igual a AVS_SERVER__DEVICE_TOKEN no servidor. Vazio = sem autenticacao.
#define DEVICE_TOKEN ""
