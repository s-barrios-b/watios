#!/usr/bin/env python3
"""
Genera dos archivos a partir del .env:
  - config.h  → credenciales para el firmware del ESP32 (C/C++)
  - config.js → variables del servidor para el dashboard (JavaScript)

Uso: python generar_config.py

Despues de editar cualquier valor en .env, ejecuta este script.
No toques config.h ni config.js a mano.
"""
import os
from dotenv import load_dotenv

load_dotenv(override=True)

WIFI_SSID        = os.getenv("WIFI_SSID", "")
WIFI_PASS        = os.getenv("WIFI_PASS", "")
NODE_SERVER_URL  = os.getenv("NODE_SERVER_URL", "http://192.168.1.7:5000/data")
APPS_SCRIPT_URL  = os.getenv("APPS_SCRIPT_URL", "")
LOCAL_SERVER     = os.getenv("LOCAL_SERVER", "http://192.168.1.7:5000").rstrip("/")
PORT             = os.getenv("PORT", "5000")

errores = []
if not WIFI_SSID:       errores.append("WIFI_SSID")
if not WIFI_PASS:       errores.append("WIFI_PASS")
if not APPS_SCRIPT_URL: errores.append("APPS_SCRIPT_URL")

if errores:
    print(f"ERROR: Faltan en .env -> {', '.join(errores)}")
    raise SystemExit(1)

# ── 1. config.h (ESP32) ───────────────────────────────────────────────────────
config_h = f"""#ifndef CONFIG_H
#define CONFIG_H

// Generado automaticamente por generar_config.py
// NO edites este archivo a mano. Edita .env y vuelve a ejecutar el script.

#define WIFI_SSID       "{WIFI_SSID}"
#define WIFI_PASS       "{WIFI_PASS}"
#define NODE_SERVER_URL "{NODE_SERVER_URL}"
#define APPS_SCRIPT_URL "{APPS_SCRIPT_URL}"

#endif
"""

with open("config.h", "w", encoding="utf-8") as f:
    f.write(config_h)

print("config.h generado correctamente.")
print(f"  WIFI_SSID       = {WIFI_SSID}")
print(f"  NODE_SERVER_URL = {NODE_SERVER_URL}")
print(f"  APPS_SCRIPT_URL = {APPS_SCRIPT_URL[:40]}...")

# ── 2. config.js (dashboard) ──────────────────────────────────────────────────
# Convierte la URL HTTP del servidor en URL WebSocket (ws://)
ws_url = LOCAL_SERVER.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
http_base = LOCAL_SERVER

config_js = f"""// Generado automaticamente por generar_config.py
// NO edites este archivo a mano. Edita .env y vuelve a ejecutar el script.

const WATIOS_WS_URL     = "{ws_url}";      // WebSocket del dashboard
const WATIOS_SERVER_URL = "{http_base}";   // Base HTTP para /chat, /data, etc.
"""

with open("config.js", "w", encoding="utf-8") as f:
    f.write(config_js)

print("\nconfig.js generado correctamente.")
print(f"  WATIOS_WS_URL     = {ws_url}")
print(f"  WATIOS_SERVER_URL = {http_base}")

