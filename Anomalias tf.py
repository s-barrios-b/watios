import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import requests
import json
import os
import argparse
import time
from datetime import datetime
from sklearn.preprocessing import StandardScaler
import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers

# ── Configuracion ─────────────────────────────────────────
LOCAL_SERVER   = "http://127.0.0.1:5000"
SCRIPT_URL     = f"{LOCAL_SERVER}/data"
MODELO_DIR     = "modelo_autoencoder"
WINDOW_SIZE    = 10      # ventana de tiempo para el LSTM
EPOCHS         = 50
BATCH_SIZE     = 16
LATENT_DIM     = 8       # dimension del espacio latente
ZSCORE_UMBRAL  = 2.0     # umbral Z-score para anomalias (igual que Apps Script)
VRMS_IDEAL     = 110.0   # tension ideal de referencia en voltios
VRMS_TOL_PCT   = 0.05    # tolerancia ± 5% sobre VRMS_IDEAL
FEATURES       = ['vrms', 'irms', 'power', 'kwh', 'joule']
FEATURES_LSTM  = ['irms', 'power', 'kwh', 'joule']  # vrms usa umbral fijo, no LSTM
R_CABLE        = 0.0627  # resistencia del cable en ohmios (igual que Apps Script)

# ── 1. Cargar datos desde Google Sheets ──────────────────
def cargar_datos(url):
    print("Descargando datos desde Apps Script...")
    try:
        res  = requests.get(url, allow_redirects=True, timeout=15)
        data = res.json()
        rows = data.get('rows', [])
        if len(rows) < 2:
            raise ValueError("No hay suficientes datos en la hoja.")

        # Saltar encabezado
        df = pd.DataFrame(rows[1:], columns=['fecha','vrms','irms','power','kwh','joule'])
        df['fecha'] = pd.to_datetime(df['fecha'], errors='coerce')
        for col in FEATURES:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', '.'), errors='coerce')
        df = df.dropna().reset_index(drop=True)
        df = df[df['vrms'] > 0].reset_index(drop=True)

        print(f"  {len(df)} filas cargadas ({df['fecha'].min()} → {df['fecha'].max()})")
        return df

    except Exception as e:
        print(f"Error al cargar datos: {e}")
        raise

# ── 2. Crear ventanas deslizantes para LSTM ───────────────
def crear_ventanas(data, window_size):
    X = []
    for i in range(len(data) - window_size):
        X.append(data[i:i + window_size])
    return np.array(X)

# ── 3. Construccion del Autoencoder LSTM ──────────────────
def construir_autoencoder(window_size, n_features, latent_dim):
    """
    Arquitectura: Encoder LSTM → espacio latente → Decoder LSTM
    El modelo aprende a reconstruir secuencias normales.
    Secuencias con alto error de reconstruccion = anomalias.
    """
    # Encoder
    inputs  = keras.Input(shape=(window_size, n_features))
    encoded = layers.LSTM(32, activation='tanh', return_sequences=True)(inputs)
    encoded = layers.LSTM(latent_dim, activation='tanh', return_sequences=False)(encoded)

    # Decoder
    decoded = layers.RepeatVector(window_size)(encoded)
    decoded = layers.LSTM(latent_dim, activation='tanh', return_sequences=True)(decoded)
    decoded = layers.LSTM(32, activation='tanh', return_sequences=True)(decoded)
    outputs = layers.TimeDistributed(layers.Dense(n_features))(decoded)

    model = keras.Model(inputs, outputs, name='autoencoder_lstm')
    model.compile(optimizer='adam', loss='mse')
    return model

# ── 4. Calcular errores de reconstruccion ─────────────────
def calcular_errores(model, X):
    X_pred = model.predict(X, verbose=0)
    # Error cuadratico medio por muestra
    errores = np.mean(np.power(X - X_pred, 2), axis=(1, 2))
    return errores

# ── 5. Detectar anomalias ─────────────────────────────────
def detectar_anomalias(errores, zscore_umbral=ZSCORE_UMBRAL):
    """
    Z-score del error MSE — alineado con la logica del Apps Script.
    Una muestra es anomalia si su Z-score supera el umbral (defecto: 2.0).
    """
    media     = np.mean(errores)
    desv      = np.std(errores)
    zscores   = np.abs((errores - media) / desv) if desv > 0 else np.zeros_like(errores)
    anomalias = zscores > zscore_umbral
    umbral    = media + zscore_umbral * desv   # umbral equivalente en escala MSE
    return anomalias, umbral, zscores

# ── 6. Generar reporte HTML ───────────────────────────────
def generar_reporte(df, errores, anomalias, umbral, historia):
    print("Generando reporte HTML...")

    # Indices de anomalias en el dataframe original
    offset = WINDOW_SIZE
    idx_anomalias = np.where(anomalias)[0] + offset

    # Figuras
    fig, axes = plt.subplots(len(FEATURES) + 1, 1, figsize=(14, 4 * (len(FEATURES) + 1)))
    fig.patch.set_facecolor('#0a0e17')

    colores = {'vrms':'#00e5ff','irms':'#ff4081','power':'#69ff47','kwh':'#ffb020','joule':'#b388ff'}

    fechas_slice = df['fecha'].iloc[offset:].reset_index(drop=True)

    # Grafica de error de reconstruccion
    ax = axes[0]
    ax.set_facecolor('#111827')
    ax.plot(fechas_slice, errores, color='#ffffff', linewidth=0.8, alpha=0.7, label='Error reconstruccion')
    ax.axhline(umbral, color='#ff3b3b', linestyle='--', linewidth=1, label=f'Umbral p{Z_PERCENTILE}={umbral:.6f}')
    anom_fechas  = fechas_slice[anomalias]
    anom_errores = errores[anomalias]
    ax.scatter(anom_fechas, anom_errores, color='#ff3b3b', s=20, zorder=5, label='Anomalias')
    ax.set_title('Error de Reconstruccion del Autoencoder', color='#e2e8f0', fontsize=10)
    ax.tick_params(colors='#4a5568', labelsize=7)
    ax.spines[:].set_color('#1e293b')
    ax.set_facecolor('#111827')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    ax.legend(fontsize=7, facecolor='#111827', labelcolor='#e2e8f0')

    # Grafica por variable
    for i, feat in enumerate(FEATURES):
        ax = axes[i + 1]
        ax.set_facecolor('#111827')
        color = colores.get(feat, '#ffffff')
        vals  = df[feat].iloc[offset:].reset_index(drop=True)
        ax.plot(fechas_slice, vals, color=color, linewidth=0.9, alpha=0.8)

        # Marcar anomalias
        if anom_fechas.any():
            ax.scatter(anom_fechas, vals[anomalias], color='#ff3b3b', s=18, zorder=5)

        ax.set_title(feat.upper(), color='#e2e8f0', fontsize=9)
        ax.tick_params(colors='#4a5568', labelsize=7)
        ax.spines[:].set_color('#1e293b')
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))

    plt.tight_layout(pad=2.0)
    plt.savefig('graficas_anomalias.png', dpi=120, bbox_inches='tight',
                facecolor='#0a0e17')
    plt.close()

    # Tabla de anomalias
    filas_anomalas = df.iloc[idx_anomalias].copy()
    filas_anomalas['error'] = errores[anomalias]
    filas_anomalas = filas_anomalas.sort_values('error', ascending=False)

    tabla_html = filas_anomalas.to_html(
        index=False, float_format='%.5f',
        classes='tabla', border=0
    )

    # Loss history
    loss_fig, loss_ax = plt.subplots(figsize=(8, 3))
    loss_fig.patch.set_facecolor('#0a0e17')
    loss_ax.set_facecolor('#111827')
    loss_ax.plot(historia.history['loss'], color='#00e5ff', linewidth=1.5, label='Train loss')
    if 'val_loss' in historia.history:
        loss_ax.plot(historia.history['val_loss'], color='#ff4081', linewidth=1.5, label='Val loss')
    loss_ax.set_title('Curva de entrenamiento', color='#e2e8f0', fontsize=9)
    loss_ax.tick_params(colors='#4a5568', labelsize=7)
    loss_ax.spines[:].set_color('#1e293b')
    loss_ax.legend(fontsize=7, facecolor='#111827', labelcolor='#e2e8f0')
    plt.tight_layout()
    plt.savefig('loss_curve.png', dpi=120, bbox_inches='tight', facecolor='#0a0e17')
    plt.close()

    # HTML final
    n_total = len(df)
    n_anom  = int(np.sum(anomalias))
    pct     = n_anom / len(anomalias) * 100 if len(anomalias) else 0

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Watios — Reporte de Anomalías</title>
<style>
  body {{ background:#0a0e17; color:#e2e8f0; font-family:'Share Tech Mono',monospace; padding:32px; }}
  h1 {{ color:#00e5ff; font-size:1.4rem; margin-bottom:4px; }}
  h2 {{ color:#4a5568; font-size:0.85rem; text-transform:uppercase; letter-spacing:1px; margin:28px 0 12px; }}
  .meta {{ color:#4a5568; font-size:0.72rem; margin-bottom:28px; }}
  .kpi-row {{ display:flex; gap:16px; margin-bottom:28px; flex-wrap:wrap; }}
  .kpi {{ background:#111827; border:1px solid #1e293b; border-radius:10px; padding:16px 20px; flex:1; min-width:140px; }}
  .kpi-val {{ font-size:2rem; color:#00e5ff; line-height:1; }}
  .kpi-lbl {{ font-size:0.65rem; color:#4a5568; text-transform:uppercase; letter-spacing:1px; margin-top:6px; }}
  .kpi-anom .kpi-val {{ color:#ff3b3b; }}
  img {{ width:100%; border-radius:10px; border:1px solid #1e293b; margin-bottom:20px; }}
  .tabla {{ width:100%; border-collapse:collapse; font-size:0.72rem; }}
  .tabla th {{ background:#0d1420; color:#4a5568; padding:9px 12px; text-align:left; border-bottom:1px solid #1e293b; text-transform:uppercase; letter-spacing:1px; font-size:0.62rem; }}
  .tabla td {{ padding:8px 12px; border-bottom:1px solid #1e293b; color:#e2e8f0; }}
  .tabla tr:hover {{ background:rgba(255,255,255,.02); }}
  .footer {{ color:#4a5568; font-size:0.65rem; margin-top:32px; border-top:1px solid #1e293b; padding-top:14px; }}
</style>
</head>
<body>
<h1>Watios — Reporte de Anomalías</h1>
<p class="meta">Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · Modelo: TensorFlow {tf.__version__} · Ventana: {WINDOW_SIZE} muestras</p>

<div class="kpi-row">
  <div class="kpi"><div class="kpi-val">{n_total}</div><div class="kpi-lbl">Total lecturas</div></div>
  <div class="kpi kpi-anom"><div class="kpi-val">{n_anom}</div><div class="kpi-lbl">Anomalias detectadas</div></div>
  <div class="kpi"><div class="kpi-val">{pct:.1f}%</div><div class="kpi-lbl">Tasa de anomalias</div></div>
  <div class="kpi"><div class="kpi-val">{umbral:.6f}</div><div class="kpi-lbl">Umbral MSE (Z&gt;{ZSCORE_UMBRAL})</div></div>
  <div class="kpi"><div class="kpi-val">{VRMS_IDEAL}V ±{int(VRMS_TOL_PCT*100)}%</div><div class="kpi-lbl">Umbral Voltaje fijo</div></div>
  <div class="kpi"><div class="kpi-val">{EPOCHS}</div><div class="kpi-lbl">Epocas entrenamiento</div></div>
</div>

<h2>Curva de entrenamiento</h2>
<img src="loss_curve.png" alt="Loss curve">

<h2>Series de tiempo con anomalias marcadas</h2>
<img src="graficas_anomalias.png" alt="Anomalias">

<h2>Filas anomalas (ordenadas por error descendente)</h2>
{tabla_html}

<div class="footer">
  Features usadas: {', '.join(FEATURES)} · 
  Encoder: Z-score (LSTM) + Umbral fijo {VRMS_IDEAL}V ±{int(VRMS_TOL_PCT*100)}% (Vrms) · Z-score umbral LSTM: {ZSCORE_UMBRAL} · 
  Arquitectura: LSTM(32) → LSTM({LATENT_DIM}) → RepeatVector → LSTM({LATENT_DIM}) → LSTM(32) → Dense
</div>
</body>
</html>"""

    with open('reporte_anomalias.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print("  Reporte html actualizado localmente.")

def enviar_resultados_al_servidor(df, errores, anomalias, umbral_lstm):
    try:
        offset = WINDOW_SIZE
        idx_anomalias = np.where(anomalias)[0] + offset
        
        # Opcional: Enviar las filas anómalas
        n_anom = int(np.sum(anomalias))
        
        payload = {
            "n_lecturas": len(df),
            "n_anomalias": n_anom,
            "tasa_pct": (n_anom / len(df) * 100) if len(df) else 0,
            "umbral_mse": float(umbral_lstm),
            "modelo": "Autoencoder LSTM"
        }
        res = requests.post(f"{LOCAL_SERVER}/ml/result", json=payload, timeout=5)
        if res.status_code == 200:
            print("  Resultados sincronizados con Node.js exitosamente.")
    except Exception as e:
        print(f"  Error sincronizando al servidor Node: {e}")

# ── Main ──────────────────────────────────────────────────
def ejecutar_analisis():
    print("=" * 55)
    print(f"  Análisis ML a las {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 55)

    # 1. Cargar datos
    df = cargar_datos(SCRIPT_URL)

    if len(df) < WINDOW_SIZE + 10:
        print(f"Necesitas al menos {WINDOW_SIZE + 10} filas para entrenar. Tienes {len(df)}.")
        return

    # 2. Normalizar con Z-score (vrms excluido: se evalua con umbral fijo 110V)
    scaler    = StandardScaler()
    data_norm = scaler.fit_transform(df[FEATURES_LSTM].values)

    # 3. Ventanas (solo features LSTM, sin vrms)
    X = crear_ventanas(data_norm, WINDOW_SIZE)
    print(f"  Forma de entrada: {X.shape}  (muestras, ventana, features)")

    # 4. Modelo
    if os.path.exists(MODELO_DIR):
        print(f"Cargando modelo existente desde {MODELO_DIR}...")
        model = keras.models.load_model(MODELO_DIR)
        historia = None
        print("  (Para re-entrenar, elimina la carpeta modelo_autoencoder/)")
    else:
        print("Construyendo y entrenando Autoencoder LSTM...")
        model = construir_autoencoder(WINDOW_SIZE, len(FEATURES_LSTM), LATENT_DIM)
        model.summary()

        historia = model.fit(
            X, X,
            epochs=EPOCHS,
            batch_size=BATCH_SIZE,
            validation_split=0.1,
            shuffle=False,
            callbacks=[
                keras.callbacks.EarlyStopping(patience=8, restore_best_weights=True),
                keras.callbacks.ReduceLROnPlateau(factor=0.5, patience=4)
            ],
            verbose=1
        )
        model.save(MODELO_DIR)
        print(f"  Modelo guardado en {MODELO_DIR}/")

    # 5. Errores y anomalias
    print("Calculando errores de reconstruccion...")
    errores = calcular_errores(model, X)

    # — Anomalias LSTM: Z-score del error de reconstruccion (irms, power, kwh, joule)
    anom_lstm, umbral_lstm, zscores = detectar_anomalias(errores)

    # — Anomalias de voltaje: umbral fijo 110 V ± 5% (vrms)
    vrms_slice = df['vrms'].values[WINDOW_SIZE:]
    anom_vrms  = np.abs(vrms_slice - VRMS_IDEAL) > (VRMS_IDEAL * VRMS_TOL_PCT)

    # Combinar ambas fuentes
    anomalias = anom_lstm | anom_vrms

    n_anom      = int(np.sum(anomalias))
    n_anom_lstm = int(np.sum(anom_lstm))
    n_anom_vrms = int(np.sum(anom_vrms))
    print(f"  Umbral Z-score LSTM : {ZSCORE_UMBRAL}  (MSE equiv: {umbral_lstm:.6f})")
    print(f"  Anomalias LSTM (Z-score):                  {n_anom_lstm}")
    print(f"  Anomalias Vrms ({VRMS_IDEAL}V ±{int(VRMS_TOL_PCT*100)}%): {n_anom_vrms}")
    print(f"  Anomalias totales: {n_anom} de {len(errores)} ({n_anom/len(errores)*100:.1f}%)")

    # Indices en df original
    idx_anomalias = np.where(anomalias)[0] + WINDOW_SIZE
    if n_anom:
        print("\n  Primeras 5 anomalias:")
        for idx in idx_anomalias[:5]:
            row = df.iloc[idx]
            print(f"    [{row['fecha']}] Vrms={row['vrms']:.2f}V Irms={row['irms']:.4f}A Power={row['power']:.4f}W")

    # 6. Reporte y sincronización
    # Nota: si historia=None (modelo cargado), la curva de loss no se genera
    generar_reporte(df, errores, anomalias, umbral_lstm, historia)
    enviar_resultados_al_servidor(df, errores, anomalias, umbral_lstm)

    print("\nAnálisis finalizado.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--daemon', action='store_true', help="Corre continuamente")
    args = parser.parse_args()

    if args.daemon:
        print("Iniciando Módulo de ML en modo continuo (esperando datos...)")
        ultimo_conteo = 0
        while True:
            try:
                # Comprobar si vale la pena ejecutar
                res = requests.get(SCRIPT_URL)
                data = res.json()
                conteo_actual = len(data.get('rows', []))
                
                # Si han llegado datos nuevos (y hay suficientes), corremos iteración
                if conteo_actual > ultimo_conteo and conteo_actual > WINDOW_SIZE + 10:
                    ejecutar_analisis()
                    ultimo_conteo = conteo_actual
            except Exception as e:
                pass # Ignorar hasta el próximo ciclo
            
            time.sleep(15) # Revisa cada 15 segundos
    else:
        ejecutar_analisis()

if __name__ == '__main__':
    main()