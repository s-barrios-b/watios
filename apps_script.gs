// ════════════════════════════════════════════════════════════
//  WATIOS — Google Apps Script  (optimizado)
// ════════════════════════════════════════════════════════════

function crearHojas() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  var datos = ss.getSheetByName("Datos");
  var esNueva = !datos;
  if (!datos) datos = ss.insertSheet("Datos");

  if (esNueva || datos.getLastRow() === 0) {
    datos.appendRow(["Fecha","Vrms (V)","Irms (A)","Potencia (W)","kWh","P. Joule (W)"]);
  }
  datos.getRange(1,1,1,6)
    .setFontWeight("bold").setBackground("#1a73e8").setFontColor("#ffffff");
  datos.setFrozenRows(1);
  datos.setColumnWidth(1, 180);
  for (var c = 2; c <= 6; c++) datos.setColumnWidth(c, 120);

  var graficas = ss.getSheetByName("Graficas");
  if (graficas) ss.deleteSheet(graficas);
  graficas = ss.insertSheet("Graficas");
  Utilities.sleep(500);
  _crearGraficas(ss, datos, graficas);
  _crearResumen(graficas);

  SpreadsheetApp.getUi().alert(
    "Watios listo\n\n" +
    "Hoja 'Datos' configurada.\n" +
    "Los datos existentes NO fueron borrados.\n\n" +
    "Para borrar todo usa reiniciarTodo()."
  );
}

// ════════════════════════════════════════════════════════════
//  actualizarEncabezados — fuerza el encabezado correcto en
//  la fila 1 sin tocar los datos existentes.
//  Ejecutar UNA SOLA VEZ desde el editor de Apps Script.
// ════════════════════════════════════════════════════════════
function actualizarEncabezados() {
  var ss    = SpreadsheetApp.getActiveSpreadsheet();
  var datos = ss.getSheetByName("Datos");
  if (!datos) { SpreadsheetApp.getUi().alert("No existe la hoja 'Datos'."); return; }

  var encabezados = ["Fecha","Vrms (V)","Irms (A)","Potencia (W)","kWh","P. Joule (W)"];
  datos.getRange(1, 1, 1, encabezados.length).setValues([encabezados]);
  datos.getRange(1, 1, 1, encabezados.length)
    .setFontWeight("bold").setBackground("#1a73e8").setFontColor("#ffffff");
  datos.setFrozenRows(1);

  SpreadsheetApp.getUi().alert("Encabezados actualizados.\nFila 1: " + encabezados.join(" | "));
}


function _crearGraficas(ss, datos, graficas) {
  var MAX = 1000;
  var configs = [
    { col:2, titulo:"Voltaje vs Tiempo (V)",           color:"#1a73e8", fila:1,  col_anchor:1 },
    { col:3, titulo:"Corriente vs Tiempo (A)",         color:"#e53935", fila:1,  col_anchor:8 },
    { col:4, titulo:"Potencia Aparente vs Tiempo (W)", color:"#43a047", fila:23, col_anchor:1 },
    { col:5, titulo:"Energia vs Tiempo (kWh)",         color:"#fb8c00", fila:23, col_anchor:8 },
    { col:6, titulo:"P. Joule Disipada vs Tiempo (W)", color:"#8e24aa", fila:45, col_anchor:1 }
  ];
  configs.forEach(function(cfg) {
    var chart = graficas.newChart()
      .setChartType(Charts.ChartType.LINE)
      .addRange(datos.getRange(1,1,MAX,1))
      .addRange(datos.getRange(1,cfg.col,MAX,1))
      .setOption("title",  cfg.titulo)
      .setOption("legend", {position:"none"})
      .setOption("hAxis",  {title:"Tiempo", slantedText:true, slantedTextAngle:45})
      .setOption("vAxis",  {title:cfg.titulo, minValue:0})
      .setOption("colors", [cfg.color])
      .setOption("pointSize",  4)
      .setOption("lineWidth",  2)
      .setOption("width",      600)
      .setOption("height",     350)
      .setOption("backgroundColor","#ffffff")
      .setPosition(cfg.fila, cfg.col_anchor, 0, 0)
      .build();
    graficas.insertChart(chart);
  });
}

function _crearResumen(graficas) {
  var startRow = 67;
  graficas.getRange(startRow,1,1,4)
    .merge().setValue("Resumen Estadistico")
    .setFontWeight("bold").setFontSize(13)
    .setBackground("#1a73e8").setFontColor("#ffffff")
    .setHorizontalAlignment("center");
  graficas.getRange(startRow+1,1,1,4)
    .setValues([["Variable","Minimo","Maximo","Promedio"]])
    .setFontWeight("bold").setBackground("#e8f0fe").setFontColor("#1a73e8");
  ["Vrms (V)","Irms (A)","Potencia (W)","kWh","P. Joule (W)"].forEach(function(nombre,i) {
    var fila = startRow+2+i;
    graficas.getRange(fila,1).setValue(nombre).setFontWeight("bold");
    graficas.getRange(fila,2,1,3).setValue("--");
    graficas.getRange(fila,1,1,4)
      .setBorder(true,true,true,true,true,true,"#cccccc",SpreadsheetApp.BorderStyle.SOLID);
  });
  [150,110,110,110].forEach(function(w,i){ graficas.setColumnWidth(i+1,w); });
}

function reiniciarTodo() {
  var ui   = SpreadsheetApp.getUi();
  var resp = ui.alert("ADVERTENCIA",
    "Esto borrara TODOS los datos y formatos. Estas seguro?",
    ui.ButtonSet.YES_NO);
  if (resp !== ui.Button.YES) { ui.alert("Cancelado."); return; }
  var ss    = SpreadsheetApp.getActiveSpreadsheet();
  var datos = ss.getSheetByName("Datos");
  if (datos) { datos.clear(); datos.clearConditionalFormatRules(); }
  crearHojas();
}

function _toNumber(value, fallback) {
  if (fallback === undefined) fallback = 0;
  if (value === null || value === undefined || value === "") return fallback;
  var n = Number(String(value).replace(",", "."));
  return isFinite(n) ? n : fallback;
}

function _firstValue(data, names) {
  for (var i = 0; i < names.length; i++) {
    var name = names[i];
    if (Object.prototype.hasOwnProperty.call(data, name) &&
        data[name] !== null && data[name] !== undefined && data[name] !== "") {
      return data[name];
    }
  }
  return null;
}

function _numberOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  var n = Number(String(value).replace(",", "."));
  return isFinite(n) ? n : null;
}

function _sheetNumberOk(value) {
  return _numberOrNull(value) !== null;
}

function _normalizarLectura(data) {
  var R_cable   = 0.066;
  var vrms = _numberOrNull(_firstValue(data, ["vrms", "Vrms", "Vrms (V)"]));
  var irms = _numberOrNull(_firstValue(data, ["irms", "Irms", "Irms (A)"]));
  var power = _numberOrNull(_firstValue(data, ["power", "apparentPower", "Potencia (W)"]));
  var kWh = _numberOrNull(_firstValue(data, ["kWh", "kwh"]));

  if (vrms === null || irms === null || power === null || kWh === null || vrms <= 0) {
    return null;
  }

  var jouleRaw = _firstValue(data, ["joule", "P. Joule (W)"]);
  var joule = _numberOrNull(jouleRaw);
  if (joule === null) {
    joule = Math.pow(irms, 2) * R_cable;
  }
  if (!isFinite(joule)) return null;

  var fechaRaw = _firstValue(data, ["fecha", "timestamp"]);
  var fecha = fechaRaw ? new Date(fechaRaw) : new Date();
  if (isNaN(fecha.getTime())) fecha = new Date();

  return {
    fecha: fecha,
    vrms: vrms,
    irms: irms,
    power: power,
    kWh: kWh,
    joule: joule,
    uptime: _toNumber(data.uptime)
  };
}

function limpiarFilasInvalidas() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var datos = ss.getSheetByName("Datos");
  if (!datos) {
    SpreadsheetApp.getUi().alert("No existe la hoja 'Datos'.");
    return;
  }

  var valores = datos.getDataRange().getValues();
  var borradas = 0;
  for (var fila = valores.length; fila >= 2; fila--) {
    var row = valores[fila - 1];
    var invalida =
      !row[0] ||
      !_sheetNumberOk(row[1]) ||
      !_sheetNumberOk(row[2]) ||
      !_sheetNumberOk(row[3]) ||
      !_sheetNumberOk(row[4]) ||
      !_sheetNumberOk(row[5]);

    if (invalida) {
      datos.deleteRow(fila);
      borradas++;
    }
  }

  SpreadsheetApp.getUi().alert("Filas invalidas eliminadas: " + borradas);
}

// ════════════════════════════════════════════════════════════
//  doPost — responde inmediatamente, difiere el trabajo pesado
// ════════════════════════════════════════════════════════════
function doPost(e) {
  var raw = (e && e.postData && e.postData.contents) ? e.postData.contents : "{}";
  var data;
  try {
    data = JSON.parse(raw);
  } catch (err) {
    return ContentService
      .createTextOutput("JSON_INVALIDO")
      .setMimeType(ContentService.MimeType.TEXT);
  }

  var ss    = SpreadsheetApp.getActiveSpreadsheet();
  var datos = ss.getSheetByName("Datos");
  if (!datos) {
    datos = ss.insertSheet("Datos");
    datos.appendRow(["Fecha","Vrms (V)","Irms (A)","Potencia (W)","kWh","P. Joule (W)"]);
    datos.getRange(1,1,1,6).setFontWeight("bold");
    datos.setFrozenRows(1);
  }

  if (Array.isArray(data.rows) || Array.isArray(data)) {
    var lecturas = Array.isArray(data.rows) ? data.rows : data;
    var filas = [];
    var ultima = null;
    lecturas.forEach(function(item) {
      var lectura = _normalizarLectura(item || {});
      if (!lectura) return;
      ultima = lectura;
      filas.push([lectura.fecha, lectura.vrms, lectura.irms, lectura.power, lectura.kWh, lectura.joule]);
    });

    if (!filas.length) {
      return ContentService
        .createTextOutput("SIN_DATOS")
        .setMimeType(ContentService.MimeType.TEXT);
    }

    var filaInicial = datos.getLastRow() + 1;
    datos.getRange(filaInicial, 1, filas.length, 6).setValues(filas);
    var filaFinal = filaInicial + filas.length - 1;

    var propsLote = PropertiesService.getScriptProperties();
    propsLote.setProperties({
      "pendiente_vrms"   : String(ultima.vrms),
      "pendiente_irms"   : String(ultima.irms),
      "pendiente_power"  : String(ultima.power),
      "pendiente_kWh"    : String(ultima.kWh),
      "pendiente_joule"  : String(ultima.joule),
      "pendiente_uptime" : String(ultima.uptime || 0),
      "pendiente_fila"   : String(filaFinal)
    });

    ScriptApp.getProjectTriggers()
      .filter(function(t){ return t.getHandlerFunction() === "tareasDiferidas"; })
      .forEach(function(t){ ScriptApp.deleteTrigger(t); });

    ScriptApp.newTrigger("tareasDiferidas")
      .timeBased()
      .after(60000)
      .create();

    return ContentService
      .createTextOutput("OK")
      .setMimeType(ContentService.MimeType.TEXT);
  }

  var lectura = _normalizarLectura(data);
  if (!lectura) {
    return ContentService
      .createTextOutput("SIN_DATOS")
      .setMimeType(ContentService.MimeType.TEXT);
  }
  var jouleInst = lectura.joule;

  // Una sola escritura atomica — sin lecturas previas
  datos.appendRow([lectura.fecha, lectura.vrms, lectura.irms, lectura.power, lectura.kWh, jouleInst]);

  // Guardar payload en PropertiesService para la tarea diferida
  var props = PropertiesService.getScriptProperties();
  props.setProperties({
    "pendiente_vrms"   : String(lectura.vrms),
    "pendiente_irms"   : String(lectura.irms),
    "pendiente_power"  : String(lectura.power),
    "pendiente_kWh"    : String(lectura.kWh),
    "pendiente_joule"  : String(jouleInst),
    "pendiente_uptime" : String(lectura.uptime || 0),
    "pendiente_fila"   : String(datos.getLastRow())
  });

  // Cancelar triggers anteriores para no acumularlos
  ScriptApp.getProjectTriggers()
    .filter(function(t){ return t.getHandlerFunction() === "tareasDiferidas"; })
    .forEach(function(t){ ScriptApp.deleteTrigger(t); });

  ScriptApp.newTrigger("tareasDiferidas")
    .timeBased()
    .after(60000)
    .create();

  return ContentService
    .createTextOutput("OK")
    .setMimeType(ContentService.MimeType.TEXT);
}

// ════════════════════════════════════════════════════════════
//  tareasDiferidas — coloreo + resumen, corre fuera del ciclo
//  del ESP32 y no bloquea la respuesta HTTP
// ════════════════════════════════════════════════════════════
function tareasDiferidas() {
  var props = PropertiesService.getScriptProperties();
  var all   = props.getProperties();

  var vrms      = parseFloat(all["pendiente_vrms"]   || 0);
  var irms      = parseFloat(all["pendiente_irms"]   || 0);
  var power     = parseFloat(all["pendiente_power"]  || 0);
  var kWh       = parseFloat(all["pendiente_kWh"]    || 0);
  var jouleInst = parseFloat(all["pendiente_joule"]  || 0);
  var uptime    = parseFloat(all["pendiente_uptime"] || 0);
  var nuevaFila = parseInt  (all["pendiente_fila"]   || 0);

  if (!nuevaFila) return;

  var ss    = SpreadsheetApp.getActiveSpreadsheet();
  var datos = ss.getSheetByName("Datos");
  var lastRow = datos.getLastRow();
  if (lastRow < 2) return;

  var enCalibracion = (uptime > 0 && uptime < 480000);

  if (enCalibracion) {
    datos.getRange(nuevaFila,1,1,6)
      .setBackground("#fff3e0").setFontColor("#ff9800");
  } else {
    var V_MIN = 99.0, V_MAX = 121.0;
    if (vrms < V_MIN || vrms > V_MAX) {
      datos.getRange(nuevaFila,2)
        .setBackground("#ff4444").setFontColor("#ffffff").setFontWeight("bold");
    }

    if (lastRow >= 10) {
      // Una sola lectura batch para todas las columnas
      var todosValores = datos.getRange(2, 2, lastRow-1, 5).getValues();
      var actuales = [vrms, irms, power, kWh, jouleInst];

      actuales.forEach(function(valor, idx) {
        var col = idx + 2;
        var valores = todosValores
          .map(function(r){ return Number(r[idx]); })
          .filter(function(v){ return v > 0; });

        if (valores.length < 5) return;
        var media    = valores.reduce(function(a,b){ return a+b; },0) / valores.length;
        var varianza = valores.reduce(function(acc,v){ return acc + Math.pow(v-media,2); },0) / valores.length;
        var desv     = Math.sqrt(varianza);

        if (desv > 0 && Math.abs((valor - media) / desv) > 2) {
          datos.getRange(nuevaFila, col)
            .setBackground("#ff4444").setFontColor("#ffffff").setFontWeight("bold");
        }
      });
    }
  }

  _actualizarResumen(ss, datos, lastRow);
  props.deleteAllProperties();
}

// ════════════════════════════════════════════════════════════
//  _actualizarResumen — lectura batch, escritura batch
// ════════════════════════════════════════════════════════════
function _actualizarResumen(ss, datos, lastRow) {
  var graficas = ss.getSheetByName("Graficas");
  if (!graficas || lastRow < 2) return;

  // Una sola lectura para todas las columnas
  var todosValores = datos.getRange(2, 2, lastRow-1, 5).getValues();
  var startRow     = 67;

  todosValores[0].forEach(function(_, idx) {
    var valores = todosValores
      .map(function(r){ return Number(r[idx]); })
      .filter(function(v){ return v > 0; });
    if (!valores.length) return;

    var min  = Math.min.apply(null, valores);
    var max  = Math.max.apply(null, valores);
    var prom = valores.reduce(function(a,b){ return a+b; },0) / valores.length;

    // Tres celdas escritas en una sola llamada
    graficas.getRange(startRow+2+idx, 2, 1, 3)
      .setValues([[min.toFixed(4), max.toFixed(4), prom.toFixed(4)]]);
  });
}

// ════════════════════════════════════════════════════════════
//  doGet — sin cambios
// ════════════════════════════════════════════════════════════
function doGet(e) {
  var ss    = SpreadsheetApp.getActiveSpreadsheet();
  var datos = ss.getSheetByName("Datos");
  var vals  = datos ? datos.getDataRange().getValues() : [[]];

  var format   = (e && e.parameter && e.parameter.format)   || "json";
  var callback = (e && e.parameter && e.parameter.callback) || null;

  if (format === "csv") {
    var lines = vals.map(function(row) {
      return row.map(function(cell) {
        if (cell instanceof Date) {
          return '"' + Utilities.formatDate(cell, Session.getScriptTimeZone(), "yyyy-MM-dd HH:mm:ss") + '"';
        }
        var s = String(cell).replace(/"/g,'""');
        return (s.indexOf(',')>=0||s.indexOf('"')>=0||s.indexOf('\n')>=0) ? '"'+s+'"' : s;
      }).join(',');
    });
    return ContentService
      .createTextOutput(lines.join('\n'))
      .setMimeType(ContentService.MimeType.CSV);
  }

  var json = JSON.stringify({ rows: vals });
  if (callback) {
    return ContentService
      .createTextOutput(callback+'('+json+')')
      .setMimeType(ContentService.MimeType.JAVASCRIPT);
  }
  return ContentService
    .createTextOutput(json)
    .setMimeType(ContentService.MimeType.JSON);
}
