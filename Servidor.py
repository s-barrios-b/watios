#!/usr/bin/env python3
"""
Watios — Servidor local (FastAPI)
──────────────────────────────────
• POST /data   → recibe JSON del ESP32, guarda en RAM, broadcast WebSocket
• GET  /data   → entrega historial como { rows: [[encabezado], [fila], ...] }
• WS   /ws     → WebSocket en tiempo real para el dashboard
• POST /chat   → proxy seguro a DeepSeek (la API key nunca va al browser)
"""

import os, json, asyncio
from datetime import datetime
from typing import List, Dict, Any

import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

# ── Configuración (editar en .env) ─────────────────────────────
PORT             = int(os.getenv("PORT", "5000"))
MAX_ROWS         = int(os.getenv("MAX_ROWS", "10000"))
R_CABLE          = float(os.getenv("R_CABLE", "0.0627"))
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip() or None
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/v1/chat/completions").strip()
DEEPSEEK_MODEL   = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

HEADER = ["fecha", "vrms", "irms", "power", "kwh", "joule"]

# ── Estado en RAM ──────────────────────────────────────────────
ROWS: List[Dict[str, Any]] = []

# ── FastAPI ────────────────────────────────────────────────────
app = FastAPI(title="Watios Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Gestor WebSocket ───────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self._conns: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._conns.append(ws)

    def disconnect(self, ws: WebSocket):
        self._conns = [c for c in self._conns if c is not ws]

    async def broadcast(self, msg: dict):
        if not self._conns:
            return
        text = json.dumps(msg, default=str)
        dead = []
        for ws in list(self._conns):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

manager = ConnectionManager()

# ── Helpers ────────────────────────────────────────────────────
def _to_float(payload, *names, default=0.0) -> float:
    for name in names:
        v = payload.get(name)
        if v is not None and v != "":
            try:
                return float(v)
            except Exception:
                try:
                    return float(str(v).replace(",", "."))
                except Exception:
                    pass
    return default


def parse_row(payload: dict) -> dict:
    """Normaliza un payload del ESP32 en una fila estandarizada."""
    vrms  = _to_float(payload, "vrms",  "Vrms")
    irms  = _to_float(payload, "irms",  "Irms")
    power = _to_float(payload, "power", "apparentPower")
    kwh   = _to_float(payload, "kWh",   "kwh")
    joule = _to_float(payload, "joule") if "joule" in payload else round(irms ** 2 * R_CABLE, 6)
    fecha = payload.get("fecha") or payload.get("timestamp") or datetime.now().isoformat()

    uptime = payload.get("uptime")
    calibrando = False
    if uptime is not None:
        try:
            calibrando = 0 <= float(uptime) < 480_000   # primeros 8 min
        except (TypeError, ValueError):
            pass

    return {
        "fecha":  fecha,
        "vrms":   round(vrms,  2),
        "irms":   round(irms,  6),
        "power":  round(power, 4),
        "kwh":    round(kwh,   6),
        "joule":  joule,
        "anomalias": {"sistema_calibrando": calibrando},
    }

# ── Rutas HTTP ─────────────────────────────────────────────────

@app.post("/data")
async def post_data(request: Request):
    """Recibe JSON del ESP32 y hace broadcast por WebSocket."""
    try:
        payload = await request.json()
    except Exception:
        body = await request.body()
        payload = json.loads(body.decode() or "{}")

    if isinstance(payload, list) and payload:
        payload = payload[0]

    row = parse_row(payload)
    ROWS.append(row)
    if len(ROWS) > MAX_ROWS:
        ROWS.pop(0)

    asyncio.create_task(manager.broadcast({"type": "new_reading", "data": row}))
    return JSONResponse({"status": "ok", "row": row})


@app.get("/data")
async def get_data():
    """Entrega el historial completo en formato { rows: [[encabezado], ...] }."""
    rows_list = [HEADER]
    for r in ROWS:
        rows_list.append([
            r.get("fecha", ""),
            str(r.get("vrms", "")),
            str(r.get("irms", "")),
            str(r.get("power", "")),
            str(r.get("kwh", "")),
            str(r.get("joule", "")),
        ])
    return JSONResponse({"rows": rows_list})


@app.post("/ml/result")
async def ml_result(request: Request):
    """Recibe resultados del módulo Anomalias tf.py y los reenvía al dashboard."""
    try:
        payload = await request.json()
    except Exception:
        body = await request.body()
        payload = json.loads(body.decode() or "{}")

    await manager.broadcast({
        "type":        "ml_result",
        "n_lecturas":  payload.get("n_lecturas"),
        "n_anomalias": payload.get("n_anomalias"),
        "tasa_pct":    payload.get("tasa_pct"),
        "umbral_mse":  payload.get("umbral_mse"),
        "modelo":      payload.get("modelo"),
    })
    return JSONResponse({"status": "ok"})


@app.post("/chat")
async def chat_proxy(request: Request):
    """
    Proxy seguro hacia DeepSeek.
    El dashboard envía { messages: [...] }, el servidor añade la API key.
    """
    try:
        payload = await request.json()
    except Exception:
        body = await request.body()
        payload = json.loads(body.decode() or "{}")

    messages = payload.get("messages") or []

    if not DEEPSEEK_API_KEY:
        return JSONResponse({
            "choices": [{"message": {"content":
                "No hay DEEPSEEK_API_KEY en el archivo .env del servidor."
            }}],
            "usage": {"total_tokens": 0}
        })

    try:
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }
        body_payload = {
            "model":       DEEPSEEK_MODEL,
            "messages":    messages,
            "max_tokens":  int(os.getenv("DEEPSEEK_MAX_TOKENS", "600")),
            "temperature": float(os.getenv("DEEPSEEK_TEMPERATURE", "0.3")),  # respuestas enfocadas
        }
        r = requests.post(DEEPSEEK_API_URL, json=body_payload, headers=headers, timeout=30)
        result = r.json()
        # Log uso de tokens en consola
        usage = result.get("usage", {})
        print(f"[DeepSeek] ↑{usage.get('prompt_tokens','?')} ↓{usage.get('completion_tokens','?')} tokens")
        return JSONResponse(result, status_code=r.status_code)
    except Exception as ex:
        return JSONResponse({"error": str(ex)}, status_code=500)


# ── WebSocket ──────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Enviar historial al conectarse
        await websocket.send_text(json.dumps(
            {"type": "history", "data": ROWS}, default=str
        ))
        while True:
            await websocket.receive_text()   # mantener viva la conexión
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


# ── Arranque ───────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print(f"Watios Server  -> http://0.0.0.0:{PORT}")
    print(f"WebSocket      -> ws://0.0.0.0:{PORT}/ws")
    print(f"DeepSeek       -> {'OK' if DEEPSEEK_API_KEY else 'SIN CLAVE - agrega DEEPSEEK_API_KEY en .env'}")
    uvicorn.run("Servidor:app", host="0.0.0.0", port=PORT, log_level="info", reload=False)
