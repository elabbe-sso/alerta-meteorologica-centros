"""
API que centraliza las consultas a Open-Meteo para app.html.

Por qué existe: si cada persona que abre app.html consulta Open-Meteo
directo desde su navegador, en una red corporativa (donde todos los
empleados comparten la misma IP pública de salida) el volumen combinado
puede superar el límite de Open-Meteo (600 llamadas/min, 5.000/hora,
10.000/día POR IP) y todos empiezan a recibir error 429 "Too Many
Requests" — no solo quien lo satura.

La solución: este servidor consulta Open-Meteo (con la IP del servidor,
no la de cada usuario) para los 68 puntos, guarda el resultado en
memoria, y se lo sirve a quien lo pida. Así, sin importar si son 5 o 500
personas viendo la app a la vez, Open-Meteo solo ve las llamadas de este
servidor.

IMPORTANTE: el refresco usa las versiones _batch de fuentes.py (Open-Meteo
base + DWD ICON + Marine), que consultan los 68 puntos en 1 SOLA llamada
HTTP por fuente (en vez de 68 llamadas individuales) -- Open-Meteo soporta
listas de lat/lon separadas por coma en un mismo request. Sin esto, cada
refresco hacía ~204 llamadas individuales (68 puntos x 3 fuentes), lo que
terminó agotando el límite de 600 llamadas/minuto y generando error 429
para TODOS los puntos, justo el problema que este servidor existe para
evitar. Solo yr.no (api.met.no, un host distinto, sin este límite) se
sigue consultando por punto.

Se despliega en Render.com (gratis) — ver README.md para instrucciones.
"""

from __future__ import annotations
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, jsonify

from config import PUNTOS_ESPECIFICOS
from fuentes import (
    fetch_datos_open_meteo_batch,
    fetch_datos_open_meteo_icon_batch,
    fetch_datos_marino_batch,
    fetch_datos_yr,
    _combinar_fuentes,
)

app = Flask(__name__)

# Cuántos puntos se consultan en paralelo para yr.no (la única fuente que
# sigue siendo por punto -- ver nota arriba sobre por qué las demás ahora
# van en lote).
MAX_HILOS = 12

# Cada cuánto se refresca el caché. Con app.html actualizándose sola cada
# 15 min, no hace falta refrescar más seguido que eso.
CACHE_TTL_SEGUNDOS = 15 * 60

_cache_lock = threading.Lock()
_cache: dict = {"datos": {}, "actualizado_en": 0.0}


def _refrescar_cache() -> None:
    puntos_coords = [(p[1], p[2]) for p in PUNTOS_ESPECIFICOS]  # [(lat, lon), ...]

    # Solo 3 llamadas HTTP en total para los 68 puntos (Open-Meteo base,
    # DWD ICON, Marine) -- en vez de 68 x 3 = 204. Si una fuente completa
    # falla, se sigue con las demás (mismo espíritu que antes, ahora a
    # nivel de lote en vez de punto por punto).
    try:
        om_lote = fetch_datos_open_meteo_batch(puntos_coords, horas_viento=12)
    except Exception as e:
        print(f"[api.py] fetch_datos_open_meteo_batch falló: {e}")
        om_lote = [None] * len(puntos_coords)

    try:
        icon_lote = fetch_datos_open_meteo_icon_batch(puntos_coords, horas_viento=12)
    except Exception as e:
        print(f"[api.py] fetch_datos_open_meteo_icon_batch falló: {e}")
        icon_lote = [None] * len(puntos_coords)

    try:
        marino_lote = fetch_datos_marino_batch(puntos_coords)
    except Exception as e:
        print(f"[api.py] fetch_datos_marino_batch falló: {e}")
        marino_lote = [None] * len(puntos_coords)

    # yr.no: host distinto (api.met.no), no está implicado en el límite de
    # Open-Meteo -- se mantiene por punto, en paralelo para no demorar.
    def _yr_de(idx: int):
        lat, lon = puntos_coords[idx]
        try:
            return idx, fetch_datos_yr(lat, lon, horas_viento=12)
        except Exception:
            return idx, None

    yr_lote: list = [None] * len(puntos_coords)
    with ThreadPoolExecutor(max_workers=MAX_HILOS) as executor:
        futuros = [executor.submit(_yr_de, i) for i in range(len(puntos_coords))]
        for futuro in as_completed(futuros):
            idx, datos_yr = futuro.result()
            yr_lote[idx] = datos_yr

    sin_ola = {"altura_ola_actual_m": None, "altura_ola_max_m": None}

    resultado: dict = {}
    for i, punto in enumerate(PUNTOS_ESPECIFICOS):
        nombre, lat, lon, comuna, region = punto
        fuentes_punto = [d for d in (om_lote[i], yr_lote[i], icon_lote[i]) if d]
        try:
            datos = _combinar_fuentes(fuentes_punto)
            datos.update(marino_lote[i] or sin_ola)
            datos["comuna"] = comuna
            datos["region"] = region
            datos["lat"] = lat
            datos["lon"] = lon
            resultado[nombre] = datos
        except Exception as e:
            resultado[nombre] = {"error": str(e)}

    _cache["datos"] = resultado
    _cache["actualizado_en"] = time.time()


def _cache_vigente() -> bool:
    return (time.time() - _cache["actualizado_en"]) < CACHE_TTL_SEGUNDOS


# Evita lanzar varios refrescos en segundo plano a la vez si llegan varias
# consultas mientras el caché ya está vencido (una sola vez basta).
_refrescando = False


def _refrescar_en_segundo_plano() -> None:
    global _refrescando
    try:
        _refrescar_cache()
    finally:
        with _cache_lock:
            _refrescando = False


@app.after_request
def _agregar_cors(response):
    # app.html vive en GitHub Pages (otro dominio), así que el navegador
    # necesita este header para no bloquear la respuesta por CORS.
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@app.route("/api/datos")
def api_datos():
    """
    Sirve el caché INMEDIATO, sin hacer esperar a quien consulta — incluso
    si está un poco vencido (hasta 15 min de más), se sigue viendo bien y
    es mucho mejor que una espera de 10-30 segundos. Si ya venció, dispara
    un refresco en segundo plano (no bloquea esta respuesta) para que la
    PRÓXIMA consulta ya tenga datos frescos. La única excepción real es la
    primerísima vez que arranca el proceso: ahí no hay nada guardado
    todavía, así que esa consulta sí tiene que esperar el primer refresco.
    """
    global _refrescando
    with _cache_lock:
        hay_datos = bool(_cache["datos"])
        vigente = _cache_vigente()
        if not hay_datos:
            _refrescar_cache()  # primera vez: no hay nada que servir, toca esperar
        elif not vigente and not _refrescando:
            _refrescando = True
            threading.Thread(target=_refrescar_en_segundo_plano, daemon=True).start()

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
