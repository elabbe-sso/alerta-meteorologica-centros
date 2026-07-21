"""
Motor de reglas: decide si un dato crudo o una alerta oficial debe
transformarse en una notificación para el usuario final.
"""

from __future__ import annotations


def evaluar_umbrales(comuna: str, datos: dict, umbrales: dict, usar_pronostico_viento: bool = False) -> list[dict]:
    """
    Compara los datos crudos de una comuna contra sus umbrales.
    Devuelve una lista de alertas propias generadas (puede ser vacía).

    `usar_pronostico_viento`: si es True, el viento sostenido y las
    ráfagas se comparan contra el PEOR valor pronosticado hacia adelante
    (`viento_max_prevista_kmh` / `rafagas_max_prevista_kmh`) en vez del
    dato instantáneo del momento — así el reporte por correo (que solo se
    arma unas pocas veces al día) no se pierde un pico de viento que
    ocurrió entre un envío y el siguiente. El dashboard en vivo (`api.py`)
    sigue usando el dato actual por defecto (False), porque ahí sí tiene
    sentido mostrar la condición del momento.
    """
    alertas = []

    if usar_pronostico_viento:
        viento = datos.get("viento_max_prevista_kmh")
        rafagas = datos.get("rafagas_max_prevista_kmh")
        etiqueta_viento, etiqueta_rafagas = "Viento sostenido previsto", "Ráfagas de viento previstas"
    else:
        viento = datos.get("viento_kmh")
        rafagas = datos.get("rafagas_kmh")
        etiqueta_viento, etiqueta_rafagas = "Viento sostenido", "Ráfagas de viento"

    if viento is not None and viento >= umbrales["viento_kmh"]:
        alertas.append({
            "comuna": comuna,
            "tipo": "viento",
            "nivel": "propia",
            "mensaje": f"{etiqueta_viento} de {viento} km/h en {comuna} "
                       f"(umbral: {umbrales['viento_kmh']} km/h).",
        })

    if rafagas is not None and rafagas >= umbrales["rafagas_kmh"]:
        alertas.append({
            "comuna": comuna,
            "tipo": "rafagas",
            "nivel": "propia",
            "mensaje": f"{etiqueta_rafagas} de {rafagas} km/h en {comuna} "
                       f"(umbral: {umbrales['rafagas_kmh']} km/h).",
        })

    precip = datos.get("precipitacion_24h_mm")
    if precip is not None and precip >= umbrales["precipitacion_24h_mm"]:
        alertas.append({
            "comuna": comuna,
            "tipo": "precipitacion",
            "nivel": "propia",
            "mensaje": f"Agua caída de {precip} mm en 24h en {comuna} "
                       f"(umbral: {umbrales['precipitacion_24h_mm']} mm).",
        })

    # Helada: mira la mínima pronosticada SOLO en las horas que faltan (no
    # todo el día ya pasado). Así, si el frío ya ocurrió y no se pronostica
    # que continúe, la alerta se cae sola en el próximo ciclo.
    temp = datos.get("temp_min_prevista_c")
    if temp is not None and temp <= umbrales["temp_min_c"]:
        alertas.append({
            "comuna": comuna,
            "tipo": "helada",
            "nivel": "propia",
            "mensaje": f"Mínima pronosticada de {temp}°C en {comuna} "
                       f"(umbral: {umbrales['temp_min_c']}°C).",
        })

    # Oleaje: usa el máximo del día si está disponible, si no el actual.
    ola = datos.get("altura_ola_max_m")
    if ola is None:
        ola = datos.get("altura_ola_actual_m")
    if ola is not None and ola >= umbrales["altura_ola_m"]:
        alertas.append({
            "comuna": comuna,
            "tipo": "oleaje",
            "nivel": "propia",
            "mensaje": f"Oleaje de {ola} m en {comuna} "
                       f"(umbral: {umbrales['altura_ola_m']} m).",
        })

    return alertas


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
