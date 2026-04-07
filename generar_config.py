#!/usr/bin/env python3
"""
Genera archivos de configuracion a partir del .env:
  - config.h  → credenciales para el firmware del ESP32 (C/C++)
  - config.js → variables del servidor para el dashboard (JavaScript)

Uso: python generar_config.py

Despues de editar CUALQUIER valor en .env, ejecuta este script.
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
R_CABLE          = os.getenv("R_CABLE", "")   # resistencia del cable (Ω)

# ── Validacion ────────────────────────────────────────────────────────────────
errores = []
if not WIFI_SSID:       errores.append("WIFI_SSID")
if not WIFI_PASS:       errores.append("WIFI_PASS")
if not APPS_SCRIPT_URL: errores.append("APPS_SCRIPT_URL")
if not R_CABLE:         errores.append("R_CABLE")

if errores:
    print(f"ERROR: Faltan en .env -> {', '.join(errores)}")
    raise SystemExit(1)

try:
    r_cable_float = float(R_CABLE)
except ValueError:
    print(f"ERROR: R_CABLE='{R_CABLE}' no es un número valido.")
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
ws_url    = LOCAL_SERVER.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
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

import re

try:
    with open("apps_script.gs", "r", encoding="utf-8", errors="ignore") as f:
        apps_script = f.read()
    
    apps_script = re.sub(r'var R_cable\s*=\s*[0-9.]+;', f'var R_cable   = {r_cable_float};', apps_script)
    
    with open("apps_script.gs", "w", encoding="utf-8") as f:
        f.write(apps_script)
    print("\napps_script.gs actualizado correctamente.")
    print(f"  R_CABLE en Apps Script modificado a: {r_cable_float} Ω")
except Exception as e:
    print(f"No se pudo actualizar apps_script.gs: {e}")

# ── 3. Resumen de parametros locales (no generan archivo, son leidos por Python) ─
print("\nParametros locales (leidos directamente del .env por los scripts Python):")
print(f"  R_CABLE  = {r_cable_float} Ω  →  usado en Servidor.py, anomaliastf.py y Apps Script")
print(f"  PORT     = {PORT}")
print(f"  LOCAL_SERVER = {LOCAL_SERVER}")
print("\n✓ Configuracion sincronizada. Si cambiaste R_CABLE, recuerda pegar nuevamente apps_script.gs en Google Apps Script, y reiniciar Servidor.py y anomaliastf.py.")
