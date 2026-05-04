#!/usr/bin/env python3
"""
Watios -- Servidor local (FastAPI)
----------------------------------
* POST /data      -> recibe JSON del ESP32, guarda en RAM, broadcast WebSocket
* GET  /data      -> entrega historial como { rows: [[encabezado], [fila], ...] }
* WS   /ws        -> WebSocket en tiempo real para el dashboard
* POST /chat      -> proxy seguro a DeepSeek (la API key nunca va al browser)
* POST /ml/result -> recibe resultados del modulo ML y los reenvía al dashboard
"""

import os, sys, csv, json, asyncio, subprocess
from collections import deque
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Any, Optional

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv(override=True)

# -- Configuracion (editar en .env) ------------------------------------
PORT             = int(os.getenv("PORT", "5000"))
MAX_ROWS         = int(os.getenv("MAX_ROWS", "10000"))
_r_cable = os.getenv("R_CABLE")
if _r_cable is None:
    raise RuntimeError("R_CABLE no definido en .env — agrega R_CABLE=0.066 y reinicia el servidor.")
R_CABLE          = float(_r_cable)           # editar SOLO en .env
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip() or None
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/v1/chat/completions").strip()
DEEPSEEK_MODEL   = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
APPS_SCRIPT_URL  = os.getenv("APPS_SCRIPT_URL", "").strip()
EXCEL_SYNC       = os.getenv("EXCEL_SYNC", "1").strip().lower() not in {"0", "false", "no", "off"}
EXCEL_SYNC_INTERVAL = float(os.getenv("EXCEL_SYNC_INTERVAL", "1"))
AUTO_START_ML    = os.getenv("AUTO_START_ML", "1").strip().lower() not in {"0", "false", "no", "off"}
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
ML_SCRIPT        = os.path.join(BASE_DIR, "anomaliastf.py")
ML_ARTIFACTS_DIR = os.path.join(BASE_DIR, "analisis_anomalias")
ML_LOG_FILE      = os.path.join(ML_ARTIFACTS_DIR, "anomaliastf.log")
TRAINING_DATA_FILE = os.path.join(ML_ARTIFACTS_DIR, "datos_entrenamiento.csv")

HEADER = ["fecha", "vrms", "irms", "power", "kwh", "joule"]


def format_fecha_csv(value) -> str:
    """Fecha compacta y editable, similar a Google Sheets."""
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if "/" in text and "T" not in text:
            return text
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
    return f"{dt.day}/{dt.month:02d}/{dt.year} {dt:%H:%M:%S}"


def format_decimal_csv(value) -> str:
    """Evita notacion cientifica y conserva los decimales disponibles."""
    if value is None or value == "":
        return ""
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return str(value)
    if not number.is_finite():
        return str(value)
    text = format(number.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"

# -- Estado en RAM y Persistencia en disco -----------------------------
ROWS: deque = deque(maxlen=MAX_ROWS)

# -- FastAPI -----------------------------------------------------------
app = FastAPI(title="Watios Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -- Gestor WebSocket --------------------------------------------------
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
            except Exception as e:
                print(f"[WS] Error en broadcast: {e}")
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

manager = ConnectionManager()
ML_PROCESS: Optional[subprocess.Popen] = None
ML_LOG_HANDLE = None
EXCEL_QUEUE: deque = deque(maxlen=5000)
EXCEL_SYNC_TASK: Optional[asyncio.Task] = None


def ensure_training_csv():
    os.makedirs(ML_ARTIFACTS_DIR, exist_ok=True)
    if not os.path.exists(TRAINING_DATA_FILE) or os.path.getsize(TRAINING_DATA_FILE) == 0:
        with open(TRAINING_DATA_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(HEADER)


def row_from_csv(record: dict) -> Optional[dict]:
    normalized = {
        "fecha": record.get("fecha") or record.get("Fecha") or "",
        "vrms": _to_float(record, "vrms", "Vrms", "Vrms (V)"),
        "irms": _to_float(record, "irms", "Irms", "Irms (A)"),
        "power": _to_float(record, "power", "Power", "Potencia (W)"),
        "kwh": _to_float(record, "kwh", "kWh"),
        "joule": _to_float(record, "joule", "P. Joule (W)"),
        "anomalias": {"sistema_calibrando": False},
    }
    if not normalized["fecha"] or normalized["vrms"] <= 0:
        return None
    return normalized


def load_rows_from_training_csv():
    ensure_training_csv()
    loaded = deque(maxlen=MAX_ROWS)
    try:
        with open(TRAINING_DATA_FILE, "r", newline="", encoding="utf-8-sig") as f:
            for record in csv.DictReader(f):
                row = row_from_csv(record)
                if row:
                    loaded.append(row)
    except Exception as e:
        print(f"[Base de Datos] Error leyendo CSV editable: {e}")
    ROWS.clear()
    ROWS.extend(loaded)
    return len(ROWS)


def append_row_to_training_csv(row: dict):
    ensure_training_csv()
    with open(TRAINING_DATA_FILE, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            format_fecha_csv(row.get("fecha", "")),
            format_decimal_csv(row.get("vrms", "")),
            format_decimal_csv(row.get("irms", "")),
            format_decimal_csv(row.get("power", "")),
            format_decimal_csv(row.get("kwh", "")),
            format_decimal_csv(row.get("joule", "")),
        ])


def excel_payload(row: dict) -> dict:
    return {
        "fecha": row.get("fecha", ""),
        "vrms": row.get("vrms", 0),
        "irms": row.get("irms", 0),
        "power": row.get("power", 0),
        "kWh": row.get("kwh", 0),
        "joule": row.get("joule", 0),
    }


def is_valid_excel_payload(payload: dict) -> bool:
    """Evita enviar lecturas incompletas que Apps Script podria convertir en filas vacias."""
    for key in ("vrms", "irms", "power", "kWh"):
        try:
            if float(payload.get(key, 0)) <= 0:
                return False
        except (TypeError, ValueError):
            return False
    return True


async def sync_excel_loop():
    if not EXCEL_SYNC or not APPS_SCRIPT_URL:
        print("[Excel] Sincronizacion desactivada.")
        return

    print(f"[Excel] Sincronizacion en segundo plano cada {EXCEL_SYNC_INTERVAL:.2f}s.")
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        while True:
            await asyncio.sleep(max(0.2, EXCEL_SYNC_INTERVAL))
            if not EXCEL_QUEUE:
                continue

            batch = []
            while EXCEL_QUEUE and len(batch) < 50:
                batch.append(EXCEL_QUEUE.popleft())

            failed = []
            for payload in batch:
                if not is_valid_excel_payload(payload):
                    print(f"[Excel] Lectura incompleta descartada: {payload}")
                    continue

                # Enviar una lectura plana mantiene compatibilidad con despliegues
                # anteriores de Apps Script y evita filas vacias con #NUM!.
                try:
                    res = await client.post(APPS_SCRIPT_URL, json=payload)
                    if res.status_code >= 400:
                        print(f"[Excel] Error HTTP {res.status_code}; se reintentara luego.")
                        failed.append(payload)
                except Exception as e:
                    print(f"[Excel] Error enviando lectura: {e}")
                    failed.append(payload)

            if failed:
                EXCEL_QUEUE.extendleft(reversed(failed))


def start_ml_daemon():
    """Arranca anomaliastf.py en segundo plano junto con el servidor."""
    global ML_PROCESS, ML_LOG_HANDLE
    if not AUTO_START_ML:
        print("[ML] Autoarranque desactivado (AUTO_START_ML=0).")
        return
    if ML_PROCESS and ML_PROCESS.poll() is None:
        return
    if not os.path.exists(ML_SCRIPT):
        print(f"[ML] No se encontro el modulo: {ML_SCRIPT}")
        return

    env = os.environ.copy()
    env["LOCAL_SERVER"] = f"http://127.0.0.1:{PORT}"
    try:
        os.makedirs(ML_ARTIFACTS_DIR, exist_ok=True)
        ML_LOG_HANDLE = open(ML_LOG_FILE, "a", encoding="utf-8")
        ML_PROCESS = subprocess.Popen(
            [sys.executable, ML_SCRIPT, "--daemon"],
            cwd=BASE_DIR,
            env=env,
            stdout=ML_LOG_HANDLE,
            stderr=subprocess.STDOUT,
        )
        print(f"[ML] anomaliastf.py iniciado en segundo plano (pid={ML_PROCESS.pid}). Log: {ML_LOG_FILE}")
    except Exception as e:
        if ML_LOG_HANDLE:
            ML_LOG_HANDLE.close()
            ML_LOG_HANDLE = None
        print(f"[ML] Error iniciando anomaliastf.py: {e}")


def stop_ml_daemon():
    """Detiene el proceso ML hijo cuando se apaga FastAPI."""
    global ML_PROCESS, ML_LOG_HANDLE
    if not ML_PROCESS or ML_PROCESS.poll() is not None:
        return
    print("[ML] Deteniendo anomaliastf.py...")
    ML_PROCESS.terminate()
    try:
        ML_PROCESS.wait(timeout=10)
    except subprocess.TimeoutExpired:
        ML_PROCESS.kill()
    ML_PROCESS = None
    if ML_LOG_HANDLE:
        ML_LOG_HANDLE.close()
        ML_LOG_HANDLE = None


@app.on_event("startup")
async def startup_event():
    global EXCEL_SYNC_TASK
    total = load_rows_from_training_csv()
    print(f"[Base de Datos] Cargadas {total} filas desde CSV editable: {TRAINING_DATA_FILE}")
    if EXCEL_SYNC and APPS_SCRIPT_URL:
        EXCEL_SYNC_TASK = asyncio.create_task(sync_excel_loop())
    start_ml_daemon()


@app.on_event("shutdown")
async def shutdown_event():
    global EXCEL_SYNC_TASK
    if EXCEL_SYNC_TASK:
        EXCEL_SYNC_TASK.cancel()
        EXCEL_SYNC_TASK = None
    stop_ml_daemon()

# -- Helpers -----------------------------------------------------------
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
    joule = _to_float(payload, "joule") if "joule" in payload else round(irms ** 2 * R_CABLE, 12)
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

# -- Rutas HTTP --------------------------------------------------------

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
    ROWS.append(row)   # deque descarta automaticamente los mas antiguos si llega a maxlen
    client_host = request.client.host if request.client else "desconocido"
    print(f"[DATA] Lectura recibida desde {client_host}: Vrms={row['vrms']} Irms={row['irms']} Power={row['power']}")

    # Guardar la fila persistentemente en el CSV editable.
    try:
        append_row_to_training_csv(row)
    except Exception as e:
        print(f"[Base de Datos] Error guardando fila en CSV: {e}")
    if EXCEL_SYNC and APPS_SCRIPT_URL:
        EXCEL_QUEUE.append(excel_payload(row))

    asyncio.create_task(manager.broadcast({"type": "new_reading", "data": row}))
    return JSONResponse({"status": "ok", "row": row})


@app.get("/data")
async def get_data():
    """Entrega el historial completo en formato { rows: [[encabezado], ...] }."""
    load_rows_from_training_csv()
    rows_list = [HEADER]
    for r in ROWS:
        rows_list.append([
            format_fecha_csv(r.get("fecha", "")),
            format_decimal_csv(r.get("vrms", "")),
            format_decimal_csv(r.get("irms", "")),
            format_decimal_csv(r.get("power", "")),
            format_decimal_csv(r.get("kwh", "")),
            format_decimal_csv(r.get("joule", "")),
        ])
    return JSONResponse({"rows": rows_list})


@app.get("/health")
async def health():
    """Estado rapido para confirmar que el servidor esta vivo."""
    return JSONResponse({
        "status": "ok",
        "lecturas_en_memoria": len(ROWS),
        "csv_datos": TRAINING_DATA_FILE,
        "ml_autoarranque": AUTO_START_ML,
        "ml_estado": "corriendo" if ML_PROCESS and ML_PROCESS.poll() is None else "detenido",
        "excel_sync": EXCEL_SYNC and bool(APPS_SCRIPT_URL),
        "excel_pendientes": len(EXCEL_QUEUE),
    })


@app.post("/ml/result")
async def ml_result(request: Request):
    """Recibe resultados del modulo anomaliastf.py y los reenvía al dashboard."""
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
    Proxy async hacia DeepSeek (no bloquea el event loop).
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
            "temperature": float(os.getenv("DEEPSEEK_TEMPERATURE", "0.3")),
        }
        # httpx.AsyncClient: no bloquea el event loop de FastAPI
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(DEEPSEEK_API_URL, json=body_payload, headers=headers)
        result = r.json()
        usage  = result.get("usage", {})
        print(f"[DeepSeek] prompt={usage.get('prompt_tokens','?')} completion={usage.get('completion_tokens','?')} tokens")
        return JSONResponse(result, status_code=r.status_code)
    except Exception as ex:
        print(f"[DeepSeek] Error: {ex}")
        return JSONResponse({"error": str(ex)}, status_code=500)


# -- WebSocket ---------------------------------------------------------
KEEPALIVE_INTERVAL = 20   # segundos entre pings para mantener la conexion abierta

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    print("[WS] Conexion abierta")
    try:
        load_rows_from_training_csv()
        await websocket.send_text(json.dumps(
            {"type": "history", "data": list(ROWS)}, default=str
        ))
        while True:
            try:
                # Espera un mensaje del cliente hasta KEEPALIVE_INTERVAL segundos
                await asyncio.wait_for(websocket.receive_text(), timeout=KEEPALIVE_INTERVAL)
            except asyncio.TimeoutError:
                # No llegó mensaje: enviar ping para mantener la conexion viva
                await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        print("[WS] Cliente desconectado normalmente")
        manager.disconnect(websocket)
    except Exception as e:
        print(f"[WS] Conexion cerrada inesperadamente: {e}")
        manager.disconnect(websocket)


# -- Arranque ----------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    print(f"Watios Server  -> http://0.0.0.0:{PORT}")
    print(f"WebSocket      -> ws://0.0.0.0:{PORT}/ws")
    print(f"DeepSeek       -> {'OK' if DEEPSEEK_API_KEY else 'SIN CLAVE - agrega DEEPSEEK_API_KEY en .env'}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
