"""
Orquestador principal del sistema de alertas meteorológicas del sur de Chile.

Uso:
    python main.py

Para automatizar, este script está pensado para correr periódicamente
(ver README.md para opciones de scheduling: cron, systemd timer, o
un scheduler tipo APScheduler embebido).
"""

from __future__ import annotations
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

from config import REGIONES, PUNTOS_ESPECIFICOS, obtener_umbrales_punto
from fuentes import fetch_datos_consenso, fetch_alertas_senapred
from reglas import evaluar_umbrales, formatear_alertas_oficiales
from reporte import generar_asunto, generar_cuerpo_texto, generar_cuerpo_html
from notificadores import enviar_email, enviar_whatsapp
from estado import ya_fue_enviada, marcar_enviada

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("alertas-meteo-sur")

# Cuántos puntos se consultan EN PARALELO. Antes esto era secuencial (uno
# por uno), y con 68 puntos x hasta 3 fuentes cada uno, superaba fácil los
# 10 minutos de límite de GitHub Actions. En paralelo baja a segundos,
# porque las llamadas son de red (I/O), no de CPU.
MAX_HILOS = 12

# Horarios del día (hora de Chile) en los que se ARMA Y ENVÍA el reporte por
# correo. Cada uno es (hora, minuto). El chequeo de datos (recolectar_alertas)
# sigue corriendo cada vez que el workflow se dispara (cada 30 min, ver
# .github/workflows/alertas.yml), pero el envío del correo solo ocurre en
# estos horarios — así no se manda un correo cada 30 min, sino un reporte
# a horarios fijos.
HORAS_ENVIO = [(7, 30), (14, 0), (19, 0)]


def _lista_desde_env(var: str, default: list[str]) -> list[str]:
    """
    Lee una lista de valores (correos, números) desde una variable de
    entorno, tolerando cualquier separador razonable: comas, saltos de
    línea, o punto y coma — y cualquier mezcla de ellos. Así, si alguien
    pega los correos uno debajo del otro en vez de separarlos por coma
    en GitHub Secrets, igual funciona (antes esto rompía el envío con un
    error de "folded header contains newline").

    Si la variable no está definida, usa el valor por defecto de abajo.
    """
    valor = os.environ.get(var)
    if valor is None:
        return default
    partes = re.split(r"[,;\n]+", valor)
    return [p.strip() for p in partes if p.strip()]


# Configura aquí el default para correr localmente, o define las variables
# de entorno / secrets DESTINATARIOS_EMAIL y DESTINATARIOS_WHATSAPP
# (separadas por comas) para no hardcodear datos personales en el código.
DESTINATARIOS_EMAIL = _lista_desde_env("DESTINATARIOS_EMAIL", ["destinatario@ejemplo.cl"])
# Números en formato internacional SIN '+', ej. "56912345678".
# En producción, esto vendría de tu base de suscriptores (con opt-in).
DESTINATARIOS_WHATSAPP = _lista_desde_env("DESTINATARIOS_WHATSAPP", [])


def horas_hasta_proximo_envio(ahora: datetime) -> int:
    """
    Cuántas horas faltan (redondeado hacia arriba) hasta el PRÓXIMO horario
    de HORAS_ENVIO, mirando también al día siguiente si ya pasaron todos
    los de hoy. Mínimo 1 hora, para no pedir una ventana de 0 horas justo
    en el instante del envío.
    """
    minutos_ahora = ahora.hour * 60 + ahora.minute
    objetivos_hoy = sorted(h * 60 + m for h, m in HORAS_ENVIO)
    siguiente = next((obj for obj in objetivos_hoy if obj > minutos_ahora), None)
    if siguiente is None:
        siguiente = objetivos_hoy[0] + 24 * 60  # el primero de mañana
    minutos_faltantes = siguiente - minutos_ahora
    return max(1, -(-minutos_faltantes // 60))  # redondeo hacia arriba


def _evaluar_punto(punto: tuple, horas_viento: int) -> list[dict]:
    """Procesa UN punto: trae sus datos y los compara contra sus umbrales.
    Se ejecuta en paralelo para los 68 puntos (ver MAX_HILOS).
    El viento/ráfaga se compara contra el PEOR valor pronosticado hasta el
    próximo envío (`horas_viento`), no el dato del instante — así el
    reporte no se pierde un pico de viento entre un correo y el siguiente.
    """
    nombre, lat, lon, comuna_ref, region = punto
    try:
        datos = fetch_datos_consenso(lat, lon, horas_viento)
        umbrales = obtener_umbrales_punto(nombre)
        return evaluar_umbrales(nombre, datos, umbrales, usar_pronostico_viento=True)
    except Exception:
        log.exception("Error obteniendo datos de Open-Meteo para %s", nombre)
        return []


def recolectar_alertas() -> list[dict]:
    todas_las_alertas = []

    # --- Alertas oficiales de SENAPRED: DESACTIVADAS a propósito ---
    # El sistema solo notifica cuando un punto supera sus propios umbrales
    # (viento, ráfagas, lluvia, helada). Para reactivar SENAPRED,
    # descomenta este bloque.
    #
    # for region in REGIONES:
    #     try:
    #         alertas_oficiales = fetch_alertas_senapred(region)
    #         todas_las_alertas += formatear_alertas_oficiales(region, alertas_oficiales)
    #     except Exception:
    #         log.exception("Error obteniendo alertas SENAPRED para %s", region)

    # Ventana de viento/ráfaga: desde ahora hasta el próximo envío programado.
    ahora = datetime.now(ZoneInfo("America/Santiago"))
    horas_viento = horas_hasta_proximo_envio(ahora)
    log.info("Ventana de viento/ráfaga previstos: próximas %d hora(s) (hasta el siguiente envío).", horas_viento)

    # --- Umbrales propios por PUNTO ESPECÍFICO (coordenadas exactas) ---
    # Se consultan EN PARALELO (antes era secuencial, uno por uno, lo que
    # con 68 puntos superaba el límite de tiempo del workflow).
    with ThreadPoolExecutor(max_workers=MAX_HILOS) as executor:
        futuros = {executor.submit(_evaluar_punto, p, horas_viento): p[0] for p in PUNTOS_ESPECIFICOS}
        for futuro in as_completed(futuros):
            todas_las_alertas += futuro.result()

    return todas_las_alertas


def slot_programado(ahora: datetime) -> tuple[int, int] | None:
    """
    Devuelve el horario de HORAS_ENVIO que coincide con "ahora" (dentro de
    los 15 minutos siguientes a ese horario), o None si no coincide con
    ninguno. Ese margen es porque GitHub Actions no garantiza el minuto
    exacto de un cron — puede atrasarse unos minutos.
    """
    minutos_ahora = ahora.hour * 60 + ahora.minute
    for hora, minuto in HORAS_ENVIO:
        objetivo = hora * 60 + minuto
        if 0 <= (minutos_ahora - objetivo) < 15:
            return (hora, minuto)
    return None


def es_hora_de_enviar(ahora: datetime) -> bool:
    return slot_programado(ahora) is not None


def notificar(alertas: list[dict]) -> None:
    """
    Arma y envía el REPORTE COMPLETO del estado actual (no solo lo "nuevo"):
    todas las alertas activas ahora mismo, agrupadas por severidad, o el
    mensaje de "sin novedades" si no hay ninguna. Solo se envía si estamos
    dentro de uno de los HORAS_ENVIO — fuera de esos horarios, no se manda
    nada (el chequeo de datos igual corre, solo el envío queda pausado).

    Como hay DOS disparadores independientes (el cron interno de GitHub y
    cron-job.org, como respaldo), ambos pueden caer dentro de la misma
    ventana de 15 min de un mismo horario — sin este chequeo, mandarían el
    reporte dos veces. `estado.py` recuerda "ya se envió el reporte de las
    14:00 de hoy" para que el segundo disparador no repita el envío.
    """
    ahora = datetime.now(ZoneInfo("America/Santiago"))
    slot = slot_programado(ahora)

    if slot is None:
        horarios_legibles = ", ".join(f"{h:02d}:{m:02d}" for h, m in HORAS_ENVIO)
        log.info(
            "Son las %s (Chile) — fuera de los horarios de envío (%s). No se manda correo.",
            ahora.strftime("%H:%M"), horarios_legibles,
        )
        return

    clave_envio = f"reporte-{ahora.date().isoformat()}-{slot[0]:02d}{slot[1]:02d}"
    if ya_fue_enviada(clave_envio):
        log.info(
            "El reporte de las %02d:%02d de hoy ya se envió (probablemente por el otro "
            "disparador, GitHub o cron-job.org). No se manda de nuevo.",
            slot[0], slot[1],
        )
        return

    log.info("Horario de envío (%s). Armando reporte con %d alerta(s) activa(s).",
              ahora.strftime("%H:%M"), len(alertas))

    asunto = generar_asunto(alertas, ahora)
    cuerpo_texto = generar_cuerpo_texto(alertas, ahora)
    cuerpo_html = generar_cuerpo_html(alertas, ahora)

    try:
        enviar_email(DESTINATARIOS_EMAIL, asunto=asunto, cuerpo=cuerpo_texto, cuerpo_html=cuerpo_html)

        if DESTINATARIOS_WHATSAPP:
            # WhatsApp usa una plantilla de 3 variables (nivel/zona/descripción),
            # no un reporte largo. Se manda un resumen corto, remitiendo al correo.
            if not alertas:
                resumen = {"color": "verde", "comuna": "Todos los centros", "mensaje": "Sin novedades."}
            elif len(alertas) == 1:
                resumen = alertas[0]
            else:
                resumen = {
                    "color": "amarilla",
                    "comuna": f"{len(alertas)} ubicaciones",
                    "mensaje": "Revisa tu correo o el dashboard para el detalle.",
                }
            enviar_whatsapp(DESTINATARIOS_WHATSAPP, resumen)

        marcar_enviada(clave_envio)
        log.info("Reporte enviado correctamente.")

    except Exception:
        log.exception("Error enviando el reporte de alertas")


def main():
    log.info("Iniciando ciclo de recolección...")
    alertas = recolectar_alertas()
    log.info("Se encontraron %d alertas nuevas o vigentes.", len(alertas))
    notificar(alertas)
    log.info("Ciclo completo.")


if __name__ == "__main__":
    main()
