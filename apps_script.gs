// ════════════════════════════════════════════════════════════
//  WATIOS — Google Apps Script
//  Recibe datos del ESP32 (POST), los guarda en Google Sheets
//  y los expone al dashboard (GET + descarga CSV).
//
//  HOW TO USE:
//    1. Pega este código en tu Google Apps Script.
//    2. Ejecuta crearHojas() una sola vez para inicializar.
//    3. Despliega como Web App (acceso: Anyone, even anonymous).
//    4. Copia la URL de despliegue en INTENTO2.ino (scriptURL).
// ════════════════════════════════════════════════════════════

// ── Inicializar hojas ────────────────────────────────────────
function crearHojas() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  // Hoja "Datos"
  var datos = ss.getSheetByName("Datos");
  var esNueva = !datos;
  if (!datos) datos = ss.insertSheet("Datos");

  if (esNueva || datos.getLastRow() === 0) {
    datos.appendRow(["Fecha", "Vrms (V)", "Irms (A)", "Potencia (W)", "kWh", "Joule (J)"]);
  }
  datos.getRange(1, 1, 1, 6)
    .setFontWeight("bold")
    .setBackground("#1a73e8")
    .setFontColor("#ffffff");
  datos.setFrozenRows(1);
  datos.setColumnWidth(1, 180);
  for (var c = 2; c <= 6; c++) datos.setColumnWidth(c, 120);

  // Hoja "Graficas"
  var graficas = ss.getSheetByName("Graficas");
  if (graficas) ss.deleteSheet(graficas);
  graficas = ss.insertSheet("Graficas");
  Utilities.sleep(500);
  _crearGraficas(ss, datos, graficas);
  _crearResumen(graficas);

  SpreadsheetApp.getUi().alert(
    "✅ Watios listo\n\n" +
    "Hoja 'Datos' configurada.\n" +
    "Los datos existentes NO fueron borrados.\n\n" +
    "Para borrar todo usa reiniciarTodo()."
  );
}

// ── Crear gráficas en la hoja Graficas ───────────────────────
function _crearGraficas(ss, datos, graficas) {
  var MAX = 1000;
  var configs = [
    { col: 2, titulo: "Voltaje vs Tiempo (V)",           color: "#1a73e8", fila: 1,  col_anchor: 1  },
    { col: 3, titulo: "Corriente vs Tiempo (A)",         color: "#e53935", fila: 1,  col_anchor: 8  },
    { col: 4, titulo: "Potencia Aparente vs Tiempo (W)", color: "#43a047", fila: 23, col_anchor: 1  },
    { col: 5, titulo: "Energía vs Tiempo (kWh)",         color: "#fb8c00", fila: 23, col_anchor: 8  },
    { col: 6, titulo: "Joule Instantáneo vs Tiempo (J)", color: "#8e24aa", fila: 45, col_anchor: 1  }
  ];
  configs.forEach(function(cfg) {
    var chart = graficas.newChart()
      .setChartType(Charts.ChartType.LINE)
      .addRange(datos.getRange(1, 1, MAX, 1))
      .addRange(datos.getRange(1, cfg.col, MAX, 1))
      .setOption("title",  cfg.titulo)
      .setOption("legend", { position: "none" })
      .setOption("hAxis",  { title: "Tiempo", slantedText: true, slantedTextAngle: 45 })
      .setOption("vAxis",  { title: cfg.titulo, minValue: 0 })
      .setOption("colors", [cfg.color])
      .setOption("pointSize",  4)
      .setOption("lineWidth",  2)
      .setOption("width",      600)
      .setOption("height",     350)
      .setOption("backgroundColor", "#ffffff")
      .setPosition(cfg.fila, cfg.col_anchor, 0, 0)
      .build();
    graficas.insertChart(chart);
  });
}

// ── Tabla de resumen estadístico ─────────────────────────────
function _crearResumen(graficas) {
  var startRow = 67;
  graficas.getRange(startRow, 1, 1, 4)
    .merge()
    .setValue("Resumen Estadístico")
    .setFontWeight("bold").setFontSize(13)
    .setBackground("#1a73e8").setFontColor("#ffffff")
    .setHorizontalAlignment("center");

  graficas.getRange(startRow + 1, 1, 1, 4)
    .setValues([["Variable", "Mínimo", "Máximo", "Promedio"]])
    .setFontWeight("bold").setBackground("#e8f0fe").setFontColor("#1a73e8");

  ["Vrms (V)", "Irms (A)", "Potencia (W)", "kWh", "Joule (J)"].forEach(function(nombre, i) {
    var fila = startRow + 2 + i;
    graficas.getRange(fila, 1).setValue(nombre).setFontWeight("bold");
    graficas.getRange(fila, 2, 1, 3).setValue("--");
    graficas.getRange(fila, 1, 1, 4)
      .setBorder(true, true, true, true, true, true, "#cccccc", SpreadsheetApp.BorderStyle.SOLID);
  });

  [150, 110, 110, 110].forEach(function(w, i) { graficas.setColumnWidth(i + 1, w); });
}

// ── Borrar todo y recrear desde cero ─────────────────────
function reiniciarTodo() {
  var ui = SpreadsheetApp.getUi();
  var resp = ui.alert("⚠️ ADVERTENCIA",
    "Esto borrará TODOS los datos y formatos (celdas rojas). ¿Estás seguro?",
    ui.ButtonSet.YES_NO);
  if (resp !== ui.Button.YES) { ui.alert("Cancelado."); return; }

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var datos = ss.getSheetByName("Datos");

  // clear() borra CONTENIDO + FORMATOS (colores, negrita, etc.)
  // Esto elimina todas las celdas rojas de alertas anteriores
  if (datos) {
    datos.clear();                  // contenido + formatos
    datos.clearConditionalFormatRules(); // reglas condicionales si las hay
  }

  crearHojas();
}

// ════════════════════════════════════════════════════════════
//  doPost — Recibe JSON del ESP32 y guarda una fila en Sheets
// ════════════════════════════════════════════════════════════
function doPost(e) {
  var ss    = SpreadsheetApp.getActiveSpreadsheet();
  var datos = ss.getSheetByName("Datos");

  // Crear hoja si no existe (primer arranque)
  if (!datos) {
    datos = ss.insertSheet("Datos");
    datos.appendRow(["Fecha", "Vrms (V)", "Irms (A)", "Potencia (W)", "kWh", "Joule (J)"]);
    datos.getRange(1, 1, 1, 6).setFontWeight("bold");
    datos.setFrozenRows(1);
  }

  var data = JSON.parse(e.postData.contents);
  var fecha = new Date();
  var R_cable = 0.0627;
  var jouleInst = Math.pow(data.irms, 2) * R_cable;

  var lastRow   = datos.getLastRow();
  var nuevaFila = lastRow + 1;
  datos.appendRow([fecha, data.vrms, data.irms, data.power, data.kWh, jouleInst]);

  // Colorear anomalías
  var uptime = data.uptime || 0;
  var enCalibracion = (uptime > 0 && uptime < 480000); // Primeros 8 minutos

  if (enCalibracion) {
    datos.getRange(nuevaFila, 1, 1, 6)
      .setBackground("#fff3e0").setFontColor("#ff9800");
  } else {
    // Voltaje: umbral fijo → rango OK: 109V – 112V
    var V_MIN = 109.0, V_MAX = 112.0;
    if (data.vrms < V_MIN || data.vrms > V_MAX) {
      datos.getRange(nuevaFila, 2)
        .setBackground("#ff4444").setFontColor("#ffffff").setFontWeight("bold");
    }

    // Irms, Potencia, kWh, Joule: Z-score histórico (umbral 2σ)
    if (lastRow >= 10) {
      [
        { col: 3, valor: data.irms  },
        { col: 4, valor: data.power },
        { col: 5, valor: data.kWh   },
        { col: 6, valor: jouleInst  }
      ].forEach(function(cfg) {
        var valores = datos.getRange(2, cfg.col, lastRow - 1, 1)
          .getValues()
          .map(function(r) { return Number(r[0]); })
          .filter(function(v) { return v > 0; });

        if (valores.length < 5) return;
        var media   = valores.reduce(function(a, b) { return a + b; }, 0) / valores.length;
        var varianza = valores.reduce(function(acc, v) { return acc + Math.pow(v - media, 2); }, 0) / valores.length;
        var desv    = Math.sqrt(varianza);

        if (desv > 0 && Math.abs((cfg.valor - media) / desv) > 2) {
          datos.getRange(nuevaFila, cfg.col)
            .setBackground("#ff4444").setFontColor("#ffffff").setFontWeight("bold");
        }
      });
    }
  }

  // Actualizar resumen estadístico
  _actualizarResumen(ss, datos, lastRow);

  return ContentService
    .createTextOutput("OK")
    .setMimeType(ContentService.MimeType.TEXT);
}

// ── Actualizar mín/máx/prom en Graficas ──────────────────────
function _actualizarResumen(ss, datos, lastRow) {
  var graficas = ss.getSheetByName("Graficas");
  if (!graficas || lastRow < 2) return;
  var startRow = 67;
  [
    { col: 2, filaResumen: startRow + 2 },
    { col: 3, filaResumen: startRow + 3 },
    { col: 4, filaResumen: startRow + 4 },
    { col: 5, filaResumen: startRow + 5 },
    { col: 6, filaResumen: startRow + 6 }
  ].forEach(function(cfg) {
    var valores = datos.getRange(2, cfg.col, lastRow - 1, 1)
      .getValues()
      .map(function(r) { return Number(r[0]); })
      .filter(function(v) { return v > 0; });
    if (!valores.length) return;
    var min  = Math.min.apply(null, valores);
    var max  = Math.max.apply(null, valores);
    var prom = valores.reduce(function(a, b) { return a + b; }, 0) / valores.length;
    graficas.getRange(cfg.filaResumen, 2).setValue(min.toFixed(4));
    graficas.getRange(cfg.filaResumen, 3).setValue(max.toFixed(4));
    graficas.getRange(cfg.filaResumen, 4).setValue(prom.toFixed(4));
  });
}

// ════════════════════════════════════════════════════════════
//  doGet — Entrega datos al dashboard o descarga CSV
//
//  Parámetros GET:
//    ?format=csv   → descarga CSV directo desde Google Sheets
//    ?format=json  → JSON { rows: [[...], ...] }
//    ?callback=fn  → JSONP (sin CORS al abrir desde file://)
// ════════════════════════════════════════════════════════════
function doGet(e) {
  var ss    = SpreadsheetApp.getActiveSpreadsheet();
  var datos = ss.getSheetByName("Datos");
  var vals  = datos ? datos.getDataRange().getValues() : [[]];

  var format   = (e && e.parameter && e.parameter.format)   || "json";
  var callback = (e && e.parameter && e.parameter.callback) || null;

  // ── CSV ──────────────────────────────────────────────────
  if (format === "csv") {
    var lines = vals.map(function(row) {
      return row.map(function(cell) {
        // Formatear fechas; escapar comillas
        if (cell instanceof Date) {
          return '"' + Utilities.formatDate(cell, Session.getScriptTimeZone(), "yyyy-MM-dd HH:mm:ss") + '"';
        }
        var s = String(cell).replace(/"/g, '""');
        return (s.indexOf(',') >= 0 || s.indexOf('"') >= 0 || s.indexOf('\n') >= 0) ? '"' + s + '"' : s;
      }).join(',');
    });
    return ContentService
      .createTextOutput(lines.join('\n'))
      .setMimeType(ContentService.MimeType.CSV);
  }

  // ── JSON / JSONP ─────────────────────────────────────────
  var json = JSON.stringify({ rows: vals });
  if (callback) {
    return ContentService
      .createTextOutput(callback + '(' + json + ')')
      .setMimeType(ContentService.MimeType.JAVASCRIPT);
  }
  return ContentService
    .createTextOutput(json)
    .setMimeType(ContentService.MimeType.JSON);
}
