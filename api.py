"""
API que centraliza las consultas a Open-Meteo para app.html.

Por qué existe: si cada persona que abre app.html consulta Open-Meteo
directo desde su navegador, en una red corporativa (donde todos los
empleados comparten la misma IP pública de salida) el volumen combinado
puede superar el límite de Open-Meteo (600 llamadas/min, 5.000/hora,
10.000/día POR IP) y todos empiezan a recibir error 429 "Too Many
Requests" — no solo quien lo satura.

La solución: este servidor consulta Open-Meteo UNA sola vez cada 15
minutos (con la IP del servidor, no la de cada usuario) para los 67
puntos, guarda el resultado en memoria, y se lo sirve a quien lo pida.
Así, sin importar si son 5 o 500 personas viendo la app a la vez,
Open-Meteo solo ve las llamadas de este servidor.

Se despliega en Render.com (gratis) — ver README.md para instrucciones.
"""

from __future__ import annotations
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, jsonify

from config import PUNTOS_ESPECIFICOS
from fuentes import fetch_datos_consenso

app = Flask(__name__)

# Cuántos puntos se consultan en paralelo al refrescar el caché (mismo
# criterio que main.py: red, no CPU, así que paralelizar ayuda mucho).
MAX_HILOS = 12

# Cada cuánto se refresca el caché. Con app.html actualizándose sola cada
# 15 min, no hace falta refrescar más seguido que eso.
CACHE_TTL_SEGUNDOS = 15 * 60

_cache_lock = threading.Lock()
_cache: dict = {"datos": {}, "actualizado_en": 0.0}


def _obtener_datos_de_un_punto(punto: tuple) -> tuple[str, dict]:
    nombre, lat, lon, comuna, region = punto
    try:
        datos = fetch_datos_consenso(lat, lon, horas_viento=12)
        datos["comuna"] = comuna
        datos["region"] = region
        datos["lat"] = lat
        datos["lon"] = lon
        return nombre, datos
    except Exception as e:
        return nombre, {"error": str(e)}


def _refrescar_cache() -> None:
    resultado: dict = {}
    with ThreadPoolExecutor(max_workers=MAX_HILOS) as executor:
        futuros = [executor.submit(_obtener_datos_de_un_punto, p) for p in PUNTOS_ESPECIFICOS]
        for futuro in as_completed(futuros):
            nombre, datos = futuro.result()
            resultado[nombre] = datos
    _cache["datos"] = resultado
    _cache["actualizado_en"] = time.time()


def _cache_vigente() -> bool:
    return (time.time() - _cache["actualizado_en"]) < CACHE_TTL_SEGUNDOS


# NOTA: antes había un precalentado automático del caché apenas arrancaba
# el proceso (un hilo de fondo que salía a buscar los 68 puntos de
# inmediato). Se quitó: en el plan gratis de Render (memoria y CPU muy
# limitadas), ese trabajo pesado justo al arrancar parecía estar causando
# reinicios en bucle (varios 503 seguidos hasta que se estabilizaba solo).
# En su lugar, el cronjob externo que mantiene el servidor despierto ahora
# apunta directo a /api/datos (no solo a la página base) — así cumple las
# dos funciones a la vez: evita que se duerma, Y mantiene el caché fresco,
# sin sobrecargar el arranque del proceso.


@app.after_request
def _agregar_cors(response):
    # app.html vive en GitHub Pages (otro dominio), así que el navegador
    # necesita este header para no bloquear la respuesta por CORS.
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@app.route("/api/datos")
def api_datos():
    with _cache_lock:
        if not _cache_vigente():
            _refrescar_cache()
    return jsonify(_cache["datos"])


@app.route("/")
def estado():
    return jsonify({
        "status": "ok",
        "puntos_monitoreados": len(PUNTOS_ESPECIFICOS),
        "cache_actualizado_hace_segundos": round(time.time() - _cache["actualizado_en"]),
    })


if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=puerto)
