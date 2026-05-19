#!/usr/bin/env python3
"""
Watios - Analisis de anomalias con Autoencoder LSTM
---------------------------------------------------
Este modulo corre de forma independiente a Servidor.py:

* Consulta GET /data para leer el historial de la sesion.
* Entrena una sola vez un autoencoder LSTM usando
  analisis_anomalias/datos_entrenamiento.csv.
* Persiste modelo, scaler y umbrales en modelo_autoencoder/.
* En arranques posteriores carga los artefactos desde disco.
* Reporta a POST /ml/result el resultado por lectura evaluada.

Regla operacional:
Las cinco variables alimentan el autoencoder, pero solo vrms e irms
pueden activar alertas. power, kwh y joule se reportan como variables
informativas porque son derivadas de vrms/irms.
"""

import argparse
import html
import json
import os
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

import joblib
import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import tensorflow as tf
from dotenv import load_dotenv
from sklearn.preprocessing import StandardScaler
from tensorflow import keras
from tensorflow.keras import layers

load_dotenv(override=True)
tf.get_logger().setLevel("ERROR")


# ---------------------------------------------------------------------------
# Configuracion general
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOCAL_SERVER = os.getenv("LOCAL_SERVER", "http://127.0.0.1:5000").rstrip("/")
DATA_URL = f"{LOCAL_SERVER}/data"
ML_RESULT_URL = f"{LOCAL_SERVER}/ml/result"

ARTIFACTS_DIR = os.path.join(BASE_DIR, "analisis_anomalias")
TRAINING_DATA_PATH = os.path.join(ARTIFACTS_DIR, "datos_entrenamiento.csv")
REPORT_PATH = os.path.join(ARTIFACTS_DIR, "reporte_anomalias.html")
GRAPH_PATH = os.path.join(ARTIFACTS_DIR, "graficas_anomalias.png")
LOSS_PATH = os.path.join(ARTIFACTS_DIR, "loss_curve.png")
LATEST_CONCLUSIONS_PATH = os.path.join(ARTIFACTS_DIR, "conclusiones_ultimas.json")

MODEL_DIR = os.path.join(BASE_DIR, "modelo_autoencoder")
MODEL_PATH = os.path.join(MODEL_DIR, "model.keras")
SCALER_PATH = os.path.join(MODEL_DIR, "scaler.pkl")
THRESHOLD_PATH = os.path.join(MODEL_DIR, "umbral.json")

FEATURES = ["vrms", "irms", "power", "kwh", "joule"]
ALERT_FEATURES = ["vrms", "irms"]
INFORMATIVE_FEATURES = ["power", "kwh", "joule"]
UNITS = {"vrms": "V", "irms": "A", "power": "W", "kwh": "kWh", "joule": "W"}

WINDOW_SIZE = int(os.getenv("ML_WINDOW_SIZE", "5"))
EPOCHS = int(os.getenv("ML_EPOCHS", "50"))
BATCH_SIZE = int(os.getenv("ML_BATCH_SIZE", "16"))
LATENT_DIM = int(os.getenv("ML_LATENT_DIM", "8"))
ZSCORE_THRESHOLD = float(os.getenv("ML_ZSCORE_UMBRAL", "2.0"))
DAEMON_INTERVAL = int(os.getenv("ML_DAEMON_INTERVAL", "15"))

VRMS_MIN = 99.0
VRMS_MAX = 121.0
ALERT_START_READING = int(os.getenv("ALERT_START_READING", "6"))

JOULE_REL_TOLERANCE = float(os.getenv("JOULE_REL_TOLERANCE", "0.20"))

_r_cable = os.getenv("R_CABLE")
if _r_cable is None:
    raise RuntimeError("R_CABLE no definido en .env. Agrega R_CABLE=0.066 y reinicia.")
R_CABLE = float(_r_cable)

os.makedirs(ARTIFACTS_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Utilidades de parseo y persistencia
# ---------------------------------------------------------------------------
def timestamp_archivo() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def preparar_csv_entrenamiento() -> None:
    """Crea el CSV de referencia si no existe, sin poblarlo con datos vivos."""
    if not os.path.exists(TRAINING_DATA_PATH):
        pd.DataFrame(columns=["fecha"] + FEATURES).to_csv(
            TRAINING_DATA_PATH, index=False, encoding="utf-8"
        )


def parsear_fechas(values) -> pd.Series:
    return pd.to_datetime(values, errors="coerce", dayfirst=True)


def formatear_numero(value, decimals: int = 6) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    if not np.isfinite(number):
        return "--"
    return f"{number:.{decimals}f}"


def formatear_numero_csv(value) -> str:
    if pd.isna(value):
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


def normalizar_nombre_columna(name: str) -> str:
    text = str(name).strip().lower()
    text = text.replace(" ", "").replace("_", "").replace("-", "")
    text = text.replace("(v)", "").replace("(a)", "").replace("(w)", "")
    text = text.replace("p.joule", "joule").replace("potencia", "power")
    if text in {"fecha", "date", "timestamp", "time"}:
        return "fecha"
    if text in {"vrms", "voltaje", "voltage"}:
        return "vrms"
    if text in {"irms", "corriente", "current"}:
        return "irms"
    if text in {"power", "apparentpower"}:
        return "power"
    if text in {"kwh", "kw/h", "energia"}:
        return "kwh"
    if text in {"joule", "pjoule"}:
        return "joule"
    return text


def normalizar_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte cualquier entrada compatible al esquema Watios canonico."""
    if df.empty:
        return pd.DataFrame(columns=["fecha"] + FEATURES + ["lectura_sesion"])

    normalized = df.copy()
    normalized.columns = [normalizar_nombre_columna(c) for c in normalized.columns]

    faltantes = [col for col in ["fecha"] + FEATURES if col not in normalized.columns]
    if faltantes:
        raise ValueError(f"Faltan columnas requeridas: {', '.join(faltantes)}")

    normalized = normalized[["fecha"] + FEATURES].copy()
    normalized["fecha"] = parsear_fechas(normalized["fecha"])
    for col in FEATURES:
        normalized[col] = pd.to_numeric(
            normalized[col].astype(str).str.replace(",", ".", regex=False),
            errors="coerce",
        )

    normalized = normalized.dropna(subset=["fecha"] + FEATURES).reset_index(drop=True)
    normalized = normalized[normalized["vrms"] > 0].reset_index(drop=True)
    normalized["lectura_sesion"] = np.arange(1, len(normalized) + 1)
    return normalized


def cargar_datos_servidor() -> pd.DataFrame:
    """Lee el historial de la sesion desde FastAPI GET /data."""
    res = requests.get(DATA_URL, allow_redirects=True, timeout=15)
    res.raise_for_status()
    rows = res.json().get("rows", [])
    if len(rows) < 2:
        return pd.DataFrame(columns=["fecha"] + FEATURES + ["lectura_sesion"])

    header = [normalizar_nombre_columna(col) for col in rows[0]]
    df = pd.DataFrame(rows[1:], columns=header)
    return normalizar_dataframe(df)


def cargar_datos_entrenamiento() -> pd.DataFrame:
    """Lee el CSV manual de referencia normal del circuito."""
    preparar_csv_entrenamiento()
    df = pd.read_csv(TRAINING_DATA_PATH, encoding="utf-8-sig")
    return normalizar_dataframe(df)


# ---------------------------------------------------------------------------
# Autoencoder LSTM
# ---------------------------------------------------------------------------
def crear_ventanas(data: np.ndarray, window_size: int) -> np.ndarray:
    """Crea ventanas deslizantes de W muestras."""
    if len(data) < window_size:
        return np.empty((0, window_size, data.shape[1]), dtype=float)
    return np.stack([data[i : i + window_size] for i in range(len(data) - window_size + 1)])


def construir_autoencoder(window_size: int, n_features: int) -> keras.Model:
    inputs = keras.Input(shape=(window_size, n_features))
    encoded = layers.LSTM(32, activation="tanh", return_sequences=True)(inputs)
    encoded = layers.LSTM(LATENT_DIM, activation="tanh", return_sequences=False)(encoded)
    decoded = layers.RepeatVector(window_size)(encoded)
    decoded = layers.LSTM(LATENT_DIM, activation="tanh", return_sequences=True)(decoded)
    decoded = layers.LSTM(32, activation="tanh", return_sequences=True)(decoded)
    outputs = layers.TimeDistributed(layers.Dense(n_features))(decoded)

    model = keras.Model(inputs, outputs, name="watios_autoencoder_lstm")
    model.compile(optimizer="adam", loss="mse")
    return model


def calcular_errores_por_feature(model: keras.Model, X: np.ndarray) -> np.ndarray:
    """
    Calcula MSE por variable.
    Shape de salida: (n_ventanas, n_features).
    """
    X_pred = model.predict(X, verbose=0)
    return np.mean(np.power(X - X_pred, 2), axis=1)


def calcular_umbrales(errores_train: np.ndarray) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, float]]:
    medias = errores_train.mean(axis=0)
    desvios = errores_train.std(axis=0)
    umbrales = medias + ZSCORE_THRESHOLD * desvios

    return (
        {feat: float(umbrales[i]) for i, feat in enumerate(FEATURES)},
        {feat: float(medias[i]) for i, feat in enumerate(FEATURES)},
        {feat: float(desvios[i]) for i, feat in enumerate(FEATURES)},
    )


def guardar_umbrales(umbrales: Dict[str, float], medias: Dict[str, float], desvios: Dict[str, float]) -> None:
    payload = {
        "features": FEATURES,
        "variables_alerta": ALERT_FEATURES,
        "variables_informativas": INFORMATIVE_FEATURES,
        "window_size": WINDOW_SIZE,
        "zscore_umbral": ZSCORE_THRESHOLD,
        "umbrales": umbrales,
        "mse_media_referencia": medias,
        "mse_desviacion_referencia": desvios,
        "fuente": TRAINING_DATA_PATH,
        "actualizado_en": datetime.now().isoformat(timespec="seconds"),
    }
    os.makedirs(MODEL_DIR, exist_ok=True)
    with open(THRESHOLD_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def cargar_umbrales(model: keras.Model, scaler: StandardScaler) -> Dict[str, float]:
    """Carga umbrales por variable; si faltan, los recalcula sin reentrenar."""
    try:
        with open(THRESHOLD_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("features") == FEATURES and payload.get("window_size") == WINDOW_SIZE:
            umbrales = payload.get("umbrales", {})
            if all(feat in umbrales for feat in FEATURES):
                return {feat: float(umbrales[feat]) for feat in FEATURES}
    except FileNotFoundError:
        pass

    print("  Umbrales ausentes o desactualizados; se recalculan desde el CSV normal.")
    df_train = cargar_datos_entrenamiento()
    if len(df_train) < WINDOW_SIZE:
        raise ValueError(
            f"No se pueden recalcular umbrales: {TRAINING_DATA_PATH} tiene "
            f"{len(df_train)} filas validas y W={WINDOW_SIZE}."
        )
    data_train = scaler.transform(df_train[FEATURES].values)
    X_train = crear_ventanas(data_train, WINDOW_SIZE)
    errores_train = calcular_errores_por_feature(model, X_train)
    umbrales, medias, desvios = calcular_umbrales(errores_train)
    guardar_umbrales(umbrales, medias, desvios)
    return umbrales


def validar_modelo_y_scaler(model: keras.Model, scaler: StandardScaler) -> None:
    expected_shape = (WINDOW_SIZE, len(FEATURES))
    current_shape = tuple(model.input_shape[1:])
    if current_shape != expected_shape:
        raise ValueError(f"modelo espera {current_shape}, configuracion actual {expected_shape}")

    n_scaler = getattr(scaler, "n_features_in_", len(getattr(scaler, "mean_", [])))
    if int(n_scaler) != len(FEATURES):
        raise ValueError(f"scaler espera {n_scaler} features, configuracion actual {len(FEATURES)}")


def cargar_modelo_persistido() -> Optional[Tuple[keras.Model, StandardScaler, Dict[str, float]]]:
    """Carga artefactos si existen y son compatibles con features/ventana."""
    if not os.path.isdir(MODEL_DIR):
        return None
    if not os.path.exists(MODEL_PATH) or not os.path.exists(SCALER_PATH):
        print("  Directorio de modelo incompleto; se considera incompatible.")
        return None

    try:
        model = keras.models.load_model(MODEL_PATH, compile=False)
        model.compile(optimizer="adam", loss="mse")
        scaler = joblib.load(SCALER_PATH)
        validar_modelo_y_scaler(model, scaler)
        umbrales = cargar_umbrales(model, scaler)
        print(f"  Modelo cargado desde {MODEL_PATH}")
        return model, scaler, umbrales
    except Exception as exc:
        print(f"  Modelo guardado incompatible o no cargable: {exc}")
        return None


def entrenar_y_persistir_modelo() -> Tuple[keras.Model, StandardScaler, Dict[str, float], Optional[keras.callbacks.History]]:
    """Entrena desde el CSV normal y guarda modelo, scaler y umbrales."""
    df_train = cargar_datos_entrenamiento()
    minimo = WINDOW_SIZE + 2
    if len(df_train) < minimo:
        raise ValueError(
            f"{TRAINING_DATA_PATH} necesita al menos {minimo} filas validas; tiene {len(df_train)}."
        )

    print("  Entrenando Autoencoder LSTM desde datos_entrenamiento.csv...")
    scaler = StandardScaler()
    data_train = scaler.fit_transform(df_train[FEATURES].values)
    X_train = crear_ventanas(data_train, WINDOW_SIZE)

    model = construir_autoencoder(WINDOW_SIZE, len(FEATURES))
    validation_split = 0.1 if len(X_train) >= 10 else 0.0
    monitor = "val_loss" if validation_split else "loss"
    callbacks = [
        keras.callbacks.EarlyStopping(monitor=monitor, patience=8, restore_best_weights=True),
        keras.callbacks.ReduceLROnPlateau(monitor=monitor, factor=0.5, patience=4),
    ]

    history = model.fit(
        X_train,
        X_train,
        epochs=EPOCHS,
        batch_size=BATCH_SIZE,
        validation_split=validation_split,
        shuffle=False,
        callbacks=callbacks,
        verbose=0,
    )

    errores_train = calcular_errores_por_feature(model, X_train)
    umbrales, medias, desvios = calcular_umbrales(errores_train)

    os.makedirs(MODEL_DIR, exist_ok=True)
    model.save(MODEL_PATH)
    joblib.dump(scaler, SCALER_PATH)
    guardar_umbrales(umbrales, medias, desvios)
    print(f"  Artefactos guardados en {MODEL_DIR}")
    return model, scaler, umbrales, history


def obtener_modelo() -> Tuple[keras.Model, StandardScaler, Dict[str, float], Optional[keras.callbacks.History], bool]:
    cargado = cargar_modelo_persistido()
    if cargado is not None:
        model, scaler, umbrales = cargado
        print("  Reentrenamiento no requerido.")
        return model, scaler, umbrales, None, False

    model, scaler, umbrales, history = entrenar_y_persistir_modelo()
    return model, scaler, umbrales, history, True


# ---------------------------------------------------------------------------
# Consistencia de joule
# ---------------------------------------------------------------------------
def calcular_error_relativo(observado: np.ndarray, esperado: np.ndarray) -> np.ndarray:
    denom = np.maximum(np.abs(esperado), 1e-12)
    return np.abs(observado - esperado) / denom


def evaluar_consistencia_joule(df: pd.DataFrame) -> Dict[str, object]:
    """
    Verifica joule contra I^2 * R_CABLE.
    Algunos historiales usan mW para esta columna; se infiere el factor que
    mejor coincide para reportar consistencia sin alterar la feature original.
    """
    if df.empty:
        return {
            "r_cable": R_CABLE,
            "factor_inferido": 1.0,
            "formula": "irms^2 * R_CABLE",
            "porcentaje_consistente": 0.0,
            "error_relativo_mediano": None,
        }

    observado = df["joule"].to_numpy(dtype=float)
    esperado_w = np.power(df["irms"].to_numpy(dtype=float), 2) * R_CABLE
    error_w = calcular_error_relativo(observado, esperado_w)
    error_mw = calcular_error_relativo(observado, esperado_w * 1000.0)

    if np.nanmedian(error_mw) < np.nanmedian(error_w):
        factor = 1000.0
        errores = error_mw
        formula = "irms^2 * R_CABLE * 1000"
    else:
        factor = 1.0
        errores = error_w
        formula = "irms^2 * R_CABLE"

    return {
        "r_cable": float(R_CABLE),
        "factor_inferido": float(factor),
        "formula": formula,
        "tolerancia_relativa": float(JOULE_REL_TOLERANCE),
        "porcentaje_consistente": float(np.mean(errores <= JOULE_REL_TOLERANCE) * 100.0),
        "error_relativo_mediano": float(np.nanmedian(errores)),
        "error_relativo_maximo": float(np.nanmax(errores)),
    }


def detalle_joule(row: pd.Series, factor: float) -> Dict[str, object]:
    esperado = float((row["irms"] ** 2) * R_CABLE * factor)
    observado = float(row["joule"])
    error_rel = abs(observado - esperado) / max(abs(esperado), 1e-12)
    return {
        "joule_esperado": esperado,
        "joule_error_relativo": float(error_rel),
        "joule_consistente": bool(error_rel <= JOULE_REL_TOLERANCE),
    }


# ---------------------------------------------------------------------------
# Deteccion y payload
# ---------------------------------------------------------------------------
def crear_detalles(
    df: pd.DataFrame,
    errores: np.ndarray,
    umbrales: Dict[str, float],
    consistencia_joule: Dict[str, object],
) -> List[Dict[str, object]]:
    detalles: List[Dict[str, object]] = []
    factor_joule = float(consistencia_joule.get("factor_inferido", 1.0))

    for window_idx, mse_vector in enumerate(errores):
        row_idx = window_idx + WINDOW_SIZE - 1
        row = df.iloc[row_idx]
        lectura = int(row["lectura_sesion"])
        calibrando = lectura <= ALERT_START_READING

        mse_por_variable = {
            feat: float(mse_vector[i]) for i, feat in enumerate(FEATURES)
        }
        mse_supera_umbral = {
            feat: bool(mse_por_variable[feat] > float(umbrales[feat])) for feat in FEATURES
        }

        vrms_fuera_rango = bool(row["vrms"] < VRMS_MIN or row["vrms"] > VRMS_MAX)
        vrms_mse_alerta = mse_supera_umbral["vrms"]
        irms_mse_alerta = mse_supera_umbral["irms"]

        variables = []
        motivos = []
        if not calibrando:
            if vrms_fuera_rango or vrms_mse_alerta:
                variables.append("vrms")
                if vrms_fuera_rango:
                    motivos.append(
                        f"vrms={row['vrms']:.2f} V fuera de [{VRMS_MIN:.0f}, {VRMS_MAX:.0f}] V "
                        "RETIE 2024 (+/-10% sobre 110 V)"
                    )
                if vrms_mse_alerta:
                    motivos.append(
                        f"MSE de vrms {mse_por_variable['vrms']:.6g} supera "
                        f"umbral {umbrales['vrms']:.6g}"
                    )
            if irms_mse_alerta:
                variables.append("irms")
                motivos.append(
                    f"MSE de irms {mse_por_variable['irms']:.6g} supera "
                    f"umbral {umbrales['irms']:.6g}"
                )
        else:
            motivos.append(
                f"sistema_calibrando: lectura {lectura}/{ALERT_START_READING}; alertas deshabilitadas"
            )

        variables_informativas_mse_alto = [
            feat for feat in INFORMATIVE_FEATURES if mse_supera_umbral[feat]
        ]
        motivos_informativos = [
            f"MSE de {feat} {mse_por_variable[feat]:.6g} supera umbral informativo {umbrales[feat]:.6g}"
            for feat in variables_informativas_mse_alto
        ]

        alerta = bool(variables)
        if not alerta and not calibrando:
            motivos.append("Sin alerta: vrms e irms dentro de criterios activos.")

        error_mse_alerta = max(mse_por_variable[feat] for feat in ALERT_FEATURES)
        umbral_mse_alerta = max(float(umbrales[feat]) for feat in ALERT_FEATURES)
        joule_info = detalle_joule(row, factor_joule)

        detalles.append(
            {
                "fecha": str(row["fecha"]),
                "lectura_sesion": lectura,
                "sistema_calibrando": bool(calibrando),
                "valores": {feat: float(row[feat]) for feat in FEATURES},
                "alerta": alerta,
                "variables": variables,
                "variables_alerta": ALERT_FEATURES,
                "variables_informativas": INFORMATIVE_FEATURES,
                "variables_informativas_mse_alto": variables_informativas_mse_alto,
                "motivos": motivos,
                "motivos_informativos": motivos_informativos,
                "mse_por_variable": mse_por_variable,
                "umbral_por_variable": {feat: float(umbrales[feat]) for feat in FEATURES},
                "mse_supera_umbral": mse_supera_umbral,
                "activa_alerta_por_variable": {
                    "vrms": bool((vrms_fuera_rango or vrms_mse_alerta) and not calibrando),
                    "irms": bool(irms_mse_alerta and not calibrando),
                    "power": False,
                    "kwh": False,
                    "joule": False,
                },
                "vrms_fuera_rango": bool(vrms_fuera_rango),
                "vrms_rango_fijo": {"min": VRMS_MIN, "max": VRMS_MAX, "unidad": "V"},
                "error_mse": float(error_mse_alerta),
                "umbral_mse": float(umbral_mse_alerta),
                **joule_info,
            }
        )

    return detalles


def resumen_anomalias(detalles: List[Dict[str, object]]) -> Tuple[np.ndarray, Dict[str, int]]:
    anomalias = np.array([bool(det["alerta"]) for det in detalles], dtype=bool)
    conteos = {feat: 0 for feat in ALERT_FEATURES}
    for det in detalles:
        for feat in det["variables"]:
            if feat in conteos:
                conteos[feat] += 1
    return anomalias, conteos


def construir_payload(
    df: pd.DataFrame,
    detalles: List[Dict[str, object]],
    umbrales: Dict[str, float],
    modelo_reentrenado: bool,
    consistencia_joule: Dict[str, object],
) -> Dict[str, object]:
    n_anomalias = int(sum(bool(det["alerta"]) for det in detalles))
    n_evaluadas = len(detalles)
    return {
        "n_lecturas": int(len(df)),
        "lecturas_evaluadas": int(n_evaluadas),
        "n_anomalias": n_anomalias,
        "tasa_pct": float((n_anomalias / n_evaluadas * 100.0) if n_evaluadas else 0.0),
        "umbral_mse": float(max(umbrales[feat] for feat in ALERT_FEATURES)),
        "umbral_por_variable": {feat: float(umbrales[feat]) for feat in FEATURES},
        "umbrales_por_variable": {feat: float(umbrales[feat]) for feat in FEATURES},
        "modelo": "Autoencoder LSTM",
        "modelo_reentrenado": bool(modelo_reentrenado),
        "ventana": int(WINDOW_SIZE),
        "features": FEATURES,
        "variables_alerta": ALERT_FEATURES,
        "variables_informativas": INFORMATIVE_FEATURES,
        "vrms_rango_fijo": {"min": VRMS_MIN, "max": VRMS_MAX, "unidad": "V"},
        "alert_start_reading": int(ALERT_START_READING),
        "consistencia_joule": consistencia_joule,
        "ultima_lectura": detalles[-1] if detalles else None,
        "resultados": detalles,
    }


def enviar_resultados_al_servidor(payload: Dict[str, object]) -> None:
    try:
        res = requests.post(ML_RESULT_URL, json=payload, timeout=10)
        res.raise_for_status()
        print("  Resultados enviados a POST /ml/result.")
    except Exception as exc:
        print(f"  Error al notificar al servidor: {exc}")


# ---------------------------------------------------------------------------
# Reportes
# ---------------------------------------------------------------------------
def generar_grafica(df: pd.DataFrame, errores: np.ndarray, detalles: List[Dict[str, object]], umbrales: Dict[str, float]) -> None:
    fechas_eval = df["fecha"].iloc[WINDOW_SIZE - 1 :].reset_index(drop=True)
    anomalias = np.array([bool(det["alerta"]) for det in detalles], dtype=bool)
    colores = {
        "vrms": "#00e5ff",
        "irms": "#ff4081",
        "power": "#69ff47",
        "kwh": "#ffb020",
        "joule": "#b388ff",
    }

    fig, axes = plt.subplots(len(FEATURES) + 1, 1, figsize=(14, 4 * (len(FEATURES) + 1)))
    fig.patch.set_facecolor("#0a0e17")

    ax = axes[0]
    ax.set_facecolor("#111827")
    for i, feat in enumerate(FEATURES):
        label = f"MSE {feat}" if feat in ALERT_FEATURES else f"MSE {feat} (info)"
        ax.plot(fechas_eval, errores[:, i], color=colores[feat], linewidth=0.9, alpha=0.85, label=label)
        ax.axhline(umbrales[feat], color=colores[feat], linestyle="--", linewidth=0.7, alpha=0.45)
    if len(detalles):
        ax.scatter(fechas_eval[anomalias], np.max(errores[anomalias][:, :2], axis=1), color="#ff3b3b", s=24, zorder=5, label="Alertas")
    ax.set_title("MSE de reconstruccion por variable", color="#e2e8f0", fontsize=10)
    ax.tick_params(colors="#94a3b8", labelsize=7)
    for spine in ax.spines.values():
        spine.set_color("#1e293b")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.legend(fontsize=7, facecolor="#111827", labelcolor="#e2e8f0", ncol=2)

    for i, feat in enumerate(FEATURES):
        ax = axes[i + 1]
        ax.set_facecolor("#111827")
        vals = df[feat].iloc[WINDOW_SIZE - 1 :].reset_index(drop=True)
        ax.plot(fechas_eval, vals, color=colores[feat], linewidth=0.9, alpha=0.9)
        if feat == "vrms":
            ax.axhline(VRMS_MIN, color="#ffb020", linestyle="--", linewidth=0.8, alpha=0.8)
            ax.axhline(VRMS_MAX, color="#ffb020", linestyle="--", linewidth=0.8, alpha=0.8)
        if len(detalles):
            ax.scatter(fechas_eval[anomalias], vals[anomalias], color="#ff3b3b", s=18, zorder=5)
        tipo = "alerta" if feat in ALERT_FEATURES else "informativa"
        ax.set_title(f"{feat.upper()} ({tipo})", color="#e2e8f0", fontsize=9)
        ax.tick_params(colors="#94a3b8", labelsize=7)
        for spine in ax.spines.values():
            spine.set_color("#1e293b")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    plt.tight_layout(pad=2.0)
    plt.savefig(GRAPH_PATH, dpi=120, bbox_inches="tight", facecolor="#0a0e17")
    plt.close(fig)


def generar_loss(history: Optional[keras.callbacks.History]) -> str:
    if history is None:
        return "<p class='muted'>Modelo cargado desde disco; no hubo reentrenamiento en este arranque.</p>"

    fig, ax = plt.subplots(figsize=(8, 3))
    fig.patch.set_facecolor("#0a0e17")
    ax.set_facecolor("#111827")
    ax.plot(history.history.get("loss", []), color="#00e5ff", linewidth=1.4, label="loss")
    if "val_loss" in history.history:
        ax.plot(history.history["val_loss"], color="#ff4081", linewidth=1.4, label="val_loss")
    ax.set_title("Curva de entrenamiento", color="#e2e8f0", fontsize=9)
    ax.tick_params(colors="#94a3b8", labelsize=7)
    for spine in ax.spines.values():
        spine.set_color("#1e293b")
    ax.legend(fontsize=7, facecolor="#111827", labelcolor="#e2e8f0")
    plt.tight_layout()
    plt.savefig(LOSS_PATH, dpi=120, bbox_inches="tight", facecolor="#0a0e17")
    plt.close(fig)
    return '<img src="loss_curve.png" alt="Curva de entrenamiento">'


def generar_tabla_anomalias(df: pd.DataFrame, detalles: List[Dict[str, object]]) -> str:
    filas = []
    for det in detalles:
        if not det["alerta"]:
            continue
        vals = det["valores"]
        filas.append(
            {
                "fecha": det["fecha"],
                "lectura": det["lectura_sesion"],
                "variables": ", ".join(det["variables"]),
                "motivos": "; ".join(det["motivos"]),
                "vrms": vals["vrms"],
                "irms": vals["irms"],
                "power": vals["power"],
                "kwh": vals["kwh"],
                "joule": vals["joule"],
                "error_mse": det["error_mse"],
            }
        )

    if not filas:
        return "<p class='muted'>No se detectaron alertas en las lecturas evaluadas.</p>"

    tabla = pd.DataFrame(filas).sort_values("error_mse", ascending=False)
    return tabla.to_html(index=False, float_format="%.6f", classes="tabla", border=0, escape=True)


def generar_reporte_html(
    df: pd.DataFrame,
    errores: np.ndarray,
    detalles: List[Dict[str, object]],
    umbrales: Dict[str, float],
    history: Optional[keras.callbacks.History],
    modelo_reentrenado: bool,
    consistencia_joule: Dict[str, object],
) -> None:
    generar_grafica(df, errores, detalles, umbrales)
    loss_html = generar_loss(history)
    tabla_html = generar_tabla_anomalias(df, detalles)

    n_evaluadas = len(detalles)
    n_anomalias = int(sum(bool(det["alerta"]) for det in detalles))
    tasa = (n_anomalias / n_evaluadas * 100.0) if n_evaluadas else 0.0

    umbrales_rows = []
    for feat in FEATURES:
        tipo = "alerta" if feat in ALERT_FEATURES else "informativa"
        umbrales_rows.append(
            "<tr>"
            f"<td>{html.escape(feat)}</td>"
            f"<td>{tipo}</td>"
            f"<td>{UNITS[feat]}</td>"
            f"<td>{umbrales[feat]:.8g}</td>"
            "</tr>"
        )

    html_doc = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Watios - Reporte de Anomalias</title>
<style>
  body {{ background:#0a0e17; color:#e2e8f0; font-family:Arial, sans-serif; padding:32px; }}
  h1 {{ color:#00e5ff; font-size:1.45rem; margin:0 0 6px; }}
  h2 {{ color:#94a3b8; font-size:.86rem; text-transform:uppercase; letter-spacing:1px; margin:28px 0 12px; }}
  .meta,.muted {{ color:#94a3b8; font-size:.82rem; line-height:1.55; }}
  .kpi-row {{ display:flex; gap:14px; flex-wrap:wrap; margin:24px 0; }}
  .kpi {{ background:#111827; border:1px solid #1e293b; border-radius:8px; padding:14px 18px; min-width:150px; flex:1; }}
  .kpi-val {{ color:#00e5ff; font-size:1.7rem; font-weight:700; line-height:1; }}
  .kpi-alert .kpi-val {{ color:#ff3b3b; }}
  .kpi-lbl {{ color:#94a3b8; font-size:.68rem; text-transform:uppercase; letter-spacing:.7px; margin-top:7px; }}
  img {{ width:100%; border:1px solid #1e293b; border-radius:8px; margin:8px 0 20px; }}
  .tabla {{ width:100%; border-collapse:collapse; font-size:.78rem; }}
  .tabla th {{ background:#0d1420; color:#94a3b8; padding:9px 10px; text-align:left; border-bottom:1px solid #1e293b; }}
  .tabla td {{ padding:8px 10px; border-bottom:1px solid #1e293b; color:#e2e8f0; vertical-align:top; }}
  .note {{ background:#111827; border:1px solid #1e293b; border-radius:8px; padding:14px 16px; color:#cbd5e1; line-height:1.55; }}
  .footer {{ color:#94a3b8; font-size:.72rem; margin-top:32px; border-top:1px solid #1e293b; padding-top:14px; }}
</style>
</head>
<body>
<h1>Watios - Reporte de Anomalias</h1>
<p class="meta">
Generado: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} |
Modelo: Autoencoder LSTM TensorFlow {tf.__version__} |
Ventana: {WINDOW_SIZE} lecturas |
Reentrenado en este arranque: {"si" if modelo_reentrenado else "no"}
</p>

<div class="note">
  Variables de alerta: <b>vrms</b> e <b>irms</b>. Variables informativas usadas por el autoencoder:
  <b>power</b>, <b>kwh</b> y <b>joule</b>. Las derivadas tienen MSE y umbral propio,
  pero no activan alertas porque no son causalmente independientes.
</div>

<div class="kpi-row">
  <div class="kpi"><div class="kpi-val">{len(df)}</div><div class="kpi-lbl">Lecturas recibidas</div></div>
  <div class="kpi"><div class="kpi-val">{n_evaluadas}</div><div class="kpi-lbl">Lecturas evaluadas</div></div>
  <div class="kpi kpi-alert"><div class="kpi-val">{n_anomalias}</div><div class="kpi-lbl">Alertas</div></div>
  <div class="kpi"><div class="kpi-val">{tasa:.1f}%</div><div class="kpi-lbl">Tasa evaluada</div></div>
  <div class="kpi"><div class="kpi-val">{VRMS_MIN:.0f}-{VRMS_MAX:.0f} V</div><div class="kpi-lbl">Rango fijo vrms</div></div>
  <div class="kpi"><div class="kpi-val">{ALERT_START_READING}</div><div class="kpi-lbl">Lecturas calibrando</div></div>
</div>

<h2>Umbrales MSE por variable</h2>
<table class="tabla">
  <thead><tr><th>Variable</th><th>Rol</th><th>Unidad</th><th>Umbral MSE</th></tr></thead>
  <tbody>{"".join(umbrales_rows)}</tbody>
</table>

<h2>Consistencia de joule</h2>
<p class="meta">
R_CABLE={R_CABLE:g}. Formula verificada: {html.escape(str(consistencia_joule.get("formula")))}.
Consistencia: {float(consistencia_joule.get("porcentaje_consistente", 0.0)):.1f}% de lecturas dentro de
tolerancia relativa {JOULE_REL_TOLERANCE:.0%}.
</p>

<h2>MSE y series de tiempo</h2>
<img src="graficas_anomalias.png" alt="Graficas de anomalias">

<h2>Entrenamiento</h2>
{loss_html}

<h2>Lecturas con alerta</h2>
{tabla_html}

<div class="footer">
  Fuente normal: {html.escape(TRAINING_DATA_PATH)} |
  Modelo persistido: {html.escape(MODEL_PATH)} |
  Scaler: {html.escape(SCALER_PATH)} |
  Umbrales: {html.escape(THRESHOLD_PATH)}
</div>
</body>
</html>"""

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html_doc)
    print(f"  Reporte HTML actualizado: {REPORT_PATH}")


def guardar_conclusiones(
    df: pd.DataFrame,
    detalles: List[Dict[str, object]],
    umbrales: Dict[str, float],
    modelo_reentrenado: bool,
    consistencia_joule: Dict[str, object],
) -> str:
    stamp = timestamp_archivo()
    anomalias = [det for det in detalles if det["alerta"]]
    payload = {
        "generado_en": datetime.now().isoformat(timespec="seconds"),
        "servidor_origen": LOCAL_SERVER,
        "modelo": "Autoencoder LSTM",
        "tensorflow": tf.__version__,
        "modelo_reentrenado": bool(modelo_reentrenado),
        "dataset_entrenamiento": TRAINING_DATA_PATH,
        "modelo_path": MODEL_PATH,
        "scaler_path": SCALER_PATH,
        "umbral_path": THRESHOLD_PATH,
        "total_lecturas": int(len(df)),
        "lecturas_evaluadas": int(len(detalles)),
        "anomalias_detectadas": int(len(anomalias)),
        "tasa_anomalias_pct": float((len(anomalias) / len(detalles) * 100.0) if detalles else 0.0),
        "window_size": int(WINDOW_SIZE),
        "features": FEATURES,
        "variables_alerta": ALERT_FEATURES,
        "variables_informativas": INFORMATIVE_FEATURES,
        "umbral_por_variable": {feat: float(umbrales[feat]) for feat in FEATURES},
        "vrms_rango_fijo": {"min": VRMS_MIN, "max": VRMS_MAX, "unidad": "V"},
        "alert_start_reading": int(ALERT_START_READING),
        "consistencia_joule": consistencia_joule,
        "primeras_anomalias": anomalias[:50],
        "ultima_lectura": detalles[-1] if detalles else None,
        "reporte_html": REPORT_PATH,
        "grafica": GRAPH_PATH,
    }

    historico_path = os.path.join(ARTIFACTS_DIR, f"conclusiones_{stamp}.json")
    with open(LATEST_CONCLUSIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(historico_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return historico_path


# ---------------------------------------------------------------------------
# Flujo principal
# ---------------------------------------------------------------------------
def ejecutar_analisis() -> None:
    print("=" * 68)
    print(f"  Analisis Watios LSTM - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 68)
    print(f"  Servidor local: {LOCAL_SERVER}")
    print(f"  Features: {', '.join(FEATURES)}")
    print(f"  Alertas: {', '.join(ALERT_FEATURES)} | Informativas: {', '.join(INFORMATIVE_FEATURES)}")

    df = cargar_datos_servidor()
    if len(df) < WINDOW_SIZE:
        print(f"  Aun no hay suficientes lecturas para una ventana W={WINDOW_SIZE}. Lecturas: {len(df)}")
        return

    model, scaler, umbrales, history, modelo_reentrenado = obtener_modelo()
    consistencia_joule = evaluar_consistencia_joule(df)

    data_norm = scaler.transform(df[FEATURES].values)
    X = crear_ventanas(data_norm, WINDOW_SIZE)
    errores = calcular_errores_por_feature(model, X)
    detalles = crear_detalles(df, errores, umbrales, consistencia_joule)
    anomalias, conteos = resumen_anomalias(detalles)

    n_anomalias = int(np.sum(anomalias))
    print(f"  Lecturas recibidas: {len(df)}")
    print(f"  Ventanas evaluadas: {len(detalles)} con W={WINDOW_SIZE}")
    print(f"  Primeras {ALERT_START_READING} lecturas: sistema_calibrando, sin alertas")
    print("  Umbrales MSE por variable:")
    for feat in FEATURES:
        rol = "alerta" if feat in ALERT_FEATURES else "info"
        print(f"    {feat:<5} ({rol}): {umbrales[feat]:.8g}")
    print(f"  Alertas totales: {n_anomalias} / {len(detalles)}")
    print(f"    vrms: {conteos['vrms']} | irms: {conteos['irms']}")
    print(
        "  Consistencia joule: "
        f"{consistencia_joule['porcentaje_consistente']:.1f}% "
        f"({consistencia_joule['formula']})"
    )

    if n_anomalias:
        print("  Primeras alertas:")
        for det in [d for d in detalles if d["alerta"]][:5]:
            print(f"    [{det['fecha']}] {', '.join(det['variables'])}: {'; '.join(det['motivos'])}")

    generar_reporte_html(df, errores, detalles, umbrales, history, modelo_reentrenado, consistencia_joule)
    conclusiones_path = guardar_conclusiones(
        df, detalles, umbrales, modelo_reentrenado, consistencia_joule
    )
    print(f"  Conclusiones guardadas: {conclusiones_path}")

    payload = construir_payload(df, detalles, umbrales, modelo_reentrenado, consistencia_joule)
    enviar_resultados_al_servidor(payload)
    print("  Analisis finalizado.")


def contar_lecturas_servidor() -> int:
    res = requests.get(DATA_URL, timeout=10)
    res.raise_for_status()
    rows = res.json().get("rows", [])
    return max(0, len(rows) - 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Modulo ML Watios con Autoencoder LSTM")
    parser.add_argument("--daemon", action="store_true", help="Corre continuamente consultando GET /data")
    parser.add_argument("--interval", type=int, default=DAEMON_INTERVAL, help="Segundos entre revisiones")
    args = parser.parse_args()

    if not args.daemon:
        ejecutar_analisis()
        return

    print("Iniciando anomaliastf.py en modo demonio...")
    print(f"  GET {DATA_URL}")
    print(f"  POST {ML_RESULT_URL}")
    ultimo_conteo = 0
    while True:
        try:
            conteo_actual = contar_lecturas_servidor()
            if conteo_actual < ultimo_conteo:
                print("  [daemon] Nueva sesion detectada; reiniciando contador local.")
                ultimo_conteo = 0

            if conteo_actual > ultimo_conteo:
                if conteo_actual >= WINDOW_SIZE:
                    ejecutar_analisis()
                else:
                    print(
                        f"  [daemon] {conteo_actual}/{WINDOW_SIZE} lecturas; esperando ventana completa."
                    )
                ultimo_conteo = conteo_actual
        except Exception as exc:
            print(f"  [daemon] Error: {exc}")

        time.sleep(max(1, args.interval))


if __name__ == "__main__":
    main()
