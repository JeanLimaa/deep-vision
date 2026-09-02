/**
 * Mapa de pinos por placa.
 *
 * A AI-Thinker ESP32-CAM entrega poucos GPIOs livres: a camera consome 16
 * pinos e o slot microSD reserva GPIO 2, 4, 12, 13, 14 e 15. Desabilitando o
 * cartao (o projeto nao usa) esses seis voltam a ficar disponiveis, mas ainda
 * com ressalvas:
 *
 *   GPIO 0   entra em modo de gravacao se estiver em nivel baixo no boot
 *   GPIO 2   precisa estar em nivel baixo no boot
 *   GPIO 4   compartilhado com o LED de flash (2 A de pico) -- evitar entrada
 *   GPIO 12  strapping: nivel alto no boot impede a inicializacao
 *   GPIO 16  usado como CS da PSRAM nos modulos com 4 MB
 *
 * Por isso o perfil basico da AI-Thinker acomoda apenas um sensor, um buzzer e
 * um botao. Para os quatro botoes do TCC use o expansor PCF8574 (I2C) ou a
 * XIAO ESP32-S3 Sense.
 *
 * Ha ainda o perfil ESP32 DevKit, sem camera: e a placa de bancada usada para
 * validar sonar, zonas, buzzer e botoes antes de o modulo com camera existir.
 */

#pragma once

#if defined(BOARD_ESP32_DEVKIT)

/*
 * ESP32 DevKit v1 comum, sem camera.
 *
 * E a placa de bancada: com ela o no de borda entrega toda a camada de
 * seguranca (sonar, zonas, buzzer, botoes, telemetria) enquanto a camera fica a
 * cargo do simulador. Como sobram GPIOs, os quatro botoes vao direto, sem
 * expansor. Pinos evitados: 0/2/15 (strapping), 6-11 (flash), 34-39 (so entrada).
 */

// Sem camera nesta placa.
#define CAM_PIN_PWDN -1
#define CAM_PIN_RESET -1
#define CAM_PIN_XCLK -1
#define CAM_PIN_SIOD -1
#define CAM_PIN_SIOC -1
#define CAM_PIN_D7 -1
#define CAM_PIN_D6 -1
#define CAM_PIN_D5 -1
#define CAM_PIN_D4 -1
#define CAM_PIN_D3 -1
#define CAM_PIN_D2 -1
#define CAM_PIN_D1 -1
#define CAM_PIN_D0 -1
#define CAM_PIN_VSYNC -1
#define CAM_PIN_HREF -1
#define CAM_PIN_PCLK -1

// --- Perifericos ---
#define PIN_SONAR_TRIG 5
#define PIN_SONAR_ECHO 18  // via divisor resistivo: o HC-SR04 devolve 5 V no echo
#define PIN_BUZZER 19
#define PIN_STATUS_LED 2       // LED azul embutido
#define STATUS_LED_ACTIVE_LOW 0  // no DevKit o LED acende com nivel alto

#define PIN_BTN_START_STOP 21
#define PIN_BTN_VOLUME_UP 22
#define PIN_BTN_VOLUME_DOWN 23
#define PIN_BTN_POWER 25

#define PIN_I2C_SDA 16
#define PIN_I2C_SCL 17

// Reservados para quando o amplificador e o microfone chegarem.
#define PIN_I2S_BCLK 26
#define PIN_I2S_LRC 27
#define PIN_I2S_DOUT 32
#define PIN_MIC_SCK 14
#define PIN_MIC_WS 13
#define PIN_MIC_SD 4

#elif defined(BOARD_AI_THINKER)

// --- Camera OV2640 (pinagem fixa da placa AI-Thinker) ---
#define CAM_PIN_PWDN 32
#define CAM_PIN_RESET -1
#define CAM_PIN_XCLK 0
#define CAM_PIN_SIOD 26
#define CAM_PIN_SIOC 27
#define CAM_PIN_D7 35
#define CAM_PIN_D6 34
#define CAM_PIN_D5 39
#define CAM_PIN_D4 36
#define CAM_PIN_D3 21
#define CAM_PIN_D2 19
#define CAM_PIN_D1 18
#define CAM_PIN_D0 5
#define CAM_PIN_VSYNC 25
#define CAM_PIN_HREF 23
#define CAM_PIN_PCLK 22

// --- Perifericos ---
// GPIO 12 e 4 foram deixados de fora de proposito: o primeiro e strapping (se
// estiver em nivel alto no boot a placa nao inicia) e o segundo aciona o LED de
// flash, cuja carga distorce a leitura de um botao.
#define PIN_SONAR_TRIG 14
#define PIN_SONAR_ECHO 13  // via divisor resistivo: o HC-SR04 devolve 5 V no echo
#define PIN_BUZZER 15
#define PIN_STATUS_LED 33       // LED vermelho embutido
#define STATUS_LED_ACTIVE_LOW 1  // acende com nivel baixo

// Botao unico ligado direto ao ESP32 (perfil esp32cam).
// GPIO 2 aceita pull-up interno; nao o mantenha pressionado durante o boot.
#define PIN_BTN_START_STOP 2
#define PIN_BTN_VOLUME_UP -1
#define PIN_BTN_VOLUME_DOWN -1
#define PIN_BTN_POWER -1

// I2C do expansor PCF8574 (perfil esp32cam_pcf), que devolve os quatro botoes.
// SCL ocupa o RX0: com este perfil o monitor serial fica somente de saida.
#define PIN_I2C_SDA 2
#define PIN_I2C_SCL 3

// Sem pinos livres para audio digital nesta placa.
#define PIN_I2S_BCLK -1
#define PIN_I2S_LRC -1
#define PIN_I2S_DOUT -1
#define PIN_MIC_SCK -1
#define PIN_MIC_WS -1
#define PIN_MIC_SD -1

#elif defined(BOARD_XIAO_S3)

// --- Camera OV2640 da XIAO ESP32-S3 Sense ---
#define CAM_PIN_PWDN -1
#define CAM_PIN_RESET -1
#define CAM_PIN_XCLK 10
#define CAM_PIN_SIOD 40
#define CAM_PIN_SIOC 39
#define CAM_PIN_D7 48
#define CAM_PIN_D6 11
#define CAM_PIN_D5 12
#define CAM_PIN_D4 14
#define CAM_PIN_D3 16
#define CAM_PIN_D2 18
#define CAM_PIN_D1 17
#define CAM_PIN_D0 15
#define CAM_PIN_VSYNC 38
#define CAM_PIN_HREF 47
#define CAM_PIN_PCLK 13

// --- Perifericos ---
#define PIN_SONAR_TRIG 1
#define PIN_SONAR_ECHO 2
#define PIN_BUZZER 3
#define PIN_STATUS_LED 21
#define STATUS_LED_ACTIVE_LOW 1

#define PIN_I2C_SDA 5
#define PIN_I2C_SCL 6

#define PIN_BTN_START_STOP 4
#define PIN_BTN_VOLUME_UP 7
#define PIN_BTN_VOLUME_DOWN 8
#define PIN_BTN_POWER 9

// Amplificador MAX98357A (saida) e microfone PDM embutido (entrada).
#define PIN_I2S_BCLK 43
#define PIN_I2S_LRC 44
#define PIN_I2S_DOUT 42
#define PIN_MIC_SCK 41
#define PIN_MIC_WS 42
#define PIN_MIC_SD 41

#else
#error "Defina BOARD_ESP32_DEVKIT, BOARD_AI_THINKER ou BOARD_XIAO_S3 nas build_flags."
#endif

// Endereco padrao do expansor PCF8574 (A0..A2 em GND).
#define PCF8574_ADDRESS 0x20
