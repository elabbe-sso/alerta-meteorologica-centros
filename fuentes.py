"""
Recolectores de datos meteorológicos.

Este módulo separa cada fuente en su propia función para que puedas
activarlas o desactivarlas de forma independiente.

Estado de cada fuente en este prototipo:

1. Open-Meteo         -> FUNCIONAL. API pública, sin llave, ideal para
                         datos crudos por coordenada (viento, lluvia,
                         temperatura, nieve). Se usa como fuente principal
                         de "umbrales propios" en este prototipo.

2. DMC (Climatología)  -> ADAPTADOR DE EJEMPLO, a completar. El portal
                         (climatologia.meteochile.gob.cl) expone datos por
                         estación mediante formularios/reportes, no una
                         REST API simple y documentada. Para producción
                         conviene: (a) pedir acceso a datos vía la
                         Plataforma de Datos (plataformadedatos.cl, requiere
                         access_key_id/secret_access_key), o (b) usarla
                         solo para validar/contrastar contra Open-Meteo.

3. SENAPRED (alertas)  -> ADAPTADOR DE EJEMPLO, a completar. El listado de
                         alertas vigentes se ve en senapred.cl/informate/alertas
                         y web.senapred.cl/archivos-de-alertas, pero para
                         obtener el JSON exacto que alimenta esa tabla hay
                         que inspeccionar las llamadas de red del sitio
                         (Devtools -> Network) porque no hay documentación
                         pública de API. Dejamos la función lista para
                         enchufar esa URL apenas la identifiques.
"""

from __future__ import annotations
import requests
import time
from datetime import datetime, timedelta, timezone


# ======================================================================
# 1. OPEN-METEO — datos crudos por coordenada (funcional)
# ======================================================================
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def _get_con_reintento(url: str, params: dict, timeout: int, intentos: int = 3):
    """
    GET con reintento automático ante 429 "Too Many Requests" -- Render
    (plan gratis) comparte el pool de IPs de salida entre TODOS sus
    clientes en la región, así que un 429 no siempre significa que NOSOTROS
    superamos el límite; puede ser tráfico de otra app ajena compartiendo
    la misma IP en ese momento. Como suele ser algo pasajero, esperar un
    poco y reintentar resuelve la mayoría de los casos.
    """
    espera = 2  # segundos, se duplica en cada intento (2, 4, 8...)
    ultimo_error = None
    for intento in range(1, intentos + 1):
        resp = requests.get(url, params=params, timeout=timeout)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp
        ultimo_error = resp
        if intento < intentos:
            print(f"[fuentes.py] 429 de Open-Meteo (intento {intento}/{intentos}), "
                  f"reintentando en {espera}s...")
            time.sleep(espera)
            espera *= 2
    ultimo_error.raise_for_status()  # ya no quedan reintentos: deja que falle normal


def _extremo_prevista(horas_iso: list[str], valores: list, horas_ventana: int, agregador) -> float | None:
    """
    Aplica `agregador` (min o max) SOLO entre las horas que faltan (desde
    ahora hacia adelante, hasta `horas_ventana` horas), nunca incluyendo
    horas ya pasadas. Se usa tanto para la mínima de helada (agregador=min)
    como para el máximo de viento/ráfaga (agregador=max) — en ambos casos,
    si la condición ya pasó y no se pronostica que continúe, deja de
    contar, y la alerta se cae sola en el próximo ciclo.
    """
    if not horas_iso or not valores:
        return None
    ahora = datetime.now()
    idx = next((i for i, h in enumerate(horas_iso) if datetime.fromisoformat(h) >= ahora), None)
    if idx is None:
        return None
    ventana = [v for v in valores[idx:idx + horas_ventana] if v is not None]
    return agregador(ventana) if ventana else None


# Ventana fija para la helada (no depende del horario de notificación).
HORAS_VENTANA_HELADA = 12

# Tormenta eléctrica: se avisa si se detecta ahora o dentro de esta ventana
# (mismo criterio que app.html). Códigos WMO que Open-Meteo usa para tormenta.
HORAS_VENTANA_TORMENTA = 6
CODIGOS_TORMENTA = {95, 96, 99}


def _parsear_respuesta_open_meteo(data: dict, horas_viento: int) -> dict:
    """
    Parsea la respuesta de UNA ubicación de la API base de Open-Meteo
    (mismo formato tanto si se consultó 1 punto como si esta ubicación es
    parte de una respuesta en lote de varios puntos a la vez).
    """
    current = data.get("current", {})
    hourly = data.get("hourly", {})
    daily = data.get("daily", {})
    horas = hourly.get("time", [])

    # Últimas 24h reales (desde "ahora" hacia atrás) — ya no es simplemente
    # "los últimos 24 del arreglo", porque con forecast_days=2 el arreglo
    # incluye también el día de mañana.
    ahora = datetime.now()
    idx_ahora = next((i for i, h in enumerate(horas) if datetime.fromisoformat(h) >= ahora), len(horas))
    ini = max(0, idx_ahora - 24)
    precip_24h = sum(v for v in hourly.get("precipitation", [])[ini:idx_ahora] if v is not None)
    nieve_24h = sum(v for v in hourly.get("snowfall", [])[ini:idx_ahora] if v is not None)

    # Tormenta eléctrica: códigos WMO 95/96/99, detectada AHORA o en las
    # próximas HORAS_VENTANA_TORMENTA horas (mismo criterio que app.html).
    codigos_futuros = hourly.get("weather_code", [])[idx_ahora:idx_ahora + HORAS_VENTANA_TORMENTA]
    tormenta_proxima = (
        current.get("weather_code") in CODIGOS_TORMENTA
        or any(c in CODIGOS_TORMENTA for c in codigos_futuros)
    )

    # Franja de próximas 6 horas (solo informativo, para mostrar en la app/API).
    proximas_horas = []
    for i in range(idx_ahora, min(idx_ahora + 6, len(horas))):
        proximas_horas.append({
            "hora": datetime.fromisoformat(horas[i]).hour,
            "temp": hourly.get("temperature_2m", [None] * len(horas))[i],
            "codigo": hourly.get("weather_code", [None] * len(horas))[i],
        })

    return {
        "fuente": "open-meteo",
        "timestamp": current.get("time"),
        "temp_actual_c": current.get("temperature_2m"),
        "humedad": current.get("relative_humidity_2m"),
        "sensacion_c": current.get("apparent_temperature"),
        "direccion_viento": current.get("wind_direction_10m"),
        "codigo_actual": current.get("weather_code"),
        "tmin_dia_c": (daily.get("temperature_2m_min") or [None])[0],
        "tmax_dia_c": (daily.get("temperature_2m_max") or [None])[0],
        "proximas_horas": proximas_horas,
        "temp_min_prevista_c": _extremo_prevista(horas, hourly.get("temperature_2m", []), HORAS_VENTANA_HELADA, min),
        "viento_kmh": current.get("wind_speed_10m"),
        "rafagas_kmh": current.get("wind_gusts_10m"),
        "viento_max_prevista_kmh": _extremo_prevista(horas, hourly.get("wind_speed_10m", []), horas_viento, max),
        "rafagas_max_prevista_kmh": _extremo_prevista(horas, hourly.get("wind_gusts_10m", []), horas_viento, max),
        "tormenta_proxima": tormenta_proxima,
        "precipitacion_24h_mm": round(precip_24h, 1),
        "nieve_cm_24h": round(nieve_24h * 100, 1),  # open-meteo entrega cm ya, se deja explícito
    }


_PARAMS_OPEN_METEO_BASE = {
    "current": "temperature_2m,wind_speed_10m,wind_gusts_10m,snowfall,weather_code,relative_humidity_2m,apparent_temperature,wind_direction_10m",
    "hourly": "precipitation,snowfall,temperature_2m,wind_speed_10m,wind_gusts_10m,weather_code",
    "daily": "temperature_2m_min,temperature_2m_max",
    "timezone": "America/Santiago",
    "forecast_days": 2,
    "past_days": 1,
}


def fetch_datos_open_meteo(lat: float, lon: float, horas_viento: int = 12) -> dict:
    """
    Consulta condiciones actuales + acumulados recientes para UN punto.
    `horas_viento`: cuántas horas hacia adelante mirar para el PEOR viento/
    ráfaga previsto (normalmente, las horas que faltan hasta el próximo
    envío programado — ver HORAS_ENVIO en main.py).
    Devuelve un dict normalizado que usa el motor de reglas.

    Para MUCHOS puntos a la vez (ej. los 68 centros), usa
    `fetch_datos_open_meteo_batch()` en su lugar -- una sola llamada HTTP
    en vez de una por punto, evita agotar el límite de Open-Meteo
    (600 llamadas/minuto) mucho más rápido de lo necesario.
    """
    params = {"latitude": lat, "longitude": lon, **_PARAMS_OPEN_METEO_BASE}
    resp = requests.get(OPEN_METEO_URL, params=params, timeout=15)
    resp.raise_for_status()
    return _parsear_respuesta_open_meteo(resp.json(), horas_viento)


def fetch_datos_open_meteo_batch(puntos: list[tuple[float, float]], horas_viento: int = 12) -> list[dict | None]:
    """
    Igual que `fetch_datos_open_meteo`, pero para VARIOS puntos en UNA sola
    llamada HTTP (Open-Meteo soporta listas de lat/lon separadas por coma
    en un solo request). Devuelve una lista en el MISMO ORDEN que `puntos`
    (con `None` en la posición de cualquier punto que Open-Meteo no haya
    podido resolver).
    """
    if not puntos:
        return []
    params = {
        "latitude": ",".join(str(p[0]) for p in puntos),
        "longitude": ",".join(str(p[1]) for p in puntos),
        **_PARAMS_OPEN_METEO_BASE,
    }
    resp = _get_con_reintento(OPEN_METEO_URL, params, timeout=30)
    data = resp.json()
    if not isinstance(data, list):
        data = [data]  # Open-Meteo devuelve un dict pelado si solo hay 1 punto
    return [
        (_parsear_respuesta_open_meteo(d, horas_viento) if d else None)
        for d in data
    ]


# ======================================================================
# 1b. YR.NO / MET NORWAY — segundo modelo de pronóstico (funcional)
# ----------------------------------------------------------------------
# API JSON gratuita, cobertura global, SIN API key. Requisitos de uso:
#  - User-Agent propio identificando tu app + un contacto (obligatorio).
#  - Respetar el header Expires: no re-consultar antes de que el dato
#    expire (este código lo maneja con un caché simple en memoria).
#  - Máximo 4 decimales en lat/lon.
#  - Atribución: los datos son CC BY 4.0 / NLOD -> hay que citar a
#    "MET Norway / Yr" en la app.
# Nota: a diferencia de Open-Meteo (que trae pasado+presente), yr.no
# entrega SOLO pronóstico, así que aquí "precipitacion_24h_mm" es la
# lluvia esperada en las PRÓXIMAS 24h (útil para alertar con antelación).
# ======================================================================
YR_URL = "https://api.met.no/weatherapi/locationforecast/2.0/complete"

# CAMBIA ESTO por el nombre real de tu app y un contacto válido (mail o web).
# MET Norway bloquea User-Agents genéricos o vacíos.
YR_USER_AGENT = "AlertasMeteoSur/1.0 contacto@tu-dominio.cl"

_yr_cache: dict = {}  # cache simple {(lat,lon): (expires_epoch, resultado)}


def fetch_datos_yr(lat: float, lon: float, horas_viento: int = 12) -> dict | None:
    import time
    from email.utils import parsedate_to_datetime

    lat_r, lon_r = round(lat, 4), round(lon, 4)
    clave = (lat_r, lon_r)

    # Respeta el caché: no vuelve a pegarle a la API si el dato sigue vigente.
    if clave in _yr_cache:
        expires_epoch, resultado = _yr_cache[clave]
        if time.time() < expires_epoch:
            return resultado

    headers = {"User-Agent": YR_USER_AGENT}
    params = {"lat": lat_r, "lon": lon_r}
    resp = requests.get(YR_URL, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    serie = data.get("properties", {}).get("timeseries", [])
    if not serie:
        return None

    ahora = serie[0]
    instant = ahora.get("data", {}).get("instant", {}).get("details", {})

    # Viento en m/s -> km/h
    viento_ms = instant.get("wind_speed")
    rafaga_ms = instant.get("wind_speed_of_gust")
    viento_kmh = round(viento_ms * 3.6, 1) if viento_ms is not None else None
    rafaga_kmh = round(rafaga_ms * 3.6, 1) if rafaga_ms is not None else None

    # Precipitación esperada próximas 24h: suma los bloques next_1_hours.
    precip_24h = 0.0
    for punto in serie[:24]:
        det = punto.get("data", {}).get("next_1_hours", {}).get("details", {})
        val = det.get("precipitation_amount")
        if val is not None:
            precip_24h += val

    # Mínima prevista en las próximas 12h (yr.no ya solo entrega futuro,
    # así que no hace falta filtrar horas pasadas como con Open-Meteo).
    temps_futuras = [
        p.get("data", {}).get("instant", {}).get("details", {}).get("air_temperature")
        for p in serie[:12]
    ]
    temps_futuras = [t for t in temps_futuras if t is not None]
    temp_min_prevista = min(temps_futuras) if temps_futuras else None

    # Peor viento/ráfaga pronosticado en las próximas `horas_viento` (yr.no
    # ya solo entrega futuro, así que serie[:horas_viento] son horas por venir).
    vientos_futuros = []
    rafagas_futuras = []
    for p in serie[:horas_viento]:
        det = p.get("data", {}).get("instant", {}).get("details", {})
        if det.get("wind_speed") is not None:
            vientos_futuros.append(det["wind_speed"] * 3.6)
        if det.get("wind_speed_of_gust") is not None:
            rafagas_futuras.append(det["wind_speed_of_gust"] * 3.6)
    viento_max_prevista = round(max(vientos_futuros), 1) if vientos_futuros else None
    rafaga_max_prevista = round(max(rafagas_futuras), 1) if rafagas_futuras else None

    resultado = {
        "fuente": "yr.no",
        "timestamp": ahora.get("time"),
        "temp_actual_c": instant.get("air_temperature"),
        "temp_min_prevista_c": temp_min_prevista,
        "viento_kmh": viento_kmh,
        "rafagas_kmh": rafaga_kmh,
        "viento_max_prevista_kmh": viento_max_prevista,
        "rafagas_max_prevista_kmh": rafaga_max_prevista,
        "precipitacion_24h_mm": round(precip_24h, 1),
        "nieve_cm_24h": None,  # yr.no no separa nieve en el compact/complete estándar
    }

    # Guarda en caché hasta el Expires que indique la API (o 30 min por defecto).
    expires_header = resp.headers.get("Expires")
    try:
        expires_epoch = parsedate_to_datetime(expires_header).timestamp()
    except Exception:
        expires_epoch = time.time() + 1800
    _yr_cache[clave] = (expires_epoch, resultado)

    return resultado


# ======================================================================
# 1c. OPEN-METEO — modelo explícito adicional (DWD ICON, funcional)
# ----------------------------------------------------------------------
# Open-Meteo agrega más de 15 servicios meteorológicos nacionales (ECMWF,
# DWD, NOAA, Météo-France, JMA, KNMI, UK Met Office, etc.) y por defecto
# usa una mezcla automática ("best_match"). Acá pedimos explícitamente el
# modelo ICON del servicio meteorológico alemán (DWD) — un linaje de
# modelo distinto tanto de esa mezcla como de yr.no (MET Norway), para
# sumar una tercera fuente genuinamente independiente al consenso.
# Mismo endpoint, mismo formato de respuesta, solo cambia &models=.
# ======================================================================
def _parsear_respuesta_icon(data: dict) -> dict:
    """Parsea la respuesta de UNA ubicación del modelo DWD ICON."""
    current = data.get("current", {})
    hourly = data.get("hourly", {})
    horas = hourly.get("time", [])

    ahora = datetime.now()
    idx_ahora = next((i for i, h in enumerate(horas) if datetime.fromisoformat(h) >= ahora), len(horas))
    ini = max(0, idx_ahora - 24)
    precip_24h = sum(v for v in hourly.get("precipitation", [])[ini:idx_ahora] if v is not None)
    nieve_24h = sum(v for v in hourly.get("snowfall", [])[ini:idx_ahora] if v is not None)

    return {
        "fuente": "dwd-icon",
        "timestamp": current.get("time"),
        "temp_actual_c": current.get("temperature_2m"),
        "temp_min_prevista_c": None,  # se completa por punto en fetch_datos_open_meteo_icon_batch
        "viento_kmh": current.get("wind_speed_10m"),
        "rafagas_kmh": current.get("wind_gusts_10m"),
        "viento_max_prevista_kmh": None,
        "rafagas_max_prevista_kmh": None,
        "precipitacion_24h_mm": round(precip_24h, 1),
        "nieve_cm_24h": round(nieve_24h * 100, 1),
        "_horas": horas,  # se usa internamente para calcular los "_max_prevista" con horas_viento
        "_hourly": hourly,
    }


def _completar_previstas_icon(parcial: dict, horas_viento: int) -> dict:
    """Calcula los campos '_max_prevista'/'_min_prevista' (que dependen de
    `horas_viento`, variable según cuándo es el próximo envío) y limpia los
    campos internos usados solo para ese cálculo."""
    horas = parcial.pop("_horas")
    hourly = parcial.pop("_hourly")
    parcial["temp_min_prevista_c"] = _extremo_prevista(horas, hourly.get("temperature_2m", []), HORAS_VENTANA_HELADA, min)
    parcial["viento_max_prevista_kmh"] = _extremo_prevista(horas, hourly.get("wind_speed_10m", []), horas_viento, max)
    parcial["rafagas_max_prevista_kmh"] = _extremo_prevista(horas, hourly.get("wind_gusts_10m", []), horas_viento, max)
    return parcial


_PARAMS_OPEN_METEO_ICON = {
    "current": "temperature_2m,wind_speed_10m,wind_gusts_10m,snowfall",
    "hourly": "precipitation,snowfall,temperature_2m,wind_speed_10m,wind_gusts_10m",
    "timezone": "America/Santiago",
    "forecast_days": 2,
    "past_days": 1,
    "models": "icon_seamless",
}


def fetch_datos_open_meteo_icon(lat: float, lon: float, horas_viento: int = 12) -> dict:
    """Consulta el modelo DWD ICON para UN punto. Para varios puntos a la
    vez, usa `fetch_datos_open_meteo_icon_batch()` (1 sola llamada HTTP)."""
    params = {"latitude": lat, "longitude": lon, **_PARAMS_OPEN_METEO_ICON}
    resp = requests.get(OPEN_METEO_URL, params=params, timeout=15)
    resp.raise_for_status()
    parcial = _parsear_respuesta_icon(resp.json())
    return _completar_previstas_icon(parcial, horas_viento)


def fetch_datos_open_meteo_icon_batch(puntos: list[tuple[float, float]], horas_viento: int = 12) -> list[dict | None]:
    """Igual que `fetch_datos_open_meteo_icon`, pero para VARIOS puntos en
    UNA sola llamada HTTP -- ver `fetch_datos_open_meteo_batch` para el
    motivo (evitar agotar el límite de 600 llamadas/minuto de Open-Meteo)."""
    if not puntos:
        return []
    params = {
        "latitude": ",".join(str(p[0]) for p in puntos),
        "longitude": ",".join(str(p[1]) for p in puntos),
        **_PARAMS_OPEN_METEO_ICON,
    }
    resp = _get_con_reintento(OPEN_METEO_URL, params, timeout=30)
    data = resp.json()
    if not isinstance(data, list):
        data = [data]
    return [
        (_completar_previstas_icon(_parsear_respuesta_icon(d), horas_viento) if d else None)
        for d in data
    ]


# ======================================================================
# 1d. OPEN-METEO MARINE — altura de olas / oleaje (funcional)
# ----------------------------------------------------------------------
# API gratuita y separada (marine-api.open-meteo.com), sin llave, usando
# modelos de oleaje del servicio meteorológico alemán (DWD), actualizados
# 2 veces al día. Relevante para centros de cultivo: mide algo que ni
# Open-Meteo estándar ni yr.no reportan (altura de ola/oleaje), no solo
# "más confianza" en el viento/lluvia que ya se mide.
# ======================================================================
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"


def _parsear_respuesta_marino(data: dict) -> dict:
    hourly = data.get("hourly", {})
    daily = data.get("daily", {})
    alturas = hourly.get("wave_height", [])
    ola_actual = next((h for h in alturas if h is not None), None)
    ola_max = (daily.get("wave_height_max") or [None])[0]
    return {
        "altura_ola_actual_m": ola_actual,
        "altura_ola_max_m": ola_max,
    }


_PARAMS_MARINO = {
    "hourly": "wave_height",
    "daily": "wave_height_max",
    "timezone": "America/Santiago",
    "forecast_days": 1,
}


def fetch_datos_marino(lat: float, lon: float) -> dict:
    """Consulta la altura de oleaje para UN punto. Para varios puntos a la
    vez, usa `fetch_datos_marino_batch()` (1 sola llamada HTTP)."""
    params = {"latitude": lat, "longitude": lon, **_PARAMS_MARINO}
    resp = requests.get(MARINE_URL, params=params, timeout=15)
    resp.raise_for_status()
    return _parsear_respuesta_marino(resp.json())


def fetch_datos_marino_batch(puntos: list[tuple[float, float]]) -> list[dict]:
    """Igual que `fetch_datos_marino`, pero para VARIOS puntos en UNA sola
    llamada HTTP."""
    if not puntos:
        return []
    params = {
        "latitude": ",".join(str(p[0]) for p in puntos),
        "longitude": ",".join(str(p[1]) for p in puntos),
        **_PARAMS_MARINO,
    }
    resp = _get_con_reintento(MARINE_URL, params, timeout=30)
    data = resp.json()
    if not isinstance(data, list):
        data = [data]
    return [
        (_parsear_respuesta_marino(d) if d else {"altura_ola_actual_m": None, "altura_ola_max_m": None})
        for d in data
    ]


def _combinar_fuentes(fuentes_datos: list[dict]) -> dict:
    """
    Combina los dicts normalizados de varias fuentes (open-meteo, yr.no,
    dwd-icon) en un solo resultado: el valor MÁS ALTO (peor caso) para
    viento/ráfaga/lluvia/nieve/tormenta, y los campos de solo visualización
    (humedad, sensación térmica, mín/máx del día, pronóstico por horas,
    dirección del viento) tomados únicamente de open-meteo (son
    informativos, no entran en el cálculo de ninguna alerta).

    Reutilizada tanto por `fetch_datos_consenso` (1 punto, llamadas
    individuales) como por el refresco en lote de `api.py` (68 puntos,
    2 llamadas HTTP para todos a la vez) -- la combinación es la misma
    en ambos casos, solo cambia CÓMO se obtuvieron los datos de cada fuente.
    """
    if not fuentes_datos:
        raise RuntimeError("Ninguna fuente de pronóstico respondió")

    def combinar(campo):
        valores = [d[campo] for d in fuentes_datos if d.get(campo) is not None]
        return max(valores) if valores else None

    resultado = {
        "fuente": "+".join(d["fuente"] for d in fuentes_datos),
        "temp_actual_c": min(  # para temp mínima interesa el valor más bajo
            [d["temp_actual_c"] for d in fuentes_datos if d.get("temp_actual_c") is not None],
            default=None,
        ),
        "temp_min_prevista_c": min(  # peor caso: la mínima pronosticada más baja entre fuentes
            [d["temp_min_prevista_c"] for d in fuentes_datos if d.get("temp_min_prevista_c") is not None],
            default=None,
        ),
        "viento_kmh": combinar("viento_kmh"),
        "rafagas_kmh": combinar("rafagas_kmh"),
        "viento_max_prevista_kmh": combinar("viento_max_prevista_kmh"),
        "rafagas_max_prevista_kmh": combinar("rafagas_max_prevista_kmh"),
        "precipitacion_24h_mm": combinar("precipitacion_24h_mm"),
        "nieve_cm_24h": combinar("nieve_cm_24h"),
        "tormenta_proxima": combinar("tormenta_proxima"),  # True si cualquier fuente la detecta
    }

    om = next((d for d in fuentes_datos if d.get("fuente") == "open-meteo"), None)
    resultado["humedad"] = om.get("humedad") if om else None
    resultado["sensacion_c"] = om.get("sensacion_c") if om else None
    resultado["direccion_viento"] = om.get("direccion_viento") if om else None
    resultado["codigo_actual"] = om.get("codigo_actual") if om else None
    resultado["tmin_dia_c"] = om.get("tmin_dia_c") if om else None
    resultado["tmax_dia_c"] = om.get("tmax_dia_c") if om else None
    resultado["proximas_horas"] = om.get("proximas_horas") if om else []

    return resultado


def fetch_datos_consenso(lat: float, lon: float, horas_viento: int = 12) -> dict:
    """
    `horas_viento`: ventana (en horas hacia adelante) para el PEOR viento y
    ráfaga previstos. Por defecto 12h; main.py pasa la cantidad real de
    horas hasta el próximo envío programado (ver HORAS_ENVIO).

    Para MUCHOS puntos a la vez (ej. los 68 centros en api.py), NO uses
    esta función en un loop -- usa las versiones _batch de cada fuente en
    su lugar (2-3 llamadas HTTP en total en vez de 68 x 3). Ver api.py.
    """
    fuentes_datos = []
    for fn in (fetch_datos_open_meteo, fetch_datos_yr, fetch_datos_open_meteo_icon):
        try:
            d = fn(lat, lon, horas_viento)
            if d:
                fuentes_datos.append(d)
        except Exception as e:
            # Antes esto se tragaba en silencio -- sin ningún rastro, era
            # imposible saber por qué faltaban campos como humedad/sensación
            # térmica/mín/máx (que dependen SOLO de fetch_datos_open_meteo).
            # Render captura print() en sus logs, así que esto sí queda visible.
            print(f"[fetch_datos_consenso] {fn.__name__} falló para ({lat},{lon}): {e}")

    resultado = _combinar_fuentes(fuentes_datos)

    # Datos marinos: fuente separada, se agregan aparte (no hay "peor caso"
    # entre modelos acá todavía, solo Open-Meteo Marine).
    try:
        resultado.update(fetch_datos_marino(lat, lon))
    except Exception:
        resultado["altura_ola_actual_m"] = None
        resultado["altura_ola_max_m"] = None

    return resultado


# ======================================================================
# 2. DMC — placeholder a completar
# ======================================================================
def fetch_datos_dmc(codigo_estacion: str) -> dict | None:
    """
    TODO: reemplazar por la llamada real a la fuente de datos de la DMC
    una vez definido el método de acceso (Plataforma de Datos con
    credenciales, o scraping autorizado del portal de climatología).

    Por ahora retorna None para indicar "sin dato" y que el motor de
    reglas dependa de Open-Meteo mientras tanto.
    """
    return None


# ======================================================================
# 3. SENAPRED — alertas oficiales
# ----------------------------------------------------------------------
# SENAPRED NO tiene una API pública documentada. Investigación de fuentes
# (jul-2026) identificó TRES rutas viables, de más robusta a más frágil.
# Elige una, complétala, y descarta las otras dos (o déjalas de respaldo).
# ======================================================================

# --- SENAPRED, capas oficiales de alertas meteorológicas ---------------
# Encontradas inspeccionando el Web Map "WM_METEOROLOGICAS" que alimenta
# el dashboard oficial "ALERTAS SENAPRED VIGENTES" (MINSAL/SENAPRED).
# Son TRES capas separadas, una por color de alerta, cada una ya
# filtrada por SENAPRED en origen (TIPO_ALERT):
SENAPRED_CAPAS = {
    "verde":    "https://services3.arcgis.com/CNzkI2T3GmfwkaAR/arcgis/rest/services/METEOROLOGICAS_VERDE/FeatureServer/0",
    "amarilla": "https://services3.arcgis.com/CNzkI2T3GmfwkaAR/arcgis/rest/services/METEOROLOGICAS_AMARILLA/FeatureServer/0",
    "roja":     "https://services3.arcgis.com/CNzkI2T3GmfwkaAR/arcgis/rest/services/METEOROLOGICAS_ROJA/FeatureServer/0",
}

# Campos reales confirmados en la capa (fieldInfos del Web Map oficial):
#   REGION, PROVINCIA, COMUNA, TIPO_ALERT, CAUSALIDAD, FECHA_INI,
#   CUT_REG/CUT_PROV/CUT_COM (códigos), SUPERFICIE.


def _consultar_capa(color: str, url: str) -> list[dict]:
    params = {
        "where": "1=1",
        "outFields": "REGION,PROVINCIA,COMUNA,TIPO_ALERT,CAUSALIDAD,FECHA_INI",
        "returnGeometry": "false",
        "f": "json",
    }
    resp = requests.get(f"{url}/query", params=params, timeout=20)
    resp.raise_for_status()
    features = resp.json().get("features", [])
    alertas = []
    for feat in features:
        a = feat.get("attributes", {})
        alertas.append({
            "region": a.get("REGION"),
            "provincia": a.get("PROVINCIA"),
            "comuna": a.get("COMUNA"),
            "tipo": a.get("TIPO_ALERT"),
            "causa": a.get("CAUSALIDAD"),
            "color": color,
            "titulo": f"Alerta {color} por {a.get('CAUSALIDAD','evento meteorológico')} en {a.get('COMUNA') or a.get('REGION')}",
        })
    return alertas


def fetch_todas_alertas_senapred() -> list[dict]:
    """Consulta las tres capas (verde/amarilla/roja) y junta los resultados."""
    todas = []
    for color, url in SENAPRED_CAPAS.items():
        try:
            todas += _consultar_capa(color, url)
        except Exception:
            pass  # si una capa falla, se sigue con las demás
    return todas


def fetch_alertas_senapred(region: str) -> list[dict]:
    """
    Punto de entrada que usa el orquestador: alertas activas para una
    región específica (case-insensitive, tolera acentos distintos).
    """
    def normaliza(s):
        import unicodedata
        return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()

    objetivo = normaliza(region)
    resultado = []
    for a in fetch_todas_alertas_senapred():
        if normaliza(a.get("region")) == objetivo:
            resultado.append(a)
    return resultado
