"""
Configuración de zonas geográficas y umbrales para el sistema de alertas
meteorológicas del sur de Chile (Los Lagos -> Magallanes).

- Las ALERTAS OFICIALES (SENAPRED) se manejan a nivel de REGIÓN y COMUNA.
- Los UMBRALES PROPIOS (datos crudos) se manejan a nivel de PUNTO ESPECÍFICO,
  y cada punto está vinculado a la comuna que le corresponde (ver el campo
  comuna_de_referencia en PUNTOS_ESPECIFICOS y COMUNAS_POR_REGION más abajo).

Puedes editar libremente las comunas, coordenadas y umbrales de este archivo
sin tocar el resto del código.
"""

# -----------------------------------------------------------------------
# Regiones a cubrir (para matchear contra las alertas oficiales de SENAPRED)
# -----------------------------------------------------------------------
REGIONES = [
    "Los Ríos",
    "Los Lagos",
    "Aysén",
    "Magallanes",
]

# -----------------------------------------------------------------------
# Comunas cubiertas, agrupadas por región. Sirve para saber qué comunas
# pertenecen a cada región (ej. al mostrar o filtrar alertas oficiales de
# SENAPRED, que vienen con campo COMUNA), y como referencia de qué valores
# son válidos para el campo comuna_de_referencia en PUNTOS_ESPECIFICOS.
# -----------------------------------------------------------------------
COMUNAS_POR_REGION = {
    "Los Lagos": [
        "Puerto Montt", "Calbuco", "Quemchi", "Castro",
        "Achao", "Chonchi", "Quellón", "Hornopirén",
        "San Pablo", "Purranque", "Fresia", "Osorno",
    ],
    "Aysén": [
        "Puerto Cisnes", "Puerto Aguirre", "Puerto Chacabuco",
    ],
    "Magallanes": [
        "Río Verde",
    ],
    "Los Ríos": [
        "Panguipulli",
    ],
}

# -----------------------------------------------------------------------
# Umbrales por defecto para generar alertas propias a partir de datos
# crudos (independientes de si SENAPRED emitió o no una alerta oficial).
# Ajusta estos valores según qué tan sensible quieres que sea el sistema.
# -----------------------------------------------------------------------
UMBRALES_DEFAULT = {
    "viento_kmh": 40,          # viento sostenido
    "rafagas_kmh": 60,         # ráfagas de viento (picos puntuales, siempre >= viento sostenido)
    "precipitacion_24h_mm": 50,  # agua caída en 24 horas
    "temp_min_c": -1,          # helada: punto de entrada (amarilla) para Los Lagos y Aysén
    "altura_ola_m": 2.0,       # oleaje (metros) — relevante para operaciones en centros de cultivo
}

# Puedes sobrescribir umbrales para comunas específicas (ej. zonas más
# expuestas a nieve o viento) agregando entradas aquí. También sirve como
# override por nombre para puntos específicos (ver obtener_umbrales_punto).
UMBRALES_POR_COMUNA = {}

# Override de umbrales POR REGIÓN completa. Se aplica a todos los puntos
# de esa región, antes del override individual por nombre (que sigue
# pudiendo pisar esto para un punto puntual si hace falta). Los Lagos y
# Aysén no necesitan entrada acá porque ya usan el default (-1°C).
UMBRALES_POR_REGION = {
    "Magallanes": {"temp_min_c": -3},  # heladas más severas y frecuentes en el extremo sur
}

# -----------------------------------------------------------------------
# ESCALA DE SEVERIDAD (4 colores: verde/amarilla/naranja/roja)
# -----------------------------------------------------------------------
# Para viento, ráfagas, lluvia y oleaje, la severidad es PROPORCIONAL al
# umbral base de arriba: 0-20% sobre el umbral = amarilla, 20-40% = naranja,
# 40%+ = roja. Estos porcentajes son configurables acá.
ESCALA_PROPORCIONAL = {
    "naranja_desde": 1.20,  # 20% sobre el umbral
    "roja_desde": 1.40,     # 40% sobre el umbral
}

# La helada NO es proporcional (las temperaturas negativas hacen que un
# porcentaje no tenga un sentido intuitivo) — usa quiebres fijos en °C,
# distintos por región. UMBRALES_DEFAULT/POR_REGION arriba definen el punto
# de entrada (amarilla); esto define dónde empiezan naranja y roja.
HELADA_ESCALA = {
    "Los Lagos":  {"naranja": -1.5, "roja": -3.0},
    "Aysén":      {"naranja": -1.5, "roja": -3.0},
    "Magallanes": {"naranja": -4.0, "roja": -6.0},
}

# -----------------------------------------------------------------------
# CAUDAL DE RÍO (solo para instalaciones en TIERRA, categoria="tierra")
# -----------------------------------------------------------------------
# A diferencia de viento/ráfaga/lluvia/oleaje, NO es proporcional a un
# único umbral base -- cada río es tan distinto (uno chico puede fluir
# normalmente a 2 m³/s, uno grande a 200 m³/s) que un solo "% sobre lo
# normal" no tiene sentido de forma automática. En su lugar, cada
# instalación en tierra tiene 3 valores FIJOS propios en m³/s (mismo
# estilo que HELADA_ESCALA): desde dónde empieza cada color. Sin entrada
# acá, ese punto no genera ninguna alerta de caudal (aunque sí las demás:
# viento, ráfaga, lluvia, helada, tormenta).
UMBRALES_CAUDAL_POR_PUNTO = {
    # "Nombre del punto": {"amarilla": <m³/s>, "naranja": <m³/s>, "roja": <m³/s>},
}


def obtener_umbral_caudal(nombre_punto: str) -> dict | None:
    """None si el punto no tiene umbrales de caudal definidos (ej. es un
    punto marino, o es de tierra pero todavía no se configuraron)."""
    return UMBRALES_CAUDAL_POR_PUNTO.get(nombre_punto)


def obtener_umbrales(comuna: str) -> dict:
    """Devuelve los umbrales aplicables a una comuna, aplicando overrides."""
    umbrales = UMBRALES_DEFAULT.copy()
    umbrales.update(UMBRALES_POR_COMUNA.get(comuna, {}))
    return umbrales


# -----------------------------------------------------------------------
# PUNTOS ESPECÍFICOS — coordenadas exactas más allá del centro de la
# comuna: un centro de cultivo, una caleta, un faro, una cabaña, un
# fundo, etc. Cada uno hereda los umbrales de su comuna (para overrides
# propios, agrégalos igual que en UMBRALES_POR_COMUNA usando el NOMBRE
# DEL PUNTO como llave).
#
# Formato: (nombre, lat, lon, comuna_de_referencia, region)
# -----------------------------------------------------------------------
PUNTOS_ESPECIFICOS = [
    ("Abtao", -41.79944, -73.36083, "Calbuco", "Los Lagos", "mar", "centro"),
    ("Acopio Chinquihue", -41.51607, -73.02722, "Puerto Montt", "Los Lagos", "mar", "centro"),
    ("Acopio Quemchi", -42.1475, -73.48111, "Quemchi", "Los Lagos", "mar", "centro"),
    ("Aguantao", -42.52, -73.58583, "Castro", "Los Lagos", "mar", "centro"),
    ("Aldunate", -44.3275, -72.89833, "Puerto Cisnes", "Aysén", "mar", "centro"),
    ("Aulen", -41.85068, -72.81142, "Hornopirén", "Los Lagos", "mar", "centro"),
    ("Bertrand", -52.81687, -72.43779, "Río Verde", "Magallanes", "mar", "centro"),
    ("Buill", -42.43716, -72.70246, "Hornopirén", "Los Lagos", "mar", "centro"),
    ("Cachihue", -42.30047, -73.0669, "Quemchi", "Los Lagos", "mar", "centro"),
    ("Calen 1", -42.33139, -73.44444, "Quemchi", "Los Lagos", "mar", "centro"),
    ("Calen 2", -42.3475, -73.47889, "Quemchi", "Los Lagos", "mar", "centro"),
    ("Caleta Soledad", -42.36444, -72.48806, "Hornopirén", "Los Lagos", "mar", "centro"),
    ("Canal Contreras", -52.775, -72.59722, "Río Verde", "Magallanes", "mar", "centro"),
    ("Caniglia 2", -45.41667, -74.07056, "Puerto Chacabuco", "Aysén", "mar", "centro"),
    ("Caucahue", -42.11222, -73.425, "Quemchi", "Los Lagos", "mar", "centro"),
    ("Chauco", -42.925, -73.59333, "Quellón", "Los Lagos", "mar", "centro"),
    ("Chaullin Norte", -43.03417, -73.44694, "Quellón", "Los Lagos", "mar", "centro"),
    ("Chaullin Sur", -43.0975, -73.41639, "Quellón", "Los Lagos", "mar", "centro"),
    ("Chaullin Weste", -43.05667, -73.46028, "Quellón", "Los Lagos", "mar", "centro"),
    ("Chidhuapi 1", -41.81556, -73.11667, "Calbuco", "Los Lagos", "mar", "centro"),
    ("Chidhuapi 2", -41.85389, -73.08417, "Calbuco", "Los Lagos", "mar", "centro"),
    ("Chidhuapi 3", -41.85278, -73.05306, "Calbuco", "Los Lagos", "mar", "centro"),
    ("Chidhuapi 4", -41.83111, -73.11306, "Calbuco", "Los Lagos", "mar", "centro"),
    ("Chope", -41.79667, -73.10806, "Calbuco", "Los Lagos", "mar", "centro"),
    ("Churrecue", -45.35667, -73.54111, "Puerto Chacabuco", "Aysén", "mar", "centro"),
    ("Colaco 4", -41.77333, -73.35194, "Calbuco", "Los Lagos", "mar", "centro"),
    ("Darsena", -52.58833, -72.36444, "Río Verde", "Magallanes", "mar", "centro"),
    ("Darsena Norte", -52.58842, -72.36446, "Río Verde", "Magallanes", "mar", "centro"),
    ("Desembocadura", -52.73809, -72.63738, "Río Verde", "Magallanes", "mar", "centro"),
    ("Ducañas", -42.25232, -73.17865, "Quemchi", "Los Lagos", "mar", "centro"),
    ("El Manzano", -42.02544, -72.65009, "Hornopirén", "Los Lagos", "mar", "centro"),
    ("Ensenada Rys", -52.56111, -72.34194, "Río Verde", "Magallanes", "mar", "centro"),
    ("Esperanza", -41.09765, -73.70959, "Fresia", "Los Lagos", "tierra", "piscicultura"),
    ("Estero", -52.85621, -72.57773, "Río Verde", "Magallanes", "mar", "centro"),
    ("Estero Conche", -44.42361, -72.78222, "Puerto Cisnes", "Aysén", "mar", "centro"),
    ("Furia", -52.62306, -72.41056, "Río Verde", "Magallanes", "mar", "centro"),
    ("Hueyusca", -40.92504, -73.56042, "Purranque", "Los Lagos", "tierra", "piscicultura"),
    ("Imelev", -42.61528, -73.41472, "Achao", "Los Lagos", "mar", "centro"),
    ("Isla García", -52.83816, -72.53191, "Río Verde", "Magallanes", "mar", "centro"),
    ("Isla Tac", -42.40143, -73.15192, "Quemchi", "Los Lagos", "mar", "centro"),
    ("Jacaff", -44.30444, -72.94333, "Puerto Cisnes", "Aysén", "mar", "centro"),
    ("Linguar", -42.05881, -72.62749, "Hornopirén", "Los Lagos", "mar", "centro"),
    ("Linlinao", -42.56917, -73.74806, "Chonchi", "Los Lagos", "mar", "centro"),
    ("Liquiñe", -39.69421, -71.88259, "Panguipulli", "Los Ríos", "tierra", "piscicultura"),
    ("Llancacheo", -41.74914, -73.03428, "Calbuco", "Los Lagos", "mar", "centro"),
    ("Luchin", -45.04851, -73.40206, "Puerto Aguirre", "Aysén", "mar", "centro"),
    ("Macetero", -44.44694, -72.81222, "Puerto Cisnes", "Aysén", "mar", "centro"),
    ("Malomacum", -42.04795, -72.62711, "Hornopirén", "Los Lagos", "mar", "centro"),
    ("Matilde", -45.48421, -74.20692, "Puerto Chacabuco", "Aysén", "mar", "centro"),
    ("Navarro", -52.89667, -72.7097, "Río Verde", "Magallanes", "mar", "centro"),
    ("PCC", -41.77938, -73.52419, "Calbuco", "Los Lagos", "mar", "piscicultura"),
    ("Pollollo", -41.80652, -73.00925, "Calbuco", "Los Lagos", "mar", "centro"),
    ("Punta Gruesa", -42.21001, -72.64493, "Hornopirén", "Los Lagos", "mar", "centro"),
    ("Punta Isla", -52.7713, -72.36162, "Río Verde", "Magallanes", "mar", "centro"),
    ("Punta Laura", -52.66806, -72.42111, "Río Verde", "Magallanes", "mar", "centro"),
    ("Punta Laura Norte", -52.64694, -72.40667, "Río Verde", "Magallanes", "mar", "centro"),
    ("Punta Victoria", -45.34944, -73.46667, "Puerto Chacabuco", "Aysén", "mar", "centro"),
    ("Punta Yoye", -42.86639, -73.67944, "Quellón", "Los Lagos", "mar", "centro"),
    ("Quilen", -42.9875, -73.54278, "Quellón", "Los Lagos", "mar", "centro"),
    ("Rahue", -40.76553, -72.93064, "Osorno", "Los Lagos", "tierra", "piscicultura"),
    ("Reñihue", -42.52386, -72.66882, "Hornopirén", "Los Lagos", "mar", "centro"),
    ("Santa Juana", -40.7587, -72.96925, "Osorno", "Los Lagos", "tierra", "piscicultura"),
    ("Sector 3", -42.46222, -73.25611, "Puerto Cisnes", "Aysén", "mar", "centro"),
    ("Sur Este", -41.77959, -73.01941, "Calbuco", "Los Lagos", "mar", "centro"),
    ("Teupa", -42.67972, -73.66972, "Chonchi", "Los Lagos", "mar", "centro"),
    ("Trafún", -40.46944, -72.90497, "San Pablo", "Los Lagos", "tierra", "piscicultura"),
    ("Tranqui I", -42.99806, -73.43583, "Quellón", "Los Lagos", "mar", "centro"),
    ("Tranqui II", -43.00278, -73.39444, "Quellón", "Los Lagos", "mar", "centro"),
    ("Transito", -44.8375, -73.60889, "Puerto Cisnes", "Aysén", "mar", "centro"),
    ("Tubildad", -42.12528, -73.46944, "Quemchi", "Los Lagos", "mar", "centro"),
    ("Unicornio", -52.62889, -72.3725, "Río Verde", "Magallanes", "mar", "centro"),
    ("Unicornio Sur", -52.63528, -72.39417, "Río Verde", "Magallanes", "mar", "centro"),
    ("Vilupulli", -42.60111, -73.77528, "Chonchi", "Los Lagos", "mar", "centro"),
    ("Voigue", -42.30072, -73.20496, "Quemchi", "Los Lagos", "mar", "centro"),
    ("Weste Isla Luz", -45.48806, -74.09333, "Puerto Chacabuco", "Aysén", "mar", "centro"),
    ("Yelcho", -43.21472, -73.58028, "Quellón", "Los Lagos", "mar", "centro"),
    ("Zañartu", -44.4125, -72.79306, "Puerto Cisnes", "Aysén", "mar", "centro"),
]


def obtener_umbrales_punto(nombre_punto: str) -> dict:
    """
    Umbrales para un punto específico (ej. centro de cultivo): NO hereda
    el umbral de ninguna comuna cercana, porque la comuna más cercana no
    es representativa de las condiciones marinas exactas en ese punto.

    Orden de aplicación (cada uno pisa al anterior si corresponde):
      1. UMBRALES_DEFAULT (global)
      2. UMBRALES_POR_REGION (según la región real del punto)
      3. UMBRALES_POR_COMUNA (override puntual por nombre exacto del punto)
    """
    umbrales = UMBRALES_DEFAULT.copy()

    region = next((p[4] for p in PUNTOS_ESPECIFICOS if p[0] == nombre_punto), None)
    if region:
        umbrales.update(UMBRALES_POR_REGION.get(region, {}))

    umbrales.update(UMBRALES_POR_COMUNA.get(nombre_punto, {}))
    return umbrales


def obtener_categoria_punto(nombre_punto: str) -> str:
    """"mar" o "tierra" -- decide si el punto usa oleaje o caudal de río."""
    return next((p[5] for p in PUNTOS_ESPECIFICOS if p[0] == nombre_punto), "mar")


def validar_comunas_de_puntos() -> list[str]:
    """
    Revisa que cada PUNTO ESPECÍFICO esté vinculado a una comuna que
    realmente exista en COMUNAS_POR_REGION (y en la región correcta).
    Devuelve una lista de problemas encontrados (vacía si todo está bien).
    Útil para correr tras editar el archivo, antes de desplegar.
    """
    problemas = []
    for nombre, lat, lon, comuna_ref, region, categoria, tipo in PUNTOS_ESPECIFICOS:
        comunas_de_la_region = COMUNAS_POR_REGION.get(region)
        if comunas_de_la_region is None:
            problemas.append(f"{nombre}: la región '{region}' no existe en COMUNAS_POR_REGION")
        elif comuna_ref not in comunas_de_la_region:
            problemas.append(
                f"{nombre}: la comuna '{comuna_ref}' no está listada en "
                f"COMUNAS_POR_REGION['{region}']"
            )
    return problemas
