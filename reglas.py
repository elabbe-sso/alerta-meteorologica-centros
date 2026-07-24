"""
Motor de reglas: decide si un dato crudo o una alerta oficial debe
transformarse en una notificación para el usuario final.

Escala de severidad (4 colores): verde / amarilla / naranja / roja.
- Viento, ráfagas, lluvia y oleaje: PROPORCIONAL al umbral base
  (ver ESCALA_PROPORCIONAL en config.py) — 0-20% sobre el umbral = amarilla,
  20-40% = naranja, 40%+ = roja.
- Helada: quiebres FIJOS en °C, distintos por región (ver HELADA_ESCALA en
  config.py) — no es proporcional, porque un "% bajo cero" no tiene un
  sentido intuitivo.
- Tormenta eléctrica: no tiene valor numérico, así que sola siempre es
  naranja.
- Combinación: 1 condición sola -> su propio color; 2 condiciones juntas ->
  mínimo naranja; 3+ juntas -> mínimo roja. El color final de un punto es
  el MÁS ALTO entre el piso por cantidad y el color individual más grave
  entre sus condiciones activas — nunca "baja" de nivel por combinarse con
  algo leve.
"""

from __future__ import annotations

from config import HELADA_ESCALA, ESCALA_PROPORCIONAL

PESO_COLOR = {"verde": 0, "amarilla": 1, "naranja": 2, "roja": 3}


def _severidad_proporcional(valor: float, umbral: float) -> str:
    """
    Clasifica una condición proporcional (viento/ráfaga/lluvia/oleaje)
    según cuánto supera su umbral base. Asume valor >= umbral (eso ya se
    filtró antes de llegar acá — es la condición de entrada).
    """
    ratio = valor / umbral if umbral else 1.0
    if ratio >= ESCALA_PROPORCIONAL["roja_desde"]:
        return "roja"
    if ratio >= ESCALA_PROPORCIONAL["naranja_desde"]:
        return "naranja"
    return "amarilla"


def _severidad_helada(temp: float, region: str) -> str:
    """Quiebres fijos en °C, distintos por región (no proporcional)."""
    niveles = HELADA_ESCALA.get(region, HELADA_ESCALA["Los Lagos"])
    if temp <= niveles["roja"]:
        return "roja"
    if temp <= niveles["naranja"]:
        return "naranja"
    return "amarilla"


def _color_final(colores_individuales: list[str]) -> str:
    """
    Aplica la regla de combinación por cantidad de condiciones activas:
      - 1 condición    -> su propio color individual
      - 2 condiciones  -> mínimo naranja
      - 3+ condiciones -> mínimo roja
    El resultado es el más alto entre el piso por cantidad y el color
    individual más grave (nunca baja de nivel por combinarse con algo leve).
    """
    if not colores_individuales:
        return "verde"
    cantidad = len(colores_individuales)
    piso_por_cantidad = "verde"
    if cantidad == 2:
        piso_por_cantidad = "naranja"
    elif cantidad >= 3:
        piso_por_cantidad = "roja"
    peor_individual = max(colores_individuales, key=lambda c: PESO_COLOR[c])
    return max([piso_por_cantidad, peor_individual], key=lambda c: PESO_COLOR[c])


def evaluar_umbrales(
    comuna: str,
    datos: dict,
    umbrales: dict,
    region: str,
    usar_pronostico_viento: bool = False,
) -> list[dict]:
    """
    Compara los datos crudos de un punto contra sus umbrales, y calcula la
    severidad final (verde/amarilla/naranja/roja) combinando TODAS las
    condiciones activas de ese punto a la vez. Devuelve una lista de
    alertas propias generadas (puede ser vacía); todas comparten el mismo
    campo "color" (el color final ya combinado, no el individual).

    `region`: necesaria para saber qué quiebres de helada usar (Los
    Lagos/Aysén vs Magallanes).

    `usar_pronostico_viento`: si es True, el viento sostenido y las
    ráfagas se comparan contra el PEOR valor pronosticado hacia adelante
    (`viento_max_prevista_kmh` / `rafagas_max_prevista_kmh`) en vez del
    dato instantáneo del momento — así el reporte por correo (que solo se
    arma unas pocas veces al día) no se pierde un pico de viento que
    ocurrió entre un envío y el siguiente.
    """
    candidatas = []  # [(tipo, mensaje, color_individual, valor, umbral)]

    if usar_pronostico_viento:
        viento = datos.get("viento_max_prevista_kmh")
        rafagas = datos.get("rafagas_max_prevista_kmh")
        etiqueta_viento, etiqueta_rafagas = "Viento sostenido previsto", "Ráfagas de viento previstas"
    else:
        viento = datos.get("viento_kmh")
        rafagas = datos.get("rafagas_kmh")
        etiqueta_viento, etiqueta_rafagas = "Viento sostenido", "Ráfagas de viento"

    if viento is not None and viento >= umbrales["viento_kmh"]:
        color = _severidad_proporcional(viento, umbrales["viento_kmh"])
        candidatas.append((
            "viento", f"{etiqueta_viento} de {viento} km/h en {comuna}.",
            color, viento, umbrales["viento_kmh"],
        ))

    if rafagas is not None and rafagas >= umbrales["rafagas_kmh"]:
        color = _severidad_proporcional(rafagas, umbrales["rafagas_kmh"])
        candidatas.append((
            "rafagas", f"{etiqueta_rafagas} de {rafagas} km/h en {comuna}.",
            color, rafagas, umbrales["rafagas_kmh"],
        ))

    precip = datos.get("precipitacion_24h_mm")
    if precip is not None and precip >= umbrales["precipitacion_24h_mm"]:
        color = _severidad_proporcional(precip, umbrales["precipitacion_24h_mm"])
        candidatas.append((
            "precipitacion", f"Agua caída de {precip} mm en 24h en {comuna}.",
            color, precip, umbrales["precipitacion_24h_mm"],
        ))

    # Helada: mira la mínima pronosticada SOLO en las horas que faltan (no
    # todo el día ya pasado). Así, si el frío ya ocurrió y no se pronostica
    # que continúe, la alerta se cae sola en el próximo ciclo.
    temp = datos.get("temp_min_prevista_c")
    if temp is not None and temp <= umbrales["temp_min_c"]:
        color = _severidad_helada(temp, region)
        candidatas.append((
            "helada", f"Mínima pronosticada de {temp}°C en {comuna}.",
            color, temp, umbrales["temp_min_c"],
        ))

    # Oleaje: usa el máximo del día si está disponible, si no el actual.
    ola = datos.get("altura_ola_max_m")
    if ola is None:
        ola = datos.get("altura_ola_actual_m")
    if ola is not None and ola >= umbrales["altura_ola_m"]:
        color = _severidad_proporcional(ola, umbrales["altura_ola_m"])
        candidatas.append((
            "oleaje", f"Oleaje de {ola} m en {comuna}.",
            color, ola, umbrales["altura_ola_m"],
        ))

    # Tormenta eléctrica: detectada ahora o en las próximas 6 horas (mismo
    # criterio que app.html). Sin valor numérico -> sola siempre es naranja.
    if datos.get("tormenta_proxima"):
        candidatas.append((
            "tormenta", f"Tormenta eléctrica prevista en las próximas 6 horas en {comuna}.",
            "naranja", None, None,
        ))

    if not candidatas:
        return []

    color_final = _color_final([c[2] for c in candidatas])

    return [
        {
            "comuna": comuna,
            "tipo": tipo,
            "nivel": "propia",
            "color": color_final,
            "valor": valor,
            "umbral": umbral,
            "mensaje": mensaje,
        }
        for tipo, mensaje, _color_individual, valor, umbral in candidatas
    ]


def formatear_alertas_oficiales(region: str, alertas_senapred: list[dict]) -> list[dict]:
    """
    Normaliza las alertas oficiales de SENAPRED al mismo formato interno.

    Preserva la comuna real de cada alerta (si SENAPRED la especifica).
    Solo se trata como alerta genuinamente REGIONAL (ej. una ATP declarada
    para toda la región) cuando SENAPRED no especifica una comuna — nunca
    al revés: una alerta con comuna nunca se generaliza a toda la región.
    """
    resultado = []
    for a in alertas_senapred:
        comuna = a.get("comuna")
        es_regional = not comuna
        if comuna:
            mensaje = (f"SENAPRED - {comuna}, {region}: {a.get('tipo')} "
                       f"({a.get('color')}) - {a.get('titulo')}")
        else:
            mensaje = (f"SENAPRED - {region} (alerta regional / ATP): {a.get('tipo')} "
                       f"({a.get('color')}) - {a.get('titulo')}")
        resultado.append({
            "comuna": comuna,
            "region": region,
            "tipo": a.get("tipo", "alerta oficial"),
            "nivel": "oficial",
            "es_regional": es_regional,
            "mensaje": mensaje,
        })
    return resultado
