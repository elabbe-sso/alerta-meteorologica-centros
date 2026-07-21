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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("alertas-meteo-sur")

# Cuántos puntos se consultan EN PARALELO. Antes esto era secuencial (uno
# por uno), y con 68 puntos x hasta 3 fuentes cada uno, superaba fácil los
# 10 minutos de límite de GitHub Actions. En paralelo baja a segundos,
# porque las llamadas son de red (I/O), no de CPU.
MAX_HILOS = 12

# Horas del día (hora de Chile) en las que se ARMA Y ENVÍA el reporte por
# correo. El chequeo de datos (recolectar_alertas) sigue corriendo cada vez
# que el workflow se dispara (ej. cada 30 min, ver .github/workflows/alertas.yml),
# pero el envío del correo solo ocurre en estas horas — así no se manda un
# correo cada 30 min, sino un reporte a horarios fijos.
HORAS_ENVIO = [8, 14, 20]


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


def _evaluar_punto(punto: tuple) -> list[dict]:
    """Procesa UN punto: trae sus datos y los compara contra sus umbrales.
    Se ejecuta en paralelo para los 68 puntos (ver MAX_HILOS)."""
    nombre, lat, lon, comuna_ref, region = punto
    try:
        datos = fetch_datos_consenso(lat, lon)
        umbrales = obtener_umbrales_punto(nombre)
        return evaluar_umbrales(nombre, datos, umbrales)
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

    # --- Umbrales propios por PUNTO ESPECÍFICO (coordenadas exactas) ---
    # Se consultan EN PARALELO (antes era secuencial, uno por uno, lo que
    # con 68 puntos superaba el límite de tiempo del workflow).
    with ThreadPoolExecutor(max_workers=MAX_HILOS) as executor:
        futuros = {executor.submit(_evaluar_punto, p): p[0] for p in PUNTOS_ESPECIFICOS}
        for futuro in as_completed(futuros):
            todas_las_alertas += futuro.result()

    return todas_las_alertas


def es_hora_de_enviar(ahora: datetime) -> bool:
    """
    True solo dentro de los primeros 30 minutos de una hora programada
    (ej. 8:00–8:29). Como el workflow corre cada 30 min, esto asegura que
    el reporte se envíe UNA vez por horario, no dos (una a la hora en
    punto y otra a la media hora).
    """
    return ahora.hour in HORAS_ENVIO and ahora.minute < 30


def notificar(alertas: list[dict]) -> None:
    """
    Arma y envía el REPORTE COMPLETO del estado actual (no solo lo "nuevo"):
    todas las alertas activas ahora mismo, agrupadas por severidad, o el
    mensaje de "sin novedades" si no hay ninguna. Solo se envía si estamos
    dentro de uno de los HORAS_ENVIO — fuera de esos horarios, no se manda
    nada (el chequeo de datos igual corre, solo el envío queda pausado).
    """
    ahora = datetime.now(ZoneInfo("America/Santiago"))

    if not es_hora_de_enviar(ahora):
        log.info(
            "Son las %s (Chile) — fuera de los horarios de envío (%s). No se manda correo.",
            ahora.strftime("%H:%M"), HORAS_ENVIO,
        )
        return

    log.info("Horario de envío (%s). Armando reporte con %d alerta(s) activa(s).",
              ahora.strftime("%H:%M"), len(alertas))

    asunto = generar_asunto(alertas)
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
