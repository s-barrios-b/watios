#include "EmonLib.h"
#include "config.h" // WIFI_SSID, WIFI_PASS, NODE_SERVER_URL, APPS_SCRIPT_URL
#include <HTTPClient.h>
#include <WiFi.h>
#include <string.h>

// Google Sheets se sincroniza desde Servidor.py en segundo plano.
// Mantener este envio directo apagado evita duplicados y bloqueos HTTPS.
#define ENABLE_APPS_SCRIPT 1

#if ENABLE_APPS_SCRIPT
#include <WiFiClientSecure.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#endif

// Calibracion sensores
#define V_CALIBRATION 155.8
#define CURR_CALIBRATION 2.3

// Tiempos de medicion y envio
const uint32_t SERIAL_BAUD = 115200;
const uint8_t NUM_MUESTRAS = 5;
const uint8_t ZERO_CROSSINGS = 20;
const uint16_t MEASUREMENT_TIMEOUT_MS = 2000;
const uint32_t SAMPLE_INTERVAL_MS = 1000UL;
const uint32_t LOCAL_SEND_INTERVAL_MS = 1000UL;
const uint32_t APPS_SCRIPT_INTERVAL_MS = 5000UL;

// Tiempos de red
const uint32_t WIFI_CONNECT_TIMEOUT_MS = 15000UL;
const uint32_t WIFI_RETRY_INTERVAL_MS = 10000UL;
const uint16_t LOCAL_HTTP_TIMEOUT_MS = 1000;
const uint16_t APPS_HTTP_TIMEOUT_MS = 18000;

struct Measurement {
  float vrms;
  float irms;
  float power;
  double kWh;
  uint32_t uptimeMs;
};

EnergyMonitor emon;
double kWh = 0.0;
uint32_t lastMillis = 0;
Measurement latestMeasurement = {};
bool hasMeasurement = false;

#if ENABLE_APPS_SCRIPT
QueueHandle_t appsScriptQueue = nullptr;
#endif

bool connectWiFi(uint32_t timeoutMs) {
  if (WiFi.status() == WL_CONNECTED) {
    return true;
  }

  Serial.print(F("[WiFi] Conectando"));
  WiFi.disconnect(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  const uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < timeoutMs) {
    delay(250);
    Serial.print('.');
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print(F("\n[WiFi] IP: "));
    Serial.println(WiFi.localIP());
    return true;
  }

  Serial.println(F("\n[WiFi] Sin conexion; seguire midiendo y reintentare."));
  return false;
}

#if ENABLE_APPS_SCRIPT
void sendToAppsScript(const Measurement &measurement) {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }

  char json[220];
  const int len =
      snprintf(json, sizeof(json),
               "{\"vrms\":%.2f,\"irms\":%.4f,\"power\":%.4f,\"kWh\":%.6f}",
               measurement.vrms, measurement.irms, measurement.power,
               measurement.kWh);
  if (len <= 0 || len >= (int)sizeof(json)) {
    Serial.println(F("[Apps] JSON demasiado largo."));
    return;
  }

  WiFiClientSecure client;
  client.setInsecure();

  HTTPClient http;
  if (!http.begin(client, APPS_SCRIPT_URL)) {
    Serial.println(F("[Apps] No se pudo iniciar HTTP."));
    return;
  }

  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
  http.addHeader(F("Content-Type"), F("application/json"));
  http.setTimeout(APPS_HTTP_TIMEOUT_MS);

  const uint32_t t0 = millis();
  const int code = http.POST((uint8_t *)json, strlen(json));
  const uint32_t dt = millis() - t0;

  if (code > 0) {
    Serial.printf("[Apps] HTTP %d (%lu ms)\n", code, (unsigned long)dt);
  } else {
    Serial.printf("[Apps] ERR %d (%lu ms)\n", code, (unsigned long)dt);
  }

  http.end();
}

void queueAppsScriptSend(const Measurement &measurement) {
  if (appsScriptQueue == nullptr) {
    return;
  }

  xQueueOverwrite(appsScriptQueue, &measurement);
}

void appsScriptTask(void *parameter) {
  Measurement measurement;

  for (;;) {
    if (xQueueReceive(appsScriptQueue, &measurement, portMAX_DELAY) == pdTRUE) {
      sendToAppsScript(measurement);
    }
  }
}
#endif

void sendToLocalServer(const Measurement &measurement) {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }

  char json[240];
  const int len =
      snprintf(json, sizeof(json),
               "{\"vrms\":%.2f,\"irms\":%.4f,\"power\":%.4f,\"kWh\":%.6f,"
               "\"uptime\":%lu}",
               measurement.vrms, measurement.irms, measurement.power,
               measurement.kWh, (unsigned long)measurement.uptimeMs);
  if (len <= 0 || len >= (int)sizeof(json)) {
    Serial.println(F("[Local] JSON demasiado largo."));
    return;
  }

  WiFiClient client;
  HTTPClient http;
  if (!http.begin(client, NODE_SERVER_URL)) {
    Serial.println(F("[Local] No se pudo iniciar HTTP."));
    return;
  }

  http.addHeader(F("Content-Type"), F("application/json"));
  http.setTimeout(LOCAL_HTTP_TIMEOUT_MS);

  const int code = http.POST((uint8_t *)json, strlen(json));
  if (code > 0) {
    Serial.printf("[Local] HTTP %d\n", code);
  } else {
    Serial.printf("[Local] ERR %d; revisa NODE_SERVER_URL en config.h\n", code);
  }

  http.end();
}

Measurement readMeasurement() {
  double sumVrms = 0.0;
  double sumIrms = 0.0;
  double sumPower = 0.0;

  for (uint8_t i = 0; i < NUM_MUESTRAS; i++) {
    emon.calcVI(ZERO_CROSSINGS, MEASUREMENT_TIMEOUT_MS);
    sumVrms += emon.Vrms;
    sumIrms += emon.Irms;
    sumPower += emon.apparentPower;
    yield();
  }

  Measurement measurement;
  measurement.vrms = sumVrms / NUM_MUESTRAS;
  measurement.irms = sumIrms / NUM_MUESTRAS;
  measurement.power = sumPower / NUM_MUESTRAS;
  measurement.uptimeMs = millis();

  if (lastMillis > 0) {
    kWh += measurement.power * (measurement.uptimeMs - lastMillis) /
           3600000000.0;
  }
  lastMillis = measurement.uptimeMs;
  measurement.kWh = kWh;

  return measurement;
}

void printMeasurement(const Measurement &measurement) {
  Serial.printf("Vrms: %.2fV  Irms: %.4fA  Power: %.4fW  kWh: %.6f\n",
                measurement.vrms, measurement.irms, measurement.power,
                measurement.kWh);
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(200);

  emon.voltage(35, V_CALIBRATION, 1.7);
  emon.current(34, CURR_CALIBRATION);

  WiFi.setAutoReconnect(true);
  connectWiFi(WIFI_CONNECT_TIMEOUT_MS);

#if ENABLE_APPS_SCRIPT
  appsScriptQueue = xQueueCreate(1, sizeof(Measurement));
  if (appsScriptQueue == nullptr) {
    Serial.println(F("[Apps] No se pudo crear la cola de envio."));
  } else {
    xTaskCreatePinnedToCore(appsScriptTask, "apps_script", 12288, nullptr, 1,
                            nullptr, 0);
  }
#endif

  lastMillis = millis();
}

void loop() {
  static uint32_t lastSample = 0;
  static uint32_t lastLocalSend = 0;
  static uint32_t lastAppsSend = 0;
  static uint32_t lastWiFiRetry = 0;
  const uint32_t now = millis();

  if (WiFi.status() != WL_CONNECTED &&
      now - lastWiFiRetry >= WIFI_RETRY_INTERVAL_MS) {
    lastWiFiRetry = now;
    connectWiFi(5000);
  }

  if (now - lastSample >= SAMPLE_INTERVAL_MS) {
    lastSample = now;
    latestMeasurement = readMeasurement();
    hasMeasurement = true;
    printMeasurement(latestMeasurement);
  }

  if (hasMeasurement && now - lastLocalSend >= LOCAL_SEND_INTERVAL_MS) {
    lastLocalSend = now;
    sendToLocalServer(latestMeasurement);
  }

#if ENABLE_APPS_SCRIPT
  if (hasMeasurement && now - lastAppsSend >= APPS_SCRIPT_INTERVAL_MS) {
    lastAppsSend = now;
    queueAppsScriptSend(latestMeasurement);
  }
#endif
}
