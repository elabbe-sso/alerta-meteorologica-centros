"""
Envío de alertas por distintos canales.

Canales:
  - EMAIL      : funcional, vía SMTP estándar (Gmail, Resend, SendGrid SMTP...).
  - WHATSAPP   : dos implementaciones, elige una con WHATSAPP_PROVIDER:
      * "cloud"  -> Meta WhatsApp Cloud API directo (recomendado, más barato).
      * "twilio" -> vía Twilio (más fácil de partir, agrega markup por mensaje).

IMPORTANTE sobre WhatsApp (modelo 2026):
  - Una alerta que TÚ envías (no es respuesta a un mensaje del usuario en las
    últimas 24h) es un mensaje "iniciado por el negocio" y DEBE usar una
    PLANTILLA pre-aprobada por Meta. No se puede mandar texto libre.
  - Crea la plantilla en la categoría UTILITY (utilitaria), no Marketing:
    es mucho más barata y no cae en el tope de ~2 mensajes/día por usuario.
  - El usuario debe haber dado opt-in explícito para recibir estas alertas.
"""

from __future__ import annotations
import os
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr


# ======================================================================
# EMAIL
# ======================================================================
def enviar_email(destinatarios: list[str], asunto: str, cuerpo: str, cuerpo_html: str | None = None) -> None:
    """
    Envía un correo. Si se pasa `cuerpo_html`, el correo se manda como
    multipart/alternative: los clientes que soportan HTML muestran la
    versión bonita, y los que no (o el modo "solo texto"), muestran
    `cuerpo` (texto plano) como respaldo automático.
    """
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]  # usuario de LOGIN del SMTP (autenticación)
    smtp_pass = os.environ["SMTP_PASS"]
    # Dirección real de remitente (el "De" que ve quien recibe el correo).
    # Con Gmail, es la misma que SMTP_USER — pero con proveedores como
    # Brevo, el usuario de login (ej. "b31289001@smtp-brevo.com") y el
    # remitente verificado (ej. "elabbe79@gmail.com") son DISTINTOS, y
    # usar el de login como remitente hace que el envío se rechace. Si no
    # se define SMTP_FROM_EMAIL, se usa SMTP_USER como antes (Gmail).
    smtp_from_email = os.environ.get("SMTP_FROM_EMAIL", "").strip() or smtp_user
    # Nombre visible del remitente (opcional). Si no se define, se usa solo
    # el correo, como antes. Ej: SMTP_FROM_NAME="Alertas Centros de Cultivo"
    smtp_from_name = os.environ.get("SMTP_FROM_NAME", "").strip()
    remitente = formataddr((smtp_from_name, smtp_from_email)) if smtp_from_name else smtp_from_email

    if cuerpo_html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(cuerpo, "plain", "utf-8"))
        msg.attach(MIMEText(cuerpo_html, "html", "utf-8"))
    else:
        msg = MIMEText(cuerpo, "plain", "utf-8")

    msg["Subject"] = asunto
    msg["From"] = remitente
    msg["To"] = ", ".join(destinatarios)

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_from_email, destinatarios, msg.as_string())


# ======================================================================
# WHATSAPP — OPCIÓN 1 (recomendada): Meta Cloud API directo
# ----------------------------------------------------------------------
# Requisitos (todo gratis salvo el costo por mensaje que cobra Meta):
#   1. App en Meta for Developers + WhatsApp Business Account (WABA).
#   2. Un número de WhatsApp verificado -> obtienes su PHONE_NUMBER_ID.
#   3. Un token permanente (System User token del Business Manager).
#   4. Una PLANTILLA aprobada, categoría UTILITY. Ejemplo sugerido:
#        Nombre:   alerta_meteo_sur
#        Idioma:   es
#        Cuerpo:   "Alerta {{1}} para {{2}}. {{3}}. Mantente informado
#                   con las recomendaciones de tu municipalidad."
#      donde {{1}}=nivel (Amarilla/Roja/Preventiva), {{2}}=comuna o región,
#      {{3}}=descripción breve del evento.
#
# Variables de entorno:
#   WA_TOKEN            = token permanente de Meta
#   WA_PHONE_NUMBER_ID  = id del número emisor
#   WA_TEMPLATE_NAME    = nombre de la plantilla (ej. alerta_meteo_sur)
#   WA_TEMPLATE_LANG    = idioma de la plantilla (ej. es)  [opcional, default es]
# ======================================================================
GRAPH_VERSION = "v21.0"


def enviar_whatsapp_cloud(destinatarios: list[str], params_plantilla: list[str]) -> None:
    """
    Envía la plantilla aprobada a cada destinatario, rellenando sus
    variables {{1}}, {{2}}, ... con `params_plantilla` (en orden).
    `destinatarios` en formato internacional sin '+', ej. "56912345678".
    """
    token = os.environ["WA_TOKEN"]
    phone_id = os.environ["WA_PHONE_NUMBER_ID"]
    template = os.environ["WA_TEMPLATE_NAME"]
    lang = os.environ.get("WA_TEMPLATE_LANG", "es")

    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    componentes = [{
        "type": "body",
        "parameters": [{"type": "text", "text": str(p)} for p in params_plantilla],
    }]

    for numero in destinatarios:
        payload = {
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": lang},
                "components": componentes,
            },
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code >= 400:
            raise RuntimeError(f"WhatsApp Cloud API error {resp.status_code}: {resp.text}")


# ======================================================================
# WHATSAPP — OPCIÓN 2 (alternativa): Twilio
# ----------------------------------------------------------------------
#   pip install twilio
#   Variables: TWILIO_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM
#   (número habilitado en Twilio, formato 'whatsapp:+1415...').
# ======================================================================
def enviar_whatsapp_twilio(destinatarios: list[str], mensaje: str) -> None:
    from twilio.rest import Client

    sid = os.environ["TWILIO_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    numero_origen = os.environ["TWILIO_WHATSAPP_FROM"]

    client = Client(sid, token)
    for numero in destinatarios:
        client.messages.create(
            from_=numero_origen,
            to=f"whatsapp:{numero}",
            body=mensaje,
        )


# ======================================================================
# DISPATCHER — el orquestador llama esto y no le importa el proveedor
# ======================================================================
WHATSAPP_PROVIDER = os.environ.get("WHATSAPP_PROVIDER", "cloud")  # "cloud" | "twilio"


def enviar_whatsapp(destinatarios: list[str], alerta: dict) -> None:
    """
    Recibe una alerta normalizada y la despacha por el proveedor configurado.
    - cloud : usa la plantilla con params [nivel, zona, descripción].
    - twilio: envía el mensaje de texto ya armado.
    """
    if not destinatarios:
        return

    nivel = (alerta.get("color") or alerta.get("nivel") or "meteorológica").capitalize()
    zona = alerta.get("comuna") or alerta.get("region") or "tu zona"
    desc = alerta.get("mensaje") or alerta.get("titulo") or "Evento meteorológico"

    if WHATSAPP_PROVIDER == "twilio":
        enviar_whatsapp_twilio(destinatarios, f"Alerta {nivel} — {zona}: {desc}")
    else:
        enviar_whatsapp_cloud(destinatarios, [nivel, zona, desc])
