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
from datetime import datetime, timedelta, timezone


# ======================================================================
# 1. OPEN-METEO — datos crudos por coordenada (funcional)
# ======================================================================
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def _min_prevista(horas_iso: list[str], temperaturas: list) -> float | None:
    """
    Mínima pronosticada SOLO entre las horas que faltan (desde ahora hacia
    adelante, hasta 12h), nunca incluyendo horas ya pasadas. Así, cuando el
    frío del día ya ocurrió y no se pronostica que continúe, el valor deja
    de bajar del umbral y la alerta de helada se cae sola en el próximo
    ciclo — no queda "pegada" todo el día por un mínimo que ya pasó.
    """
    if not horas_iso or not temperaturas:
        return None
    ahora = datetime.now()
    idx = next((i for i, h in enumerate(horas_iso) if datetime.fromisoformat(h) >= ahora), None)
    if idx is None:
        return None
    ventana = [t for t in temperaturas[idx:idx + 12] if t is not None]
    return min(ventana) if ventana else None


def fetch_datos_open_meteo(lat: float, lon: float) -> dict:
    """
    Consulta condiciones actuales + acumulados recientes para un punto.
    Devuelve un dict normalizado que usa el motor de reglas.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,wind_speed_10m,wind_gusts_10m,snowfall",
        "hourly": "precipitation,snowfall,temperature_2m",
        "timezone": "America/Santiago",
        "forecast_days": 2,
        "past_days": 1,
    }
    resp = requests.get(OPEN_METEO_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    current = data.get("current", {})
    hourly = data.get("hourly", {})
    horas = hourly.get("time", [])

    # Últimas 24h reales (desde "ahora" hacia atrás) — ya no es simplemente
    # "los últimos 24 del arreglo", porque con forecast_days=2 el arreglo
    # incluye también el día de mañana.
    ahora = datetime.now()
    idx_ahora = next((i for i, h in enumerate(horas) if datetime.fromisoformat(h) >= ahora), len(horas))
    ini = max(0, idx_ahora - 24)
    precip_24h = sum(v for v in hourly.get("precipitation", [])[ini:idx_ahora] if v is not None)
    nieve_24h = sum(v for v in hourly.get("snowfall", [])[ini:idx_ahora] if v is not None)

    return {
        "fuente": "open-meteo",
        "timestamp": current.get("time"),
        "temp_actual_c": current.get("temperature_2m"),
        "temp_min_prevista_c": _min_prevista(horas, hourly.get("temperature_2m", [])),
        "viento_kmh": current.get("wind_speed_10m"),
        "rafagas_kmh": current.get("wind_gusts_10m"),
        "precipitacion_24h_mm": round(precip_24h, 1),
        "nieve_cm_24h": round(nieve_24h * 100, 1),  # open-meteo entrega cm ya, se deja explícito
    }


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


def fetch_datos_yr(lat: float, lon: float) -> dict | None:
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

    resultado = {
        "fuente": "yr.no",
        "timestamp": ahora.get("time"),
        "temp_actual_c": instant.get("air_temperature"),
        "temp_min_prevista_c": temp_min_prevista,
        "viento_kmh": viento_kmh,
        "rafagas_kmh": rafaga_kmh,
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
def fetch_datos_open_meteo_icon(lat: float, lon: float) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,wind_speed_10m,wind_gusts_10m,snowfall",
        "hourly": "precipitation,snowfall,temperature_2m",
        "timezone": "America/Santiago",
        "forecast_days": 2,
        "past_days": 1,
        "models": "icon_seamless",
    }
    resp = requests.get(OPEN_METEO_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

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
        "temp_min_prevista_c": _min_prevista(horas, hourly.get("temperature_2m", [])),
        "viento_kmh": current.get("wind_speed_10m"),
        "rafagas_kmh": current.get("wind_gusts_10m"),
        "precipitacion_24h_mm": round(precip_24h, 1),
        "nieve_cm_24h": round(nieve_24h * 100, 1),
    }


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


def fetch_datos_marino(lat: float, lon: float) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "wave_height",
        "daily": "wave_height_max",
        "timezone": "America/Santiago",
        "forecast_days": 1,
    }
    resp = requests.get(MARINE_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    hourly = data.get("hourly", {})
    daily = data.get("daily", {})

    alturas = hourly.get("wave_height", [])
    ola_actual = next((h for h in alturas if h is not None), None)
    ola_max = (daily.get("wave_height_max") or [None])[0]

    return {
        "altura_ola_actual_m": ola_actual,
        "altura_ola_max_m": ola_max,
    }


# ======================================================================
# 1e. CONSENSO MULTI-FUENTE — combina Open-Meteo + yr.no + DWD ICON + Marine
# ----------------------------------------------------------------------
# Toma el valor MÁS ALTO (peor caso) de cada variable entre las fuentes
# disponibles, para no perder una alerta si un modelo la subestima.
# Si prefieres promediar en vez de tomar el máximo, cambia max() por
# una media en combinar().
# ======================================================================
def fetch_datos_consenso(lat: float, lon: float) -> dict:
    fuentes_datos = []
    for fn in (fetch_datos_open_meteo, fetch_datos_yr, fetch_datos_open_meteo_icon):
        try:
            d = fn(lat, lon)
            if d:
                fuentes_datos.append(d)
        except Exception:
            pass

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
        "precipitacion_24h_mm": combinar("precipitacion_24h_mm"),
        "nieve_cm_24h": combinar("nieve_cm_24h"),
    }

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
