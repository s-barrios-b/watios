#include "EmonLib.h"
#include "config.h" // WIFI_SSID, WIFI_PASS, NODE_SERVER_URL, APPS_SCRIPT_URL
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

// ── Calibración sensores ─────────────────────────────────────
#define vCalibration 151
#define currCalibration 0.06
#define NUM_MUESTRAS 5 // promedia 5 lecturas por envío

EnergyMonitor emon;
float kWh = 0;
unsigned long lastMillis = 0;

// ════════════════════════════════════════════════════════════
//  1. Envío HTTPS al Apps Script (lógica original intacta)
// ════════════════════════════════════════════════════════════
void sendToAppsScript(float vrms, float irms, float power) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Desconectado — reintentando...");
    WiFi.reconnect();
    return;
  }

  char json[200];
  sprintf(json, "{\"vrms\":%.2f,\"irms\":%.4f,\"power\":%.4f,\"kWh\":%.6f}",
          vrms, irms, power, kWh);

  WiFiClientSecure client;
  client.setInsecure(); // Apps Script no requiere validación de cert

  HTTPClient http;
  http.begin(client, APPS_SCRIPT_URL); // ← viene de config.h / .env
  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(10000); // 10s — Apps Script puede tardar en redireccionar

  unsigned long t0 = millis();
  int code = http.POST(json);
  unsigned long dt = millis() - t0;

  if (code == 200 || code == 302) {
    Serial.printf("[OK]  Apps Script respondió HTTP %d  (%lu ms)\n", code, dt);
  } else {
    Serial.printf("[ERR] HTTP %d  (%lu ms)\n", code, dt);
  }

  http.end();
}

// ════════════════════════════════════════════════════════════
//  2. Envío HTTP al servidor local (Servidor.py → dashboard)
// ════════════════════════════════════════════════════════════
void sendToLocalServer(float vrms, float irms, float power) {
  if (WiFi.status() != WL_CONNECTED)
    return;

  char json[220];
  sprintf(json,
          "{\"vrms\":%.2f,\"irms\":%.4f,\"power\":%.4f,\"kWh\":%.6f,\"uptime\":"
          "%lu}",
          vrms, irms, power, kWh, millis());

  WiFiClient client;
  HTTPClient http;
  http.begin(client, NODE_SERVER_URL); // ← viene de config.h / .env
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(3000); // timeout corto — servidor local debe responder rápido

  int code = http.POST(json);
  if (code > 0) {
    Serial.printf("[Local] HTTP %d\n", code);
  } else {
    Serial.printf("[Local] ERR %d — edita .env y ejecuta generar_config.py\n",
                  code);
  }
  http.end();
}

// ════════════════════════════════════════════════════════════
//  Lectura + promedio + cálculo de energía
// ════════════════════════════════════════════════════════════
void myTimerEvent() {
  float sumVrms = 0, sumIrms = 0, sumPower = 0;

  for (int i = 0; i < NUM_MUESTRAS; i++) {
    emon.calcVI(20, 2000); // 20 cruces de cero ≈ 167ms/muestra
    sumVrms += emon.Vrms;
    sumIrms += emon.Irms;
    sumPower += emon.apparentPower;
  }

  float vrms = sumVrms / NUM_MUESTRAS;
  float irms = sumIrms / NUM_MUESTRAS;
  float power = sumPower / NUM_MUESTRAS;

  unsigned long now = millis();
  kWh += power * (now - lastMillis) / 3600000000.0;
  lastMillis = now;

  Serial.printf("Vrms: %.2fV  Irms: %.4fA  Power: %.4fW  kWh: %.6f\n", vrms,
                irms, power, kWh);

  sendToLocalServer(vrms, irms, power); // rápido: HTTP sin SSL
  sendToAppsScript(vrms, irms, power);  // lento:  HTTPS con redirección
}

// ════════════════════════════════════════════════════════════
void setup() {
  Serial.begin(9600);

  emon.voltage(35, vCalibration, 1.7);
  emon.current(34, currCalibration);

  WiFi.begin(WIFI_SSID, WIFI_PASS); // ← vienen de config.h / .env
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
  const unsigned long INTERVALO = 5000; // envío cada 5 segundos

  if (millis() - lastEvent >= INTERVALO) {
    lastEvent = millis();
    myTimerEvent();
  }
}
