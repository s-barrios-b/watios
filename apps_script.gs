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

// ════════════════════════════════════════════════════════════
//  doPost — responde inmediatamente, difiere el trabajo pesado
// ════════════════════════════════════════════════════════════
function doPost(e) {
  var data = JSON.parse(e.postData.contents);

  var ss    = SpreadsheetApp.getActiveSpreadsheet();
  var datos = ss.getSheetByName("Datos");
  if (!datos) {
    datos = ss.insertSheet("Datos");
    datos.appendRow(["Fecha","Vrms (V)","Irms (A)","Potencia (W)","kWh","P. Joule (W)"]);
    datos.getRange(1,1,1,6).setFontWeight("bold");
    datos.setFrozenRows(1);
  }

  var R_cable   = 0.066;
  var jouleInst = Math.pow(data.irms, 2) * R_cable;

  // Una sola escritura atomica — sin lecturas previas
  datos.appendRow([new Date(), data.vrms, data.irms, data.power, data.kWh, jouleInst]);

  // Guardar payload en PropertiesService para la tarea diferida
  var props = PropertiesService.getScriptProperties();
  props.setProperties({
    "pendiente_vrms"   : String(data.vrms),
    "pendiente_irms"   : String(data.irms),
    "pendiente_power"  : String(data.power),
    "pendiente_kWh"    : String(data.kWh),
    "pendiente_joule"  : String(jouleInst),
    "pendiente_uptime" : String(data.uptime || 0),
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