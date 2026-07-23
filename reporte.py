"""
Arma el cuerpo del reporte de alertas que se envía en cada horario
programado (ver HORAS_ENVIO en main.py). A diferencia del diseño anterior
(un correo solo cuando había algo "nuevo"), esto genera un REPORTE COMPLETO
del estado actual en cada horario — incluyendo el mensaje de "todo normal"
cuando no hay ninguna alerta activa.
"""

from __future__ import annotations
from datetime import datetime

COLOR_HEX = {"roja": "#e5484d", "amarilla": "#e3b341", "verde": "#3fb950"}
TITULO_SEVERIDAD = {"roja": "Rojas", "amarilla": "Amarillas", "verde": "Informativas"}

MENSAJE_SIN_ALERTAS = (
    "No hay parámetros meteorológicos que superen los umbrales definidos "
    "en los centros de cultivo."
)

# Link a la app de monitoreo en vivo (app.html en GitHub Pages). Actualiza
# esto si cambias de usuario/repositorio de GitHub.
URL_APP = "https://elabbe-sso.github.io/alerta-meteorologica-centros/app.html"

# A partir de esta cantidad de centros en UNA severidad, se condensa en una
# lista de nombres en vez de una tarjeta de detalle por centro — para que
# el correo no se vuelva larguísimo cuando hay muchas alertas a la vez.
UMBRAL_CONDENSAR = 10


def _extraer_condicion(mensaje: str, comuna: str) -> str:
    """
    Quita el ' en {comuna}.' final del mensaje, dejando solo la condición
    en sí — para poder mostrar varias condiciones del mismo centro juntas
    sin repetir su nombre en cada una.
    """
    sufijo = f" en {comuna}."
    if comuna and mensaje.endswith(sufijo):
        return mensaje[: -len(sufijo)]
    return mensaje.rstrip(".")


# Tipos que cuentan para el "2 o más" (la helada NUNCA cuenta acá).
TIPOS_AMARILLOS = {"viento", "rafagas", "precipitacion", "oleaje", "tormenta"}


def _agrupar_por_centro_y_severidad(alertas: list[dict]) -> dict[str, list[tuple[str, list[str]]]]:
    """
    Agrupa TODAS las alertas por centro y calcula la severidad FINAL de
    cada uno con las mismas reglas que app.html (no por el tipo individual
    de cada alerta por separado):
      - Solo helada -> verde (informativa).
      - 1 sola condición amarilla (viento/ráfaga/lluvia/oleaje/tormenta) -> amarilla.
      - Helada + 1 amarilla -> amarilla (la helada nunca cuenta para el "2 o más").
      - 2+ amarillas juntas (la tormenta cuenta como una más) -> roja.
      - Cualquier condición al 30%+ sobre su umbral, aunque sea única -> roja.
    """
    por_centro: dict[str, list[dict]] = {}
    orden: list[str] = []
    for a in alertas:
        comuna = a.get("comuna") or ""
        if comuna not in por_centro:
            por_centro[comuna] = []
            orden.append(comuna)
        por_centro[comuna].append(a)

    resultado: dict[str, list[tuple[str, list[str]]]] = {"roja": [], "amarilla": [], "verde": []}
    for comuna in orden:
        count_amarillas = 0
        es_roja = False
        condiciones = []
        for a in por_centro[comuna]:
            condiciones.append(_extraer_condicion(a["mensaje"], comuna))
            tipo = a["tipo"]
            if tipo in TIPOS_AMARILLOS:
                count_amarillas += 1
                valor, umbral = a.get("valor"), a.get("umbral")
                if valor is not None and umbral:
                    if valor >= umbral * 1.3:
                        es_roja = True

        if count_amarillas >= 2:
            es_roja = True

        if es_roja:
            color = "roja"
        elif count_amarillas >= 1:
            color = "amarilla"
        else:
            color = "verde"  # solo helada

        resultado[color].append((comuna, condiciones))

    return resultado


def generar_asunto(alertas: list[dict], ahora: datetime) -> str:
    return f"Reporte alertas meteorológicas — {ahora.strftime('%H:%M')} hrs"


def generar_cuerpo_texto(alertas: list[dict], ahora: datetime) -> str:
    encabezado = f"Reporte de alertas — {ahora.strftime('%d-%m-%Y %H:%M')} (hora Chile)\n"
    if not alertas:
        return encabezado + "\n" + MENSAJE_SIN_ALERTAS + "\n"

    grupos = _agrupar_por_centro_y_severidad(alertas)
    partes = [encabezado]
    for color in ("roja", "amarilla", "verde"):
        centros = grupos[color]
        if not centros:
            continue
        partes.append(f"\n--- {TITULO_SEVERIDAD[color]} ({len(centros)}) ---")
        if len(centros) >= UMBRAL_CONDENSAR:
            partes.append(", ".join(comuna for comuna, _ in centros))
        else:
            for comuna, condiciones in centros:
                partes.append(f"- {comuna}: {' · '.join(condiciones)}")
    return "\n".join(partes) + "\n"


def generar_cuerpo_html(alertas: list[dict], ahora: datetime) -> str:
    fecha = ahora.strftime("%d-%m-%Y %H:%M")

    if not alertas:
        cuerpo_interno = f"""
          <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;
                      padding:18px 20px;color:#166534;font-size:15px;line-height:1.5;">
            ✅ {MENSAJE_SIN_ALERTAS}
          </div>"""
    else:
        grupos = _agrupar_por_centro_y_severidad(alertas)
        secciones = []
        for color in ("roja", "amarilla", "verde"):
            centros = grupos[color]
            if not centros:
                continue
            hex_color = COLOR_HEX[color]

            if len(centros) >= UMBRAL_CONDENSAR:
                # Muchos centros: se condensa en una sola lista de nombres,
                # no una tarjeta por cada uno (si no, el correo se hace
                # eterno). El detalle completo queda a un clic en la app.
                nombres = ", ".join(comuna for comuna, _ in centros)
                filas = f"""
                <div style="border-left:3px solid {hex_color};background:#fafafa;
                            border-radius:6px;padding:10px 14px;margin-bottom:8px;
                            font-size:12.5px;color:#3a3a3a;line-height:1.6;">
                  {nombres}
                </div>"""
            else:
                filas = "".join(
                    f"""
                    <div style="border-left:3px solid {hex_color};background:#fafafa;
                                border-radius:6px;padding:10px 14px;margin-bottom:8px;
                                font-size:13.5px;color:#27272a;line-height:1.5;">
                      <strong>{comuna}</strong><br>
                      <span style="font-size:12.5px;color:#3a3a3a;">{' &middot; '.join(condiciones)}</span>
                    </div>"""
                    for comuna, condiciones in centros
                )

            secciones.append(f"""
              <div style="margin-bottom:22px;">
                <div style="font-family:monospace;font-size:12px;letter-spacing:.08em;
                            text-transform:uppercase;color:{hex_color};font-weight:700;
                            margin-bottom:8px;">
                  {TITULO_SEVERIDAD[color]} &middot; {len(centros)}
                </div>
                {filas}
              </div>""")
        cuerpo_interno = "".join(secciones)

    return f"""\
<!DOCTYPE html>
<html lang="es">
<body style="margin:0;padding:0;background:#f4f4f5;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:24px 0;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:12px;overflow:hidden;max-width:600px;width:100%;">
        <tr>
          <td style="background:#0d1420;padding:20px 28px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td>
                  <div style="font-family:Arial,sans-serif;font-size:18px;font-weight:700;color:#ffffff;">
                    Centros <span style="color:#0096a0;">Cermaq</span>
                  </div>
                  <div style="font-family:monospace;font-size:12px;color:#9aa8bd;margin-top:4px;">
                    Reporte de alertas &middot; {fecha} (hora Chile)
                  </div>
                </td>
                <td align="right" valign="top">
                  <a href="{URL_APP}" style="font-family:monospace;font-size:11px;color:#0096a0;
                     background:rgba(0,150,160,.15);border:1px solid rgba(0,150,160,.4);
                     padding:6px 12px;border-radius:20px;text-decoration:none;white-space:nowrap;">
                    Ver app ↗
                  </a>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:24px 28px;">
            {cuerpo_interno}
            <a href="{URL_APP}" style="display:block;text-align:center;background:#00b8c4;
               color:#ffffff;font-family:Arial,sans-serif;font-size:14px;font-weight:700;
               text-decoration:none;padding:12px;border-radius:8px;margin-top:20px;">
              Ver monitoreo en vivo →
            </a>
          </td>
        </tr>
        <tr>
          <td style="padding:16px 28px;border-top:1px solid #d2d2cd;">
            <div style="font-family:monospace;font-size:11px;color:#324664;line-height:1.6;">
              Datos: Open-Meteo, yr.no, DWD ICON, ECMWF &middot; Umbrales propios de cada centro
            </div>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
