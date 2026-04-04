require('dotenv').config();
const express = require('express');
const cors = require('cors');
const { WebSocketServer } = require('ws');
const http = require('http');
const path = require('path');
const { spawn } = require('child_process');
const db = require('./database');

const app = express();
const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: '/ws' });

const PORT = 5000;
const R_CABLE = 0.0627;
const V_IDEAL = 110.0;
const V_TOL_PCT = 0.05;
const Z_UMBRAL = 2.0;

app.use(cors());
app.use(express.json());

// Sirviendo el Dashboard desde el directorio padre
const publicPath = path.join(__dirname, '..');
app.use(express.static(publicPath));

// Función para cálculo básico de Z-score (mientras no hay ML)
function calcZScore(valor, historico) {
  if (historico.length < 5) return 0;
  const sum = historico.reduce((a, b) => a + b, 0);
  const mean = sum / historico.length;
  const sd = Math.sqrt(historico.map(x => Math.pow(x - mean, 2)).reduce((a, b) => a + b) / historico.length);
  return sd > 0 ? Math.abs(valor - mean) / sd : 0;
}

// ==========================================
// ENDPOINTS DEL ESP32
// ==========================================
app.post('/data', (req, res) => {
  try {
    const data = req.body;
    const vrms = parseFloat(data.vrms || 0);
    const irms = parseFloat(data.irms || 0);
    const power = parseFloat(data.power || 0);
    const kwh = parseFloat(data.kWh || data.kwh || 0);
    const uptime = parseInt(data.uptime || 0);
    const joule = Math.pow(irms, 2) * R_CABLE;

    const enCalibracion = uptime > 0 && uptime < (8 * 60 * 1000); // Primeros 8 minutos

    const fila = {
      fecha: new Date().toISOString(),
      vrms, irms, power, kwh, joule,
      es_anomalia: false,
      anomalias: {}
    };

    // Lógica básica temporal Z-Score hasta que ML envíe actualizaciones
    fila.anomalias.vrms = Math.abs(vrms - V_IDEAL) > (V_IDEAL * V_TOL_PCT);
    
    // Obtener últimos para el zscore rápido
    const records = db.getReadings(100);
    for (const campo of ['irms', 'power', 'kwh', 'joule']) {
      const historyVals = records.map(r => r[campo]).filter(v => v > 0);
      const z = calcZScore(fila[campo], historyVals);
      fila.anomalias[campo] = z > Z_UMBRAL;
    }
    
    fila.es_anomalia = Object.values(fila.anomalias).some(v => v);

    // Si está en calibración, NO disparamos alertas de nada porque los filtros aún vibran
    if (enCalibracion) {
      fila.es_anomalia = false;
      Object.keys(fila.anomalias).forEach(k => fila.anomalias[k] = false);
      fila.anomalias.sistema_calibrando = true;
    }

    // Guardar en SQLite
    const result = db.insertReading(fila);
    fila.id = result.lastInsertRowid;

    // Enviar a todos los WebSockets conectados
    wss.clients.forEach(client => {
      if (client.readyState === 1 /* OPEN */) {
        client.send(JSON.stringify({ type: 'new_reading', data: fila }));
      }
    });

    console.log(`[ESP32] Vrms: ${vrms}V, Irms: ${irms}A -> Anomalia: ${fila.es_anomalia}`);
    res.json({ ok: true });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: err.message });
  }
});

// ==========================================
// ENDPOINTS DE CONSULTA (Para ML y Dashboard)
// ==========================================
app.get('/data', (req, res) => {
  // Retorna datos en el formato de celdas que espera el script Python
  res.json(db.getAllRowsFormat());
});

app.get('/status', (req, res) => {
  res.json(db.getStatus());
});

// ==========================================
// ENDPOINTS PARA EL MÓDULO MACHINE LEARNING
// ==========================================
app.post('/ml/result', (req, res) => {
  try {
    const payload = req.body;
    
    // Guardar resultados
    const result = db.insertMLResult(payload);
    
    // Notificar al Dashboard que hay un análisis nuevo!
    wss.clients.forEach(client => {
      if (client.readyState === 1 /* OPEN */) {
        client.send(JSON.stringify({
          type: 'ml_result',
          data: payload,
          db_id: result.lastInsertRowid
        }));
      }
    });

    console.log(`[ML] Resultado recibido: ${payload.n_anomalias}/${payload.n_lecturas} anomalías.`);
    res.json({ ok: true, db_id: result.lastInsertRowid });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: err.message });
  }
});

app.get('/ml/results', (req, res) => {
  res.json({ ok: true, results: db.getMLResults() });
});

// ==========================================
// ENDPOINT CHATBOT DEEPSEEK (Seguro)
// ==========================================
app.post('/chat', async (req, res) => {
  try {
    const apiKey = process.env.DEEPSEEK_API_KEY;
    if (!apiKey || apiKey === 'sk-tu-api-key-aqui') {
      return res.status(400).json({ error: { message: "API Key no configurada en el archivo .env del backend." } });
    }

    const { messages } = req.body;
    
    // Llamada segura desde el Node.js hacia la IA usando el fetch nativo
    const apiRes = await fetch('https://api.deepseek.com/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${apiKey}`
      },
      body: JSON.stringify({
        model: 'deepseek-chat',
        max_tokens: 500,
        messages: messages
      })
    });
    
    const data = await apiRes.json();
    res.json(data);
  } catch (error) {
    console.error('[Chat API Error]', error);
    res.status(500).json({ error: { message: error.message } });
  }
});

// ==========================================
// WEBSOCKET SERVER
// ==========================================
wss.on('connection', (ws) => {
  console.log('[WS] Nuevo cliente conectado al dashboard.');
  
  // Enviar historial inicial
  const records = db.getReadings(500);
  ws.send(JSON.stringify({
    type: 'history',
    data: records
  }));

  ws.on('close', () => {
    console.log('[WS] Cliente desconectado.');
  });
});

// INICIAR SERVIDOR
server.listen(PORT, '0.0.0.0', () => {
  console.log("=".repeat(50));
  console.log("  Watios - Backend de Monitoreo Node.js");
  console.log("=".repeat(50));
  console.log(`  http://localhost:${PORT}`);
  console.log(`  http://localhost:${PORT}/data     (ESP32 / ML leen aquí)`);
  console.log(`  http://localhost:${PORT}/ml/result (ML guarda aquí)`);
  console.log(`  http://localhost:${PORT}/chat      (API segura Bot)`);
  console.log(`  ws://localhost:${PORT}/ws         (Dashboard en vivo)`);
  console.log("=".repeat(50));
  
  // ======== AUTO-EJECUTAR MÓDULO MACHINE LEARNING ========
  console.log("[Node] Auto-Iniciando el motor de Machine Learning en Python...");
  const pyPath = path.join(__dirname, '..', 'ml', 'anomalias_tf.py');
  
  // Lanzamos el .py en modo daemon 
  const mlProcess = spawn('python', [pyPath, '--daemon']);
  
  mlProcess.stdout.on('data', data => {
    // format logging
    const lines = data.toString().split('\n');
    lines.forEach(l => { if (l.trim()) console.log(`[ML-IA] ${l.trim()}`) });
  });
  
  mlProcess.stderr.on('data', data => {
    const lines = data.toString().split('\n');
    lines.forEach(l => { if (l.trim()) console.error(`[ML-IA] ${l.trim()}`) });
  });

  mlProcess.on('close', code => {
    console.log(`[ML-IA] El proceso Python terminó con código: ${code}`);
  });
});
