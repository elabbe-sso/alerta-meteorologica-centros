"""
Arma el cuerpo del reporte de alertas que se envía en cada horario
programado (ver HORAS_ENVIO en main.py). A diferencia del diseño anterior
(un correo solo cuando había algo "nuevo"), esto genera un REPORTE COMPLETO
del estado actual en cada horario — incluyendo el mensaje de "todo normal"
cuando no hay ninguna alerta activa.
"""

from __future__ import annotations
from datetime import datetime

# A qué color/severidad visual pertenece cada tipo de alerta, para agrupar
# el reporte igual que en app.html / api.py.
SEVERIDAD_TIPO = {
    "tormenta": "roja",
    "viento": "amarilla",
    "rafagas": "amarilla",
    "precipitacion": "amarilla",
    "oleaje": "amarilla",
    "helada": "verde",
}

COLOR_HEX = {"roja": "#e5484d", "amarilla": "#e3b341", "verde": "#3fb950"}
TITULO_SEVERIDAD = {"roja": "Rojas", "amarilla": "Amarillas", "verde": "Informativas"}

MENSAJE_SIN_ALERTAS = (
    "No hay parámetros meteorológicos que superen los umbrales definidos "
    "en los centros de cultivo."
)


def _agrupar_por_severidad(alertas: list[dict]) -> dict[str, list[dict]]:
    grupos: dict[str, list[dict]] = {"roja": [], "amarilla": [], "verde": []}
    for a in alertas:
        color = SEVERIDAD_TIPO.get(a["tipo"], "amarilla")
        grupos[color].append(a)
    return grupos


def generar_asunto(alertas: list[dict]) -> str:
    if not alertas:
        return "Reporte de alertas — sin novedades"
    grupos = _agrupar_por_severidad(alertas)
    if grupos["roja"]:
        return f"Reporte de alertas — {len(grupos['roja'])} roja(s), {len(alertas)} en total"
    return f"Reporte de alertas — {len(alertas)} activa(s)"


def generar_cuerpo_texto(alertas: list[dict], ahora: datetime) -> str:
    encabezado = f"Reporte de alertas — {ahora.strftime('%d-%m-%Y %H:%M')} (hora Chile)\n"
    if not alertas:
        return encabezado + "\n" + MENSAJE_SIN_ALERTAS + "\n"

    grupos = _agrupar_por_severidad(alertas)
    partes = [encabezado]
    for color in ("roja", "amarilla", "verde"):
        if not grupos[color]:
            continue
        partes.append(f"\n--- {TITULO_SEVERIDAD[color]} ({len(grupos[color])}) ---")
        for a in grupos[color]:
            partes.append(f"- {a['mensaje']}")
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
        grupos = _agrupar_por_severidad(alertas)
        secciones = []
        for color in ("roja", "amarilla", "verde"):
            if not grupos[color]:
                continue
            hex_color = COLOR_HEX[color]
            filas = "".join(
                f"""
                <div style="border-left:3px solid {hex_color};background:#fafafa;
                            border-radius:6px;padding:10px 14px;margin-bottom:8px;
                            font-size:14px;color:#27272a;line-height:1.4;">
                  {a['mensaje']}
                </div>"""
                for a in grupos[color]
            )
            secciones.append(f"""
              <div style="margin-bottom:22px;">
                <div style="font-family:monospace;font-size:12px;letter-spacing:.08em;
                            text-transform:uppercase;color:{hex_color};font-weight:700;
                            margin-bottom:8px;">
                  {TITULO_SEVERIDAD[color]} &middot; {len(grupos[color])}
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
          <td style="background:#0b161f;padding:20px 28px;">
            <div style="font-family:Arial,sans-serif;font-size:18px;font-weight:700;color:#ffffff;">
              Centros <span style="color:#4cc9e0;">Cermaq</span>
            </div>
            <div style="font-family:monospace;font-size:12px;color:#7f97a6;margin-top:4px;">
              Reporte de alertas &middot; {fecha} (hora Chile)
            </div>
          </td>
        </tr>
        <tr>
          <td style="padding:24px 28px;">
            {cuerpo_interno}
          </td>
        </tr>
        <tr>
          <td style="padding:16px 28px;border-top:1px solid #e4e4e7;">
            <div style="font-family:monospace;font-size:11px;color:#a1a1aa;line-height:1.6;">
              Datos: Open-Meteo, yr.no, DWD ICON, ECMWF &middot; Umbrales propios de cada centro
            </div>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
