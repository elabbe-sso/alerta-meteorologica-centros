"""
API mínima que conecta el motor de alertas con el dashboard.

Expone dos cosas:
  GET /api/alertas   -> JSON { "Abtao": "roja", "Calen 1": "amarilla", ... }
                        (una entrada por cada punto específico monitoreado)
  GET /              -> sirve el propio dashboard.html

El color por punto se deriva combinando:
  - las alertas propias por umbral (Open-Meteo + yr.no), y
  - las alertas oficiales de SENAPRED, aplicadas con esta regla:
      * si la alerta trae una COMUNA específica, solo eleva el color de los
        puntos vinculados a ESA comuna (nunca a toda la región);
      * si la alerta NO trae comuna (una ATP genuinamente regional), ahí sí
        se aplica a todos los puntos de esa región.

Ejecutar:
    pip install flask
    python api.py
    # abre http://localhost:5000
"""

from __future__ import annotations
from pathlib import Path
from flask import Flask, jsonify, send_file

from config import PUNTOS_ESPECIFICOS, obtener_umbrales_punto
from fuentes import fetch_datos_consenso, fetch_alertas_senapred
from reglas import evaluar_umbrales

app = Flask(__name__)
AQUI = Path(__file__).parent


# Mapea el tipo de alerta propia a un color estilo SENAPRED.
# Ajusta la severidad según tu criterio operativo.
SEVERIDAD = {
    "viento": "amarilla",
    "rafagas": "amarilla",
    "precipitacion": "amarilla",
    "helada": "verde",
    "oleaje": "amarilla",
    "tormenta": "roja",
}
ORDEN = {"verde": 1, "amarilla": 2, "roja": 3}


def color_por_comuna() -> dict:
    resultado = {}

    # 1. Alertas propias por umbral, evaluadas en las coordenadas exactas
    #    de cada punto (no en el centro de ninguna comuna).
    for nombre, lat, lon, comuna, region in PUNTOS_ESPECIFICOS:
        try:
            datos = fetch_datos_consenso(lat, lon)
            alertas = evaluar_umbrales(nombre, datos, obtener_umbrales_punto(nombre))
        except Exception:
            alertas = []
        colores = [SEVERIDAD.get(a["tipo"], "amarilla") for a in alertas]
        if colores:
            resultado[nombre] = max(colores, key=lambda c: ORDEN[c])

    # 2. Alertas oficiales de SENAPRED: DESACTIVADAS a propósito, igual que
    #    en main.py. El dashboard solo colorea por umbral propio. Para
    #    reactivar SENAPRED, descomenta este bloque.
    #
    # regiones = sorted({p[4] for p in PUNTOS_ESPECIFICOS})
    # for region in regiones:
    #     try:
    #         oficiales = fetch_alertas_senapred(region)
    #     except Exception:
    #         oficiales = []
    #     for alerta in oficiales:
    #         comuna_alerta = alerta.get("comuna")
    #         color = alerta.get("color") or "amarilla"
    #         for nombre, lat, lon, comuna, reg in PUNTOS_ESPECIFICOS:
    #             if reg != region:
    #                 continue
    #             # Con comuna específica: solo aplica a esa comuna.
    #             # Sin comuna (ATP genuinamente regional): aplica a toda la región.
    #             aplica = (comuna_alerta == comuna) if comuna_alerta else True
    #             if not aplica:
    #                 continue
    #             actual = resultado.get(nombre)
    #             if actual is None or ORDEN.get(color, 2) > ORDEN.get(actual, 0):
    #                 resultado[nombre] = color

    return resultado


@app.route("/api/alertas")
def api_alertas():
    return jsonify(color_por_comuna())


@app.route("/")
def dashboard():
    return send_file(AQUI / "dashboard.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
