#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include "EmonLib.h"

// ── Credenciales WiFi ────────────────────────────────────────
const char* ssid     = "Saira_Sofia";
const char* password = "1043154303";

// ── Google Apps Script (Para Google Sheets) ──────────────────
const char* scriptURL = "https://script.google.com/macros/s/AKfycbz_wMGcjBVCWe-Aupx1TwA_bagBBEB3yoPfW9B1S_eDu1jKa1Wv0NEiedUUNZNjxkAUUg/exec";

// ── Servidor Local Node.js (Para Dashboard ML y Tiempo Real) ─
// IMPORTANTE: Cambia esta IP por la IP local de tu computador
const char* nodeServerURL = "http://192.168.1.XX:5000/data";

// ── Calibración sensores ─────────────────────────────────────
#define vCalibration    147
#define currCalibration 0.056
#define NUM_MUESTRAS    3      // promedia 3 lecturas por envío

EnergyMonitor emon;
float kWh            = 0;
unsigned long lastMillis = 0;

// ════════════════════════════════════════════════════════════
//  Envío HTTPS al Apps Script y HTTP al Node Server
// ════════════════════════════════════════════════════════════
void sendData(float vrms, float irms, float power) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Desconectado — reintentando...");
    WiFi.reconnect();
    return;
  }

  unsigned long uptime = millis();
  char json[200];
  sprintf(json,
    "{\"vrms\":%.2f,\"irms\":%.4f,\"power\":%.4f,\"kWh\":%.6f,\"uptime\":%lu}",
    vrms, irms, power, kWh, uptime);

  WiFiClientSecure clientSecure;
  clientSecure.setInsecure(); // Para Google

  WiFiClient client; // Para Servidor Node local
  HTTPClient http;

  // 1. ENVIAR A SERVIDOR LOCAL NODE.JS (Rápido)
  http.begin(client, nodeServerURL);
  http.addHeader("Content-Type", "application/json");
  int codeNode = http.POST(json);
  if (codeNode > 0) {
    Serial.printf("[NodeJS] OK ( HTTP %d )\n", codeNode);
  } else {
    Serial.printf("[NodeJS] ERR ( %d ) - Revisa la IP de tu PC\n", codeNode);
  }
  http.end();

  // 2. ENVIAR A GOOGLE APPS SCRIPT (Más lento)
  http.begin(clientSecure, scriptURL);
  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(10000); 

  unsigned long t0 = millis();
  int codeGas = http.POST(json);
  unsigned long dt = millis() - t0;

  if (codeGas == 200 || codeGas == 302) {
    Serial.printf("[Google] OK ( HTTP %d en %lu ms)\n", codeGas, dt);
  } else {
    Serial.printf("[Google] ERR( HTTP %d en %lu ms)\n", codeGas, dt);
  }
  http.end();
}

// ════════════════════════════════════════════════════════════
//  Lectura + promedio + cálculo de energía
// ════════════════════════════════════════════════════════════
void myTimerEvent() {
  float sumVrms = 0, sumIrms = 0, sumPower = 0;

  for (int i = 0; i < NUM_MUESTRAS; i++) {
    emon.calcVI(20, 2000);          // 20 cruces de cero ≈ 167ms/muestra
    sumVrms  += emon.Vrms;
    sumIrms  += emon.Irms;
    sumPower += emon.apparentPower;
  }

  float vrms  = sumVrms  / NUM_MUESTRAS;
  float irms  = sumIrms  / NUM_MUESTRAS;
  float power = sumPower / NUM_MUESTRAS;

  unsigned long now = millis();
  kWh += power * (now - lastMillis) / 3600000000.0;
  lastMillis = now;

  Serial.printf("Vrms: %.2fV  Irms: %.4fA  Power: %.4fW  kWh: %.6f\n",
                vrms, irms, power, kWh);

  sendData(vrms, irms, power);
}

// ════════════════════════════════════════════════════════════
void setup() {
  Serial.begin(9600);

  emon.voltage(35, vCalibration, 1.7);
  emon.current(34, currCalibration);

  WiFi.begin(ssid, password);
  WiFi.setAutoReconnect(true);
  Serial.print("Conectando WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nConectado: " + WiFi.localIP().toString());

  lastMillis = millis();
}

void loop() {
  static unsigned long lastEvent = 0;
  const unsigned long INTERVALO  = 5000;   // envío cada 5 segundos

  if (millis() - lastEvent >= INTERVALO) {
    lastEvent = millis();
    myTimerEvent();
  }
}