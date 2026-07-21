# Alertas meteorológicas — sur de Chile (Los Lagos a Magallanes)

Sistema en producción que monitorea 68 centros de cultivo, evalúa umbrales
propios de viento, lluvia, oleaje y helada, y envía alertas por email
cuando corresponde. Incluye además una app web de monitoreo en vivo.

**En producción ahora mismo:**
- Backend (`main.py`) corriendo cada 30 min vía GitHub Actions.
- App de monitoreo (`app.html`) publicada en GitHub Pages:
  `https://elabbe-sso.github.io/alerta-meteorologica-centros/app.html`

## Qué está activo hoy

- **68 puntos monitoreados** (centros de cultivo), cada uno con coordenadas
  exactas y vinculado a una comuna real. No hay comunas "genéricas": todo se
  evalúa punto por punto.
- **Datos crudos**: se combina el **peor caso** entre varios modelos
  meteorológicos, para no perder una alerta si uno solo la subestima:
  - **Open-Meteo** (mezcla "best_match") — sin llave, activo en Python y en `app.html`.
  - **DWD ICON** (modelo alemán explícito, `&models=icon_seamless`) — sin llave, activo en ambos.
  - **ECMWF** (modelo europeo explícito, `&models=ecmwf_ifs025`) — sin llave, activo **solo en `app.html`** (ver nota más abajo sobre por qué no en Python).
  - **Open-Meteo Marine API** (altura de olas/oleaje) — endpoint separado, sin llave, activo en ambos.
- **Alertas por umbral propio** (no por alertas oficiales de ningún organismo — ver más abajo):
  - Viento sostenido ≥ 40 km/h (en el correo: el **peor pronosticado** hasta el próximo envío — ver detalle abajo)
  - Ráfagas ≥ 60 km/h (mismo criterio de pronóstico que el viento)
  - Lluvia ≥ 50 mm/24h
  - Oleaje ≥ 2.0 m
  - Helada: mínima pronosticada ≤ -3°C, mirando **solo las próximas 12 horas hacia adelante** (nunca horas ya pasadas — ver detalle abajo)
  - Tormenta eléctrica: código de clima 95/96/99 detectado ahora o en las próximas 6 horas
- **Notificación por email — reporte en horarios fijos**: el chequeo de datos
  corre cada vez que el workflow se dispara (cada 30 min), pero el correo
  solo se **arma y envía a las 7:30, 14:00 y 19:00** (hora de Chile,
  configurable en `HORAS_ENVIO` de `main.py`). Cada envío es un **reporte
  completo del estado actual** — no solo lo "nuevo" — agrupado por
  severidad (rojas / amarillas / informativas). Si no hay ninguna alerta
  activa, igual se manda un correo confirmando que todo está normal, para
  que el silencio no se confunda con que el sistema dejó de funcionar. El
  correo es HTML (con una versión en texto plano de respaldo automático).
  El remitente puede mostrar un nombre visible (no solo el correo pelado)
  configurando el secret opcional `SMTP_FROM_NAME`.
- **`app.html`**: buscador, ícono de clima, temperatura actual, pronóstico de
  próximas 6h, chips de resumen clicables (filtran por color), enlace
  destacado a "Estados de Puerto" (ver abajo).

## Viento y ráfagas en el correo — pronóstico dinámico, no el dato actual

A diferencia del dashboard en vivo (`api.py`, que sí muestra la condición
del momento), **el correo de reporte compara contra el peor viento/ráfaga
pronosticado desde ahora hasta el próximo envío programado** (ver
`HORAS_ENVIO`), no el dato instantáneo. Así, si a las 7:30 el viento está
tranquilo pero se pronostica fuerte para el mediodía, el reporte de las
7:30 ya avisa — no espera a que el viento realmente suba.

La ventana se calcula sola en cada corrida (`horas_hasta_proximo_envio()`
en `main.py`): por ejemplo, a las 14:05 con horarios 7:30/14:00/19:00, la
ventana es de ~5 horas (hasta las 19:00). Esto usa el mismo mecanismo de
`reglas.py` que la helada (`usar_pronostico_viento=True`), controlado con
un parámetro para no afectar al dashboard en vivo.

## Alerta de helada — "hacia adelante", no hacia atrás

En vez de mirar la temperatura de ahora mismo o el mínimo de todo el día, se
calcula la temperatura **más baja pronosticada entre este momento y las
próximas 12 horas**, mirando siempre hacia adelante:
- Si el frío **ya pasó** y no se pronostica que continúe, la alerta se cae
  sola en el siguiente ciclo.
- Si el frío **viene** más tarde, la alerta se enciende con anticipación.

Misma lógica en `fuentes.py` (`_min_prevista()`) y en `app.html`.

## Otros parámetros que se muestran (informativos, no disparan alerta)

- **Mín / Máx**: pronóstico de temperatura del día completo.
- **Humedad** y **sensación térmica**: dato puntual del momento actual.

Solo viento, ráfaga, lluvia, oleaje, helada y tormenta generan alertas.

## Por qué ECMWF reemplaza a yr.no en `app.html`, no en Python

yr.no (MET Norway) es una fuente real y de buena calidad, pero **no puede
llamarse desde el navegador**: exige un header `User-Agent` propio, y el
`fetch()` de JavaScript no permite que una página modifique ese header
(restricción de seguridad del navegador). Por eso `app.html` usa ECMWF en su
lugar — sin llave, mismo endpoint de Open-Meteo, sin ese problema. En la
práctica, el propio yr.no ya se apoya en datos de ECMWF fuera de la zona
nórdica, así que la calidad es equivalente.

## Estructura de archivos

```
config.py          -> los 68 puntos, sus comunas, y los umbrales
fuentes.py         -> recolectores de datos (Open-Meteo, DWD ICON, yr.no, Marine)
reglas.py          -> motor de reglas (umbrales -> alertas)
notificadores.py   -> envío por email (activo) y WhatsApp (implementado, sin configurar)
estado.py          -> registro en SQLite (ya no usado por main.py, ver nota abajo)
reporte.py         -> arma el correo (texto plano + HTML), agrupado por severidad
main.py            -> orquestador: junta todo, un ciclo por corrida
app.html           -> app de monitoreo en vivo, sin servidor (publicada en GitHub Pages)
dashboard.html + api.py -> dashboard con mapa Windy (no desplegado, ver abajo)
.github/workflows/alertas.yml -> automatiza main.py en GitHub Actions
```

## Cómo correrlo / desplegarlo

```bash
pip install -r requirements.txt

export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USER="tu_correo@gmail.com"
export SMTP_PASS="tu_password_de_aplicacion"
export SMTP_FROM_NAME="Alertas Centros de Cultivo"   # opcional
export DESTINATARIOS_EMAIL="correo1@x.cl,correo2@x.cl"

python main.py
```

**En producción** esto no hace falta correrlo a mano: `.github/workflows/alertas.yml`
ya lo ejecuta automáticamente cada 30 minutos en GitHub Actions, leyendo las
credenciales desde repository secrets (mismo nombre que las variables de
entorno de arriba). Para agregar/cambiar destinatarios: Settings → Secrets
and variables → Actions → editar `DESTINATARIOS_EMAIL` (hay que reescribir
la lista completa, GitHub no muestra el valor anterior).

`app.html` no necesita desplegarse aparte de GitHub Pages — ya está
publicada y se actualiza sola cada vez que se sube un cambio al repositorio.

⚠️ Si cambias tu nombre de usuario de GitHub, el link de GitHub Pages
**cambia y no redirige automáticamente** (a diferencia de los repositorios).
Hay que volver a compartir el link nuevo.

## Ajustar los 68 puntos, comunas y umbrales

Todo vive en `config.py`:
- `PUNTOS_ESPECIFICOS`: lista ordenada alfabéticamente de `(nombre, lat, lon, comuna, región)`. Agregar uno nuevo es una línea.
- `COMUNAS_POR_REGION`: las comunas válidas, agrupadas por región. `validar_comunas_de_puntos()` avisa si algún punto quedó con una comuna que no existe en esta lista.
- `UMBRALES_DEFAULT`: los umbrales globales (arriba). `UMBRALES_POR_COMUNA` permite un override por nombre exacto de punto.

Al editar `config.py`, hay que replicar el mismo cambio en las listas
equivalentes dentro de `app.html` y `dashboard.html` (`PUNTOS_ESPECIFICOS`
o `COBERTURA`, y `COMUNAS_POR_REGION`) para que todo quede sincronizado.

## Lo que existe en el código pero NO está activo

Estas piezas están implementadas y funcionan si se activan, pero
actualmente no influyen en ninguna alerta ni notificación:

- **`estado.py`** (registro SQLite con ventana de 24h): dejó de usarse
  cuando se pasó al modelo de reportes en horarios fijos — ahora cada
  envío programado incluye TODAS las alertas activas en ese momento, no
  solo las "nuevas" desde el último aviso, así que la deduplicación ya no
  aplica. El archivo queda disponible por si en el futuro se quiere volver
  a un modelo de notificación inmediata en vez de reportes por horario.

- **Alertas oficiales de SENAPRED**: el código para consultar sus tres
  capas reales (verde/amarilla/roja) sigue en `fuentes.py` y funciona, pero
  la llamada está **comentada** en `main.py` y `app.html` — a pedido
  explícito, el sistema solo notifica por umbral propio, no por alertas
  oficiales de ningún organismo.
- **yr.no en el backend Python**: el código existe en `fuentes.py`, pero
  requiere editar `YR_USER_AGENT` con el nombre real de tu app y un
  contacto válido antes de que MET Norway responda datos reales — sin ese
  paso, la llamada falla silenciosamente y el consenso sigue funcionando
  igual con las demás fuentes.
- **WhatsApp**: implementado en `notificadores.py` (Meta Cloud API o
  Twilio), pero aún no se configuraron credenciales reales
  (`WA_TOKEN`, `WHATSAPP_PROVIDER`, etc.) — hoy solo se envía por email.
- **`dashboard.html` + `api.py`** (mapa de Windy): el código está
  sincronizado con los 68 puntos actuales y la llave de Windy ya está
  puesta, pero requiere un servidor corriendo 24/7 (no es como `app.html`,
  que no necesita backend) — todavía no se desplegó en ningún lado.
- **Estados de Puerto (DIRECTEMAR)**: no se automatiza porque SITPORT y
  SVIP bloquean explícitamente el acceso automatizado (`robots.txt`). En su
  lugar, `app.html` tiene un botón "⚓ Estados de Puerto" que enlaza
  directamente al sitio oficial para revisarlo manualmente.
