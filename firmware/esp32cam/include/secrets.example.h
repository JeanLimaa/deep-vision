/**
 * Opcional. Copie para secrets.h apenas se precisar sobrescrever um padrao.
 * secrets.h esta no .gitignore -- credenciais nao entram no repositorio.
 *
 * A rede Wi-Fi NAO fica mais aqui: ela e escolhida pelo celular no primeiro uso
 * (rede "AVS-Config") e salva na memoria da placa. O servidor e encontrado
 * sozinho na rede local (mDNS).
 */

#pragma once

// Endereco do servidor, usado so quando a descoberta automatica falhar (rede
// que bloqueia mDNS) e nada tiver sido digitado no portal.
// #define SERVER_HOST "192.168.0.101"
// #define SERVER_PORT 8000

// Deve ser igual a AVS_SERVER__DEVICE_TOKEN no servidor. Vazio = sem autenticacao.
#define DEVICE_TOKEN ""
