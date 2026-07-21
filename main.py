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
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import REGIONES, PUNTOS_ESPECIFICOS, obtener_umbrales_punto
from fuentes import fetch_datos_consenso, fetch_alertas_senapred
from reglas import evaluar_umbrales, formatear_alertas_oficiales
from estado import ya_fue_enviada, marcar_enviada
from notificadores import enviar_email, enviar_whatsapp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("alertas-meteo-sur")

# Cuántos puntos se consultan EN PARALELO. Antes esto era secuencial (uno
# por uno), y con 68 puntos x hasta 3 fuentes cada uno, superaba fácil los
# 10 minutos de límite de GitHub Actions. En paralelo baja a segundos,
# porque las llamadas son de red (I/O), no de CPU.
MAX_HILOS = 12


def _lista_desde_env(var: str, default: list[str]) -> list[str]:
    """Lee una lista separada por comas desde una variable de entorno
    (ej. DESTINATARIOS_EMAIL="a@x.cl,b@x.cl"). Si no está definida, usa
    el valor por defecto codificado abajo. Así el mismo main.py sirve
    tanto para correr localmente (edita el default) como en GitHub
    Actions (define el secret DESTINATARIOS_EMAIL / DESTINATARIOS_WHATSAPP).
    """
    valor = os.environ.get(var)
    if valor is None:
        return default
    return [x.strip() for x in valor.split(",") if x.strip()]


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


def notificar(alertas: list[dict]) -> None:
    """
    Agrupa TODAS las alertas nuevas de este ciclo en un solo correo
    (evita mandar un email separado por cada una, que con 60+ puntos
    puede significar decenas de correos de golpe en la primera corrida).
    """
    nuevas = []
    for alerta in alertas:
        # Clave única para no repetir la misma alerta en ciclos futuros.
        clave = f"{alerta.get('comuna') or alerta.get('region')}-{alerta['tipo']}-{alerta['nivel']}"
        if ya_fue_enviada(clave):
            continue
        nuevas.append((clave, alerta))

    if not nuevas:
        log.info("Sin alertas nuevas en este ciclo.")
        return

    log.info("Se encontraron %d alertas nuevas. Enviando en un solo correo.", len(nuevas))

    cuerpo = "\n\n".join(f"- {alerta['mensaje']}" for _, alerta in nuevas)
    asunto = f"Alertas meteorológicas — {len(nuevas)} nueva(s)"

    try:
        enviar_email(DESTINATARIOS_EMAIL, asunto=asunto, cuerpo=cuerpo)

        if DESTINATARIOS_WHATSAPP:
            # WhatsApp usa una plantilla de 3 variables (nivel/zona/descripción),
            # no lista libre. Para no mandar una plantilla por cada alerta,
            # se manda UNA sola indicando el total y remitiendo al correo/dashboard.
            if len(nuevas) == 1:
                enviar_whatsapp(DESTINATARIOS_WHATSAPP, nuevas[0][1])
            else:
                resumen = {
                    "color": "amarilla",
                    "comuna": f"{len(nuevas)} ubicaciones",
                    "mensaje": "Revisa tu correo o el dashboard para el detalle.",
                }
                enviar_whatsapp(DESTINATARIOS_WHATSAPP, resumen)

        for clave, _ in nuevas:
            marcar_enviada(clave)

    except Exception:
        log.exception("Error enviando notificaciones (no se marcó ninguna como enviada; se reintentará)")


def main():
    log.info("Iniciando ciclo de recolección...")
    alertas = recolectar_alertas()
    log.info("Se encontraron %d alertas nuevas o vigentes.", len(alertas))
    notificar(alertas)
    log.info("Ciclo completo.")


if __name__ == "__main__":
    main()
